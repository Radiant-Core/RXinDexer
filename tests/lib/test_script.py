import pytest

from electrumx.lib.script import OpCodes, is_unspendable_legacy, is_unspendable_genesis


@pytest.mark.parametrize("script, iug", (
    (bytes([OpCodes.OP_RETURN]), False),
    (bytes([OpCodes.OP_RETURN]) + bytes([2, 28, 50]), False),
    (bytes([OpCodes.OP_0, OpCodes.OP_RETURN]), True),
    (bytes([OpCodes.OP_0, OpCodes.OP_RETURN]) + bytes([2, 28, 50]), True)
))
def test_op_return_legacy(script, iug):
    assert is_unspendable_legacy(script)
    assert is_unspendable_genesis(script) is iug


@pytest.mark.parametrize("script", (
    bytes([]),
    bytes([OpCodes.OP_1, OpCodes.OP_RETURN]) + bytes([2, 28, 50]),
    bytes([OpCodes.OP_0]),
    bytes([OpCodes.OP_0, OpCodes.OP_1]),
    bytes([OpCodes.OP_HASH160]),
))
def test_not_op_return(script):
    assert not is_unspendable_legacy(script)
    assert not is_unspendable_genesis(script)


# ---------------------------------------------------------------------------
# Script.base_locking_script — strips the Radiant input-ref preamble so that
# Glyph token outputs are keyed by the recipient's base-address hashX, making
# them discoverable via the holder's standard Electrum scripthash.
# Vectors are the real "Radiant Cube" NFT fixture.
# ---------------------------------------------------------------------------

from electrumx.lib.script import Script  # noqa: E402
from electrumx.lib.hash import sha256, hex_str_to_hash, HASHX_LEN  # noqa: E402

# vout 0 of the reveal tx: OP_PUSHINPUTREFSINGLETON <36b ref> OP_DROP <P2PKH>
_RADIANT_CUBE_TOKEN_OUTPUT = bytes.fromhex(
    "d80425b11bb6fa037fc6c25df145c9aa27f2ceeb76f2d85ece5408af6625991a0400000000"
    "7576a91455ff8f32c1a7e6a5664609e9f1e07ca396de3fb788ac"
)
_BASE_P2PKH = bytes.fromhex("76a91455ff8f32c1a7e6a5664609e9f1e07ca396de3fb788ac")
# Electrum scripthash of the owner address moMfswEJUgX3VK6LWBgFvZsXzHHxZHxJ1f
_OWNER_SCRIPTHASH = "59dea47da05ec1d2ecf6ed312b523926c7ac1058860dcf2bfe3338ce4495d1e6"


def test_base_locking_script_strips_singleton_ref_preamble():
    assert Script.base_locking_script(_RADIANT_CUBE_TOKEN_OUTPUT) == _BASE_P2PKH


def test_base_locking_script_identity_for_plain_script():
    # No ref preamble -> returned unchanged.
    assert Script.base_locking_script(_BASE_P2PKH) == _BASE_P2PKH


def test_base_locking_script_handles_multi_ref_2drop():
    # OP_PUSHINPUTREF <36> OP_PUSHINPUTREFSINGLETON <36> OP_2DROP <P2PKH>
    script = (bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(36)
              + bytes([OpCodes.OP_PUSHINPUTREFSINGLETON]) + bytes(36)
              + bytes([OpCodes.OP_2DROP]) + _BASE_P2PKH)
    assert Script.base_locking_script(script) == _BASE_P2PKH


def test_base_hashX_matches_owner_scripthash():
    # The crux of the per-address ownership fix: the base-address hashX derived
    # from a Glyph token output must equal the hashX the server derives from the
    # holder's standard Electrum scripthash (scripthash_to_hashX), so that
    # glyph.list_tokens(scripthash) finds the balance.
    base_hashX = sha256(Script.base_locking_script(_RADIANT_CUBE_TOKEN_OUTPUT))[:HASHX_LEN]
    scripthash_hashX = hex_str_to_hash(_OWNER_SCRIPTHASH)[:HASHX_LEN]
    assert base_hashX == scripthash_hashX
