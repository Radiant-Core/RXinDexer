"""Regression test for RSWP v2 swap-advertisement parsing.

Decodes the priceTerms MultiTxOutV1 blob (Photonic encoding) so /swaps/orders
returns the real requested amount and a resolvable maker — instead of the
price=0/amount=0/maker=null the previous parser produced.

Exercised against a real mainnet order (tx 5fbd060e…, height 428525) over the
actual RSWP OP_RETURN script — no node/chain needed.
"""
from electrumx.lib.coins import Radiant
from electrumx.server.swap_index import SwapIndex, OrderSide


# Real RSWP v2 OP_RETURN scriptPubKey from mainnet order 5fbd060e… vout 0.
ORDER_SCRIPT_HEX = (
    "6a0452535750010201010100010120"
    "0000000000000000000000000000000000000000000000000000000000000000"  # offeredTokenId (zero = RXD)
    "204f9b4240e9e550191ba05d50f036f0939283c2457a16dccae57dd23b41414932"  # wantTokenId
    "20d900351899a25a4aea526dc17884059891c798fa9cf6ad2bfc9119797868181b"  # offeredTxid
    "00"  # offeredVout
    "4c55"  # OP_PUSHDATA1 85 -> priceTerms (MultiTxOutV1)
    "0110270000000000004b76a914d1734330f1afe6cd574d7bc5ec792e113a90a12e88ac"
    "bdd018e54ed487d33ec9ffc09cc05aed8513c6a893e6d19c47224159a04a38830f3700000000dec0e9aa76e378e4a269e69d"
    "4c6a"  # OP_PUSHDATA1 106 -> signature
    "47304402203c138413ad81fa0439b9480c19136d142c7a360e3d612c5a9caf11d42da4c027"
    "02201990700db8d4c88bd56cf0bb34169f67e1e8aab2f34dc0321f3ba7f3552055d3c321"
    "035c4691a0a46949cee9f698631301634264338a1290708603d0fb8e51dbfa208b"
)

EXPECT_MAKER_ADDR = "1L6UJfojmZEciBo83yB1cCMASZyQ8zMKuw"
EXPECT_MAKER_SH = "7c661d65276a2a7b6e3abd85cf8b843d834fc4aeef578d34a4328e33a63c0526"


class FakeEnv:
    coin = Radiant
    swap_index = True
    reorg_limit = 0


class FakeDB:
    class utxo_db:
        @staticmethod
        def get(k):
            return None
    db_height = 433966


def _index():
    return SwapIndex(FakeDB(), FakeEnv())


def test_parse_multi_txout_roundtrip():
    si = _index()
    # count=1, value=10000 (LE 8B), scriptLen=3, script=aabbcc
    blob = bytes.fromhex("01" + "1027000000000000" + "03" + "aabbcc")
    out = si._parse_multi_txout(blob)
    assert out == [(10000, bytes.fromhex("aabbcc"))]


def test_parse_multi_txout_legacy_fallback():
    si = _index()
    # not valid MultiTxOut (no sensible count framing) -> legacy [value(8), script(rest)]
    blob = bytes.fromhex("e803000000000000" + "deadbeef")  # value=1000
    out = si._parse_multi_txout(blob)
    assert out and out[0][0] == 1000 and out[0][1] == bytes.fromhex("deadbeef")


def test_rswp_v2_order_amount_and_maker():
    si = _index()
    script = bytes.fromhex(ORDER_SCRIPT_HEX)
    order = si._parse_rswp_advertisement(script, b"\x11" * 32, 0, 428525, 1780264931)
    assert order is not None, "parser returned None"
    # economic fields now populated from the priceTerms blob (were 0/null before)
    assert order.amount == 10000, order.amount
    assert order.price == 10000, order.price
    assert order.remaining_amount == 10000
    # resolvable maker (was null)
    assert order.maker_address == EXPECT_MAKER_ADDR, order.maker_address
    assert order.maker_scripthash.hex() == EXPECT_MAKER_SH, order.maker_scripthash.hex()
    # structural fields: RXD offered + token wanted = a BUY, and the pair is
    # normalized token-as-base so this bid lands in the SAME orderbook book
    # as the asks for the wanted token (base = WANT side, quote = RXD).
    assert order.base_ref[:32].hex() == "4f9b4240e9e550191ba05d50f036f0939283c2457a16dccae57dd23b41414932"
    assert order.quote_ref == bytes(36)  # zero ref = native RXD offered
    assert order.side == OrderSide.BUY


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
