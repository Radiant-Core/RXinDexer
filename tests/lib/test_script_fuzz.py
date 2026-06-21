# Fuzz harness for the block-parser halt invariant.
#
# advance_txs / _backup_txs in the block processor wrap per-output script
# parsing in `except (ScriptError, AssertionError, ValueError, IndexError)`.
# If any parser can raise an exception OUTSIDE that set on a consensus-reachable
# (malformed but in-a-block) scriptPubKey, a single such tx would escape the
# guard and HALT the indexer for everyone at that height. These tests assert the
# parsers only ever raise within the caught set.
#
# Stdlib seeded-random + exhaustive short-script coverage, so this runs in the
# existing `pytest tests/` CI with NO extra dependency. An optional, stronger
# Hypothesis pass runs too when hypothesis is installed (CI installs it).

import random

import pytest

from electrumx.lib.script import Script, ScriptError, OpCodes  # noqa: F401
from electrumx.lib.tx import Deserializer

# The exact exception set advance_txs/_backup_txs catch (block_processor.py).
# MemoryError is deliberately NOT here — it must propagate, never be swallowed.
ALLOWED = (ScriptError, AssertionError, ValueError, IndexError)

# advance_txs gates put_utxo on Script.zero_refs and also runs get_ops /
# get_push_input_refs (refs) and get_stateseperator_index (via codeScriptHash).
SCRIPT_PARSERS = (
    Script.get_ops,
    Script.get_push_input_refs,
    Script.zero_refs,
    Script.get_stateseperator_index,
)

# Bias the generator toward bytes that drive interesting branches: PUSHDATA1/2/4
# length prefixes, the CHECKSIG family (gates zero_refs ref-zeroing),
# OP_STATESEPARATOR, the OP_PUSHINPUTREF family (each consumes 36 inline bytes),
# OP_RETURN, and small direct-push opcodes.
_INTERESTING = bytes((
    0x00, 0x01, 0x02, 0x05, 0x4b,
    0x4c, 0x4d, 0x4e,                    # OP_PUSHDATA1 / 2 / 4
    0x6a,                               # OP_RETURN
    0xac, 0xad, 0xae, 0xaf,             # OP_CHECKSIG / CHECKSIGVERIFY / multisig
    0xba,                              # OP_CHECKDATASIG (does NOT count as checksig)
    0xbd,                              # OP_STATESEPARATOR (189)
    0xd0, 0xd1, 0xd2, 0xd3, 0xd8,       # OP_PUSHINPUTREF family (consume 36)
))


def _assert_only_allowed(fn, data):
    try:
        fn(data)
    except ALLOWED:
        pass
    except Exception as exc:  # noqa: BLE001 — the precise failure we guard against
        pytest.fail(
            f"{fn.__qualname__} raised uncaught {type(exc).__name__} on "
            f"{data!r}: {exc}"
        )


def _for_all_parsers(data):
    for fn in SCRIPT_PARSERS:
        _assert_only_allowed(fn, data)


def _random_script(rng, max_len=72):
    out = bytearray()
    for _ in range(rng.randint(0, max_len)):
        out.append(rng.choice(_INTERESTING) if rng.random() < 0.55
                   else rng.randint(0, 255))
    return bytes(out)


def test_fuzz_script_parsers_only_raise_allowed():
    rng = random.Random(0xC0FFEE)  # fixed seed -> deterministic CI runs
    for _ in range(100_000):
        _for_all_parsers(_random_script(rng))


def test_exhaustive_short_scripts_only_raise_allowed():
    # Total coverage of every 0-, 1- and 2-byte script (65,793 inputs). Cheap and
    # deterministic; pins down the truncated-prefix edge cases exactly.
    _for_all_parsers(b"")
    for a in range(256):
        _for_all_parsers(bytes((a,)))
        for b in range(256):
            _for_all_parsers(bytes((a, b)))


def test_fuzz_tx_deserializer_only_raises_allowed():
    rng = random.Random(0xBEEF)
    for _ in range(50_000):
        data = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 96)))
        try:
            Deserializer(data).read_tx()
        except ALLOWED:
            pass
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"Deserializer.read_tx raised uncaught {type(exc).__name__} on "
                f"{data!r}: {exc}"
            )


def test_hypothesis_script_parsers_only_raise_allowed():
    # Stronger property-based pass; skipped when hypothesis isn't installed so the
    # stdlib coverage above is never blocked.
    pytest.importorskip("hypothesis")
    from hypothesis import given, settings, strategies as st

    @given(st.binary(max_size=256))
    @settings(max_examples=3000, deadline=None)
    def _run(data):
        _for_all_parsers(data)
        try:
            Deserializer(data).read_tx()
        except ALLOWED:
            pass
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"read_tx uncaught {type(exc).__name__} on {data!r}: {exc}")

    _run()
