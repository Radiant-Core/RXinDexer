import pytest
import os

from electrumx.server.storage import Storage, db_class
from electrumx.lib.util import subclasses

# Find out which db engines to test
# Those that are not installed will be skipped
db_engines = []
for klass in subclasses(Storage):
    try:
        klass.import_module()
    except ImportError:
        db_engines.append("skip")
    else:
        db_engines.append(klass.__name__)


@pytest.fixture(params=db_engines)
def db(tmpdir, request):
    cwd = os.getcwd()
    os.chdir(str(tmpdir))
    if request.param == 'skip':
        raise pytest.skip()
    db = db_class(request.param)("db", False)
    yield db
    os.chdir(cwd)
    db.close()


def test_put_get(db):
    db.put(b"x", b"y")
    assert db.get(b"x") == b"y"


def test_batch(db):
    db.put(b"a", b"1")
    with db.write_batch() as b:
        b.put(b"a", b"2")
        assert db.get(b"a") == b"1"
    assert db.get(b"a") == b"2"


def test_iterator(db):
    """
    The iterator should contain all key/value pairs starting with prefix
    ordered by key.
    """
    for i in range(5):
        db.put(b"abc" + str.encode(str(i)), str.encode(str(i)))
    db.put(b"abc", b"")
    db.put(b"a", b"xyz")
    db.put(b"abd", b"x")
    assert list(db.iterator(prefix=b"abc")) == [(b"abc", b"")] + [
            (b"abc" + str.encode(str(i)), str.encode(str(i))) for
            i in range(5)
        ]


def test_iterator_reverse(db):
    for i in range(5):
        db.put(b"abc" + str.encode(str(i)), str.encode(str(i)))
    db.put(b"a", b"xyz")
    db.put(b"abd", b"x")
    assert list(db.iterator(prefix=b"abc", reverse=True)) == [
            (b"abc" + str.encode(str(i)), str.encode(str(i))) for
            i in reversed(range(5))
        ]


def test_close(db):
    db.put(b"a", b"b")
    db.close()
    db = db_class(db.__class__.__name__)("db", False)
    assert db.get(b"a") == b"b"


# ---------------------------------------------------------------------------
# Cursor-seek semantics (R16, docs/pagination-cursors.md)
#
# The cursor-pagination layer hands the engine a ``seek`` key — the next
# key to *serve*.  Ascending scans resume at the first key >= seek;
# descending scans at the last key <= seek.  Regression for the RocksDB
# engine silently ignoring ``seek`` on reverse scans, which made page 2
# of a reverse feed re-serve page 1.
# ---------------------------------------------------------------------------

def paginate_reverse(db, prefix, page_size):
    """Walk a reverse feed page by page exactly the way the cursor layer
    does (see swap_index.get_swap_history): when a page is full, the key
    just read but not served becomes the cursor for the next call."""
    pages = []
    seek = None
    for _ in range(50):
        kwargs = {"prefix": prefix, "reverse": True}
        if seek is not None:
            kwargs["seek"] = seek
        page = []
        seek = None
        for key, _value in db.iterator(**kwargs):
            if len(page) >= page_size:
                seek = key
                break
            page.append(key)
        pages.append(page)
        if seek is None:
            return pages
    raise AssertionError("pagination did not terminate")


def test_reverse_cursor_pagination_no_dupes_no_gaps(db):
    keys = [b"swh" + bytes([i]) for i in range(10)]
    for k in keys:
        db.put(k, b"v" + k[-1:])
    db.put(b"swg", b"below-prefix")
    db.put(b"swi", b"above-prefix")

    pages = paginate_reverse(db, b"swh", page_size=3)

    assert len(pages) >= 2
    seen = [k for page in pages for k in page]
    assert len(seen) == len(set(seen)), "cursor page re-served an entry"
    assert seen == sorted(keys, reverse=True)


def test_reverse_cursor_pagination_prefix_is_last_range(db):
    """The prefix range being the lexicographically last range in the DB
    exercises the trickiest RocksDB positioning (nothing to Seek() to
    above the range)."""
    keys = [b"\xff\xfe" + bytes([i]) for i in range(7)]
    for k in keys:
        db.put(k, b"x")
    db.put(b"\x01", b"far-below")

    pages = paginate_reverse(db, b"\xff\xfe", page_size=2)

    assert len(pages) >= 2
    seen = [k for page in pages for k in page]
    assert seen == sorted(keys, reverse=True)


def test_reverse_seek_positioning(db):
    keys = [b"P" + bytes([i]) for i in (0, 2, 4, 6, 8)]
    for k in keys:
        db.put(k, b"x")
    db.put(b"O~", b"below")
    db.put(b"Q0", b"above")

    def walk(seek):
        return [k for k, _ in db.iterator(prefix=b"P", reverse=True,
                                          seek=seek)]

    # Cursor on an existing key: that key is served first (it is the
    # next-unread key, not the last-served one).
    assert walk(b"P\x04") == [b"P\x04", b"P\x02", b"P\x00"]
    # Cursor between keys: resume at the largest key below it.
    assert walk(b"P\x05") == [b"P\x04", b"P\x02", b"P\x00"]
    # Cursor below the whole prefix range: nothing left to serve... the
    # engines treat an out-of-range cursor as malformed and restart the
    # walk rather than guessing (same degradation as a bad cursor).
    assert walk(b"A") == [k for k in reversed(keys)]
    # Cursor at/above the prefix's upper bound: full walk (no-seek
    # equivalent), never an out-of-prefix key.
    assert walk(b"Q") == [k for k in reversed(keys)]
    assert walk(b"Q9") == [k for k in reversed(keys)]


def test_forward_seek_positioning(db):
    keys = [b"P" + bytes([i]) for i in (0, 2, 4, 6, 8)]
    for k in keys:
        db.put(k, b"x")
    db.put(b"O~", b"below")
    db.put(b"Q0", b"above")

    def walk(seek):
        return [k for k, _ in db.iterator(prefix=b"P", seek=seek)]

    # Cursor on an existing key: served first.
    assert walk(b"P\x04") == [b"P\x04", b"P\x06", b"P\x08"]
    # Cursor between keys: resume at the next key above it.
    assert walk(b"P\x05") == [b"P\x06", b"P\x08"]
    # Cursor beyond the prefix range: nothing to serve.
    assert walk(b"Q") == []
    # Cursor below the prefix: clamped to a full walk.
    assert walk(b"A") == keys


def test_reverse_seek_insertion_stability(db):
    """Rows landing mid-pagination must be served exactly once if they
    sort below the cursor (still ahead of the walk) and not at all if
    they sort above it (already passed) — never duplicated."""
    for i in (1, 3, 5, 7):
        db.put(b"H" + bytes([i]), b"x")

    it = db.iterator(prefix=b"H", reverse=True)
    page1 = []
    seek = None
    for key, _ in it:
        if len(page1) >= 2:
            seek = key
            break
        page1.append(key)
    assert page1 == [b"H\x07", b"H\x05"]
    assert seek == b"H\x03"

    db.put(b"H\x06", b"landed-above-cursor")   # already passed by page 1
    db.put(b"H\x02", b"landed-below-cursor")   # still ahead of the walk

    rest = [k for k, _ in db.iterator(prefix=b"H", reverse=True, seek=seek)]
    assert rest == [b"H\x03", b"H\x02", b"H\x01"]

    seen = page1 + rest
    assert len(seen) == len(set(seen)), "entry served twice"
    assert b"H\x06" not in seen  # above-cursor row deferred to a fresh walk


def test_iterator_include_value_false(db):
    """plyvel yields bare keys with include_value=False; the RocksDB
    engine must match (get_swap_count relies on this not raising)."""
    for i in range(4):
        db.put(b"K" + bytes([i]), b"v")
    db.put(b"L0", b"other")

    keys = list(db.iterator(prefix=b"K", include_value=False))
    assert keys == [b"K" + bytes([i]) for i in range(4)]

    keys_rev = list(db.iterator(prefix=b"K", reverse=True,
                                include_value=False))
    assert keys_rev == list(reversed(keys))


def test_swap_history_cursor_walk_real_engine(db):
    """End-to-end regression for swap.get_history(_use_cursor=True) on a
    real storage engine: a full cursor walk of the newest-first feed must
    serve every row exactly once across 2+ pages."""
    cbor2 = pytest.importorskip("cbor2")
    import struct
    from unittest.mock import MagicMock

    from electrumx.server.swap_index import SwapDBKeys, SwapIndex

    wrapper = MagicMock()
    wrapper.db_height = 0
    wrapper.utxo_db = db
    env = MagicMock()
    env.swap_index = True
    env.reorg_limit = 0
    idx = SwapIndex(wrapper, env)

    base_ref = b"\x42" * 36
    heights = list(range(100, 111))
    for h in heights:
        key = (SwapDBKeys.HISTORY + base_ref
               + struct.pack(">I", h) + struct.pack(">H", 0))
        db.put(key, cbor2.dumps({"height": h}))

    seen = []
    cursor = None
    pages = 0
    while True:
        r = idx.get_swap_history(base_ref, limit=4, cursor=cursor,
                                 _use_cursor=True)
        pages += 1
        assert pages < 20, "pagination did not terminate"
        seen.extend(e["height"] for e in r["entries"])
        if not r["has_more"]:
            assert r["next_cursor"] is None
            break
        cursor = r["next_cursor"]

    assert pages >= 2
    assert seen == sorted(heights, reverse=True)
    assert len(seen) == len(set(seen)), "page 2 re-served page 1 rows"
