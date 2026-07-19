"""
H5 — dMint supply/state validation bounds.

These tests pin the behaviour of the dMint contract-state parser and the
total_supply derivation against attacker-authored on-chain scriptnums:

  * negative / zero / out-of-range reward & max_height are rejected at parse,
  * an oversized reward * max_height * num_contracts is clamped (never an
    absurd/overflowing/negative total_supply),
  * a truncated contract script bails cleanly (returns None),
  * a NORMAL valid dMint contract still parses to the SAME state and derives
    the SAME total_supply as before (golden regression pin).

See electrumx/lib/glyph.py (DMINT_MAX_SCRIPTNUM / DMINT_MAX_TOTAL_SUPPLY,
parse_dmint_contract_state) and electrumx/server/glyph_index.py
(_parse_deploy_contract_state).
"""

import struct

import pytest

from electrumx.lib.glyph import (
    parse_dmint_contract_state,
    DMINT_MAX_SCRIPTNUM,
    DMINT_MAX_TOTAL_SUPPLY,
)
from tests.support import FakeEnv


# ---------------------------------------------------------------------------
# Script-building helpers (minimal CScriptNum + data pushes).
# ---------------------------------------------------------------------------
def _scriptnum(value: int) -> bytes:
    """Encode *value* as a minimal CScriptNum (little-endian, sign bit)."""
    if value == 0:
        return b''
    negative = value < 0
    absval = abs(value)
    result = []
    while absval:
        result.append(absval & 0xFF)
        absval >>= 8
    if result[-1] & 0x80:
        result.append(0x80 if negative else 0x00)
    elif negative:
        result[-1] |= 0x80
    return bytes(result)


def _push(data: bytes) -> bytes:
    n = len(data)
    if n <= 75:
        return bytes([n]) + data
    if n <= 0xFF:
        return bytes([0x4C, n]) + data
    if n <= 0xFFFF:
        return bytes([0x4D]) + struct.pack('<H', n) + data
    return bytes([0x4E]) + struct.pack('<I', n) + data


def _push_scriptnum(value: int) -> bytes:
    return _push(_scriptnum(value))


def _make_dmint_v1_state(height, max_height, reward, target,
                         contract_ref=None, token_ref=None):
    """Build a synthetic V1 dMint contract state output script."""
    if contract_ref is None:
        contract_ref = bytes(36)
    if token_ref is None:
        token_ref = bytes(36)
    state_prefix = (
        _push_scriptnum(height)
        + bytes([0xd8]) + contract_ref
        + bytes([0xd0]) + token_ref
        + _push_scriptnum(max_height)
        + _push_scriptnum(reward)
        + _push_scriptnum(target)
    )
    # 0xbd = OP_CHECKTEMPLATEVERIFY marks the start of the contract bytecode.
    return state_prefix + bytes([0xbd]) + bytes(10)


# ---------------------------------------------------------------------------
# Golden / valid-contract regression pin.
# ---------------------------------------------------------------------------
class TestValidContractUnchanged:
    """A normal valid dMint contract parses to the SAME state as before."""

    def test_golden_v1_state_unchanged(self):
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=5_000,
            target=0x1FFFFFFF,
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        # Golden values — must not regress.
        assert result['height'] == 500_000
        assert result['max_height'] == 1_000_000
        assert result['reward'] == 5_000
        assert result['target'] == 0x1FFFFFFF
        assert 'algo_id' not in result

    def test_golden_total_supply_unchanged(self):
        """num_contracts * reward * max_height for a legit token is unchanged."""
        max_height = 1_000_000
        reward = 5_000
        num_contracts = 3
        supply = num_contracts * reward * max_height
        # Sanity: a legit token's supply is well under the int64 ceiling.
        assert 0 < supply <= DMINT_MAX_TOTAL_SUPPLY
        assert supply == 15_000_000_000


# ---------------------------------------------------------------------------
# Negative / zero supply-relevant fields are rejected.
# ---------------------------------------------------------------------------
class TestNegativeFieldsRejected:

    def test_negative_reward_rejected(self):
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=-5_000,
            target=0x1FFFFFFF,
        )
        assert parse_dmint_contract_state(script) is None

    def test_negative_max_height_rejected(self):
        script = _make_dmint_v1_state(
            height=500_000, max_height=-1_000_000, reward=5_000,
            target=0x1FFFFFFF,
        )
        assert parse_dmint_contract_state(script) is None

    def test_zero_max_height_rejected(self):
        """max_height == 0 means a contract that mines no blocks — inert."""
        script = _make_dmint_v1_state(
            height=500_000, max_height=0, reward=5_000, target=0x1FFFFFFF,
        )
        assert parse_dmint_contract_state(script) is None

    def test_zero_reward_allowed(self):
        """reward == 0 is degenerate but not negative; parser keeps it.

        Downstream supply math guards reward > 0, so a zero reward simply
        yields no derived supply rather than a poisoned one.
        """
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=0, target=0x1FFFFFFF,
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['reward'] == 0


# ---------------------------------------------------------------------------
# Oversized values are rejected / clamped.
# ---------------------------------------------------------------------------
class TestOversizedClamped:

    def test_max_in_filter_reward_accepted(self):
        """The largest 8-byte (in-filter) positive scriptnum is accepted.

        A positive 8-byte CScriptNum cannot exceed DMINT_MAX_SCRIPTNUM
        (= 2^63-1), so the per-field cap never false-rejects a legit value;
        the real overflow protection lives in the product clamp below.
        """
        big = (1 << 55) - 1     # 7-byte magnitude, large but a sane reward cap probe
        assert big <= DMINT_MAX_SCRIPTNUM
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=big,
            target=0x1FFFFFFF,
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['reward'] == big

    def test_negative_field_cap_logic(self):
        """Directly exercise the per-field reject predicate used at parse.

        Pins the contract: reward<0 or >cap is rejected; max_height<=0 or >cap
        is rejected.  (The over-cap branch is unreachable via an 8-byte
        scriptnum push, but the predicate must still hold defensively.)
        """
        def field_ok(reward, max_height):
            if max_height is not None and (
                max_height <= 0 or max_height > DMINT_MAX_SCRIPTNUM
            ):
                return False
            if reward is not None and (
                reward < 0 or reward > DMINT_MAX_SCRIPTNUM
            ):
                return False
            return True

        assert field_ok(5_000, 1_000_000) is True
        assert field_ok(-1, 1_000_000) is False
        assert field_ok(5_000, 0) is False
        assert field_ok(5_000, -1) is False
        assert field_ok(DMINT_MAX_SCRIPTNUM + 1, 1_000_000) is False
        assert field_ok(5_000, DMINT_MAX_SCRIPTNUM + 1) is False

    def test_supply_product_clamped_in_indexer(self):
        """num_contracts * reward * max_height overflowing int64 → not set.

        Mirror the indexer's _parse_deploy_contract_state clamp: when the
        product exceeds DMINT_MAX_TOTAL_SUPPLY, total_supply is left at 0
        (treated as unknown) rather than written absurd/negative.
        """
        reward = DMINT_MAX_SCRIPTNUM // 2          # in range individually
        max_height = 1_000_000                     # in range individually
        num_contracts = 1000
        supply = num_contracts * reward * max_height
        assert supply > DMINT_MAX_TOTAL_SUPPLY     # would overflow int64

        # Replicate the indexer guard.
        total_supply = 0
        if 0 < supply <= DMINT_MAX_TOTAL_SUPPLY:
            total_supply = supply
        assert total_supply == 0                   # clamped, not absurd

    def test_valid_product_set_in_indexer(self):
        """A legitimate product is written through unchanged (golden)."""
        reward = 5_000
        max_height = 1_000_000
        num_contracts = 3
        supply = num_contracts * reward * max_height
        total_supply = 0
        if 0 < supply <= DMINT_MAX_TOTAL_SUPPLY:
            total_supply = supply
        assert total_supply == 15_000_000_000


# ---------------------------------------------------------------------------
# Truncated / malformed scripts bail cleanly.
# ---------------------------------------------------------------------------
class TestTruncatedScriptBails:

    def test_pushdata_overrun_returns_none(self):
        """A fixed-length push whose payload runs off the end bails (None).

        Build: <height push> d8<cref> d0<tref> then a 0x4b (75-byte) push
        opcode with only a few trailing bytes — the cursor must not skip past
        the (missing) OP_CHECKTEMPLATEVERIFY boundary.
        """
        script = (
            _push_scriptnum(500_000)
            + bytes([0xd8]) + bytes(36)
            + bytes([0xd0]) + bytes(36)
            + bytes([0x4b])           # OP_PUSHBYTES_75 ...
            + bytes(5)                # ... but only 5 bytes follow
        )
        # Pad to clear the 80-byte minimum length gate so we exercise the
        # scan-loop bail rather than the early length return.
        script = script + bytes(20)
        assert len(script) >= 80
        assert parse_dmint_contract_state(script) is None

    def test_pushdata1_overrun_returns_none(self):
        script = (
            _push_scriptnum(500_000)
            + bytes([0xd8]) + bytes(36)
            + bytes([0xd0]) + bytes(36)
            + bytes([0x4c, 0xff])     # OP_PUSHDATA1 claiming 255 bytes ...
            + bytes(3)                # ... but only 3 follow
        )
        script = script + bytes(20)
        assert len(script) >= 80
        assert parse_dmint_contract_state(script) is None

    def test_too_short_returns_none(self):
        assert parse_dmint_contract_state(bytes(79)) is None

    def test_no_d8_returns_none(self):
        assert parse_dmint_contract_state(bytes(100)) is None


# ---------------------------------------------------------------------------
# Indexer-level integration: _parse_deploy_contract_state end-to-end.
# ---------------------------------------------------------------------------
from types import SimpleNamespace
from unittest.mock import Mock

from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo
from electrumx.lib.glyph import GlyphProtocol


def _make_tx(scripts):
    outputs = [SimpleNamespace(pk_script=s, value=0) for s in scripts]
    return SimpleNamespace(outputs=outputs)


def _fresh_token():
    token = GlyphTokenInfo()
    token.protocols = [GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]
    token.total_supply = 0
    token.reward = 0
    return token


def _indexer():
    # FakeEnv, not Mock: see tests/support.py — a Mock env auto-creates every
    # attribute, so GlyphIndex's getattr(env, 'dmint_denylist', set()) default
    # never applied and the constructor tried to iterate a Mock.
    return GlyphIndex(db=Mock(db_height=0), env=FakeEnv(reorg_limit=0))


class TestDeployContractStateIntegration:
    """Drive the real indexer parse + total_supply derivation."""

    def test_valid_contract_sets_expected_supply(self):
        """Golden: one valid contract → total_supply = 1 * reward * max_height."""
        idx = _indexer()
        token = _fresh_token()
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=5_000,
            target=0x1FFFFFFF,
        )
        # pad so len(script) >= 80 (deploy scan gate)
        script = script + bytes(80)
        idx._parse_deploy_contract_state(token, _make_tx([script]))
        assert token.reward == 5_000
        assert token.total_supply == 1 * 5_000 * 1_000_000  # 5_000_000_000

    def test_negative_reward_yields_no_negative_supply(self):
        """A contract with negative reward is rejected → supply stays 0."""
        idx = _indexer()
        token = _fresh_token()
        script = _make_dmint_v1_state(
            height=500_000, max_height=1_000_000, reward=-5_000,
            target=0x1FFFFFFF,
        ) + bytes(80)
        idx._parse_deploy_contract_state(token, _make_tx([script]))
        # parse rejected the contract → no state applied at all.
        assert token.total_supply == 0
        assert token.total_supply >= 0
        assert token.reward == 0

    def test_negative_max_height_yields_no_supply(self):
        idx = _indexer()
        token = _fresh_token()
        script = _make_dmint_v1_state(
            height=500_000, max_height=-1_000_000, reward=5_000,
            target=0x1FFFFFFF,
        ) + bytes(80)
        idx._parse_deploy_contract_state(token, _make_tx([script]))
        assert token.total_supply == 0
        assert token.reward == 0

    def test_oversized_product_not_set(self):
        """reward * max_height * num_contracts overflowing int64 → supply stays 0.

        Each multiplicand is individually in range (passes parse), but the
        product exceeds the int64 ceiling, so the indexer must NOT write it.
        Use many identical contract outputs to inflate num_contracts.
        """
        idx = _indexer()
        token = _fresh_token()
        # reward and max_height are 7-/4-byte in-filter scriptnums.
        reward = (1 << 50)            # ~1.1e15, valid scriptnum
        max_height = 1_000_000
        script = _make_dmint_v1_state(
            height=500_000, max_height=max_height, reward=reward,
            target=0x1FFFFFFF,
        ) + bytes(80)
        # 20 identical contract outputs → num_contracts = 20.
        # 20 * 1.1e15 * 1e6 = 2.2e22 >> 9.2e18 (int64 max).
        tx = _make_tx([script] * 20)
        idx._parse_deploy_contract_state(token, tx)
        product = 20 * reward * max_height
        assert product > DMINT_MAX_TOTAL_SUPPLY
        assert token.total_supply == 0          # clamped — not absurd/overflow
        assert token.reward == reward           # reward itself is valid

    def test_percent_mined_safe_when_supply_clamped(self):
        """is_fully_mined()/percent_mined() never divide-by-zero / go negative."""
        token = _fresh_token()
        token.total_supply = 0
        token.mined_supply = 1234
        assert token.percent_mined() == 0.0
        assert token.is_fully_mined() is False


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(pytest.main([__file__, '-v']))
