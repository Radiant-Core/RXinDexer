"""Regression tests for the glyph_get_token / glyph_get_by_ref fixes.

These exercise the logic that changed in glyph_api.py without needing a live
indexer or daemon:

  * glyph_get_token now searches the named output, then any output, then any
    input scriptSig (where v1 / v2 Style B reveals carry their envelope), and
    reports `envelope_source`. `value` reflects the *queried* outpoint, not the
    output the envelope happened to live in.
  * glyph_get_by_ref now uses glyph_index.get_token() + _token_to_dict()
    (the old self.db.get_utxos_by_ref did not exist -> AttributeError/-32603).
  * to_jsonsafe() hex-encodes raw bytes / CBORTag so records survive JSON.
"""

import asyncio

import pytest

import electrumx.server.glyph_api as glyph_api
from electrumx.server.glyph_api import GlyphAPIMixin
from electrumx.lib.glyph import to_jsonsafe


TXID = "aa" * 32  # 64 hex chars; parse_glyph_id just splits on ':'


def run(coro):
    return asyncio.run(coro)


class FakeSession(GlyphAPIMixin):
    """Minimal stand-in exposing only what the two methods touch."""

    def __init__(self, raw_tx=None, glyph_index=None):
        self._raw_tx = raw_tx
        self.glyph_index = glyph_index

    def bump_cost(self, _):
        pass

    async def daemon_request(self, method, *args):
        assert method == "getrawtransaction"
        return self._raw_tx


def _out(script_hex, value):
    return {"scriptPubKey": {"hex": script_hex}, "value": value}


def _vin(script_hex):
    return {"scriptSig": {"hex": script_hex}}


@pytest.fixture
def patch_envelope(monkeypatch):
    """Map specific script-hex strings to fake envelopes."""

    def _install(mapping):
        def fake_parse(data):
            return mapping.get(data.hex())
        monkeypatch.setattr(glyph_api, "parse_glyph_envelope", fake_parse)

    return _install


# --------------------------------------------------------------------------
# to_jsonsafe
# --------------------------------------------------------------------------

class _FakeCBORTag:
    """Mimics cbor2.CBORTag for the duck-typed branch in to_jsonsafe."""
    def __init__(self, tag, value):
        self.tag = tag
        self.value = value


_FakeCBORTag.__name__ = "CBORTag"  # to_jsonsafe matches on class name


def test_to_jsonsafe_encodes_bytes_and_recurses():
    obj = {
        "name": "tok",
        "ref": b"\xde\xad\xbe\xef",
        "nested": {"hash": b"\x01\x02"},
        "list": [b"\xaa", 1, "x"],
        "scalar": 42,
        "tag": _FakeCBORTag(64, b"\xff\xee"),
    }
    out = to_jsonsafe(obj)
    assert out["ref"] == "deadbeef"
    assert out["nested"]["hash"] == "0102"
    assert out["list"] == ["aa", 1, "x"]
    assert out["scalar"] == 42
    assert out["tag"] == "ffee"  # CBORTag unwrapped then hex-encoded
    # Whole structure must now be JSON-serialisable.
    import json
    json.dumps(out)


def _assert_no_bytes(obj):
    if isinstance(obj, (bytes, bytearray)):
        raise AssertionError(f"raw bytes leaked: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_bytes(k)
            _assert_no_bytes(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _assert_no_bytes(v)


# --------------------------------------------------------------------------
# glyph_get_token — envelope location, envelope_source, value
# --------------------------------------------------------------------------

def test_get_token_envelope_in_named_output(patch_envelope):
    patch_envelope({"e0": {"version": 2, "is_reveal": False}})
    raw_tx = {"vout": [_out("e0", 1.5), _out("ff", 2.0)], "vin": [_vin("aa")]}
    res = run(FakeSession(raw_tx).glyph_get_token(f"{TXID}:0"))
    assert res["envelope_source"] == "output:0"
    assert res["value"] == 150_000_000
    assert res["is_reveal"] is False


def test_get_token_envelope_in_other_output_value_tracks_queried_outpoint(patch_envelope):
    # Envelope lives in output 2, but the caller asked about outpoint :0.
    patch_envelope({"e2": {"version": 2, "is_reveal": False}})
    raw_tx = {
        "vout": [_out("00", 3.0), _out("11", 0.0), _out("e2", 5.0)],
        "vin": [_vin("zz")],
    }
    res = run(FakeSession(raw_tx).glyph_get_token(f"{TXID}:0"))
    assert res["envelope_source"] == "output:2"
    # value is the *queried* outpoint (:0 -> 3.0 RXD), NOT output 2's 5.0 RXD.
    assert res["value"] == 300_000_000


def test_get_token_envelope_in_input_scriptsig(patch_envelope):
    # The core fix: reveal envelope in vin[1].scriptSig, queried outpoint :0
    # carries the value. Previously value was a misleading 0 here.
    patch_envelope({"e1": {"version": 1, "is_reveal": True}})
    raw_tx = {
        "vout": [_out("nope", 4.0)],
        "vin": [_vin("xx"), _vin("e1")],
    }
    res = run(FakeSession(raw_tx).glyph_get_token(f"{TXID}:0"))
    assert res["envelope_source"] == "input:1"
    assert res["value"] == 400_000_000  # not 0


def test_get_token_no_envelope_returns_none(patch_envelope):
    patch_envelope({})  # nothing parses
    raw_tx = {"vout": [_out("aa", 1.0)], "vin": [_vin("bb")]}
    assert run(FakeSession(raw_tx).glyph_get_token(f"{TXID}:0")) is None


def test_get_token_missing_tx_returns_none(patch_envelope):
    patch_envelope({"e0": {"version": 2, "is_reveal": False}})
    assert run(FakeSession(None).glyph_get_token(f"{TXID}:0")) is None


def test_get_token_reveal_metadata_is_json_safe(patch_envelope, monkeypatch):
    patch_envelope({"e0": {"version": 2, "is_reveal": True, "metadata_bytes": b"x"}})
    # Force decoded metadata to contain raw bytes (binary NFT attr).
    monkeypatch.setattr(glyph_api, "parse_glyph_metadata",
                        lambda env: {"name": "Tok", "p": [2], "attrs": {"icon": b"\xde\xad"}})
    raw_tx = {"vout": [_out("e0", 1.0)], "vin": []}
    res = run(FakeSession(raw_tx).glyph_get_token(f"{TXID}:0"))
    assert res["is_reveal"] is True
    assert "metadata" in res and "token_type" in res
    # The whole result must be free of raw bytes after to_jsonsafe.
    _assert_no_bytes(res)
    assert res["metadata"]["attrs"]["icon"] == "dead"


# --------------------------------------------------------------------------
# glyph_get_by_ref
# --------------------------------------------------------------------------

class FakeGlyphIndex:
    def __init__(self, token, token_dict):
        self._token = token
        self._token_dict = token_dict

    def get_token(self, ref_bytes):
        return self._token

    def _token_to_dict(self, token):
        return self._token_dict


def test_get_by_ref_bad_length():
    res = run(FakeSession().glyph_get_by_ref("ab"))
    assert "error" in res


def test_get_by_ref_bad_hex():
    res = run(FakeSession().glyph_get_by_ref("zz" * 36))  # 72 chars, not hex
    assert res == {"error": "Invalid hex in ref"}


def test_get_by_ref_indexing_disabled():
    res = run(FakeSession(glyph_index=None).glyph_get_by_ref("00" * 36))
    assert res == {"error": "Glyph indexing not enabled"}


def test_get_by_ref_not_found():
    idx = FakeGlyphIndex(token=None, token_dict=None)
    assert run(FakeSession(glyph_index=idx).glyph_get_by_ref("00" * 36)) is None


def test_get_by_ref_found_is_json_safe():
    # _token_to_dict may pass through raw bytes (e.g. container_ref) -> hex.
    idx = FakeGlyphIndex(
        token=object(),
        token_dict={"ref": "aa..._0", "container_ref": b"\x01\x02", "name": "T"},
    )
    res = run(FakeSession(glyph_index=idx).glyph_get_by_ref("00" * 36))
    _assert_no_bytes(res)
    assert res["container_ref"] == "0102"
