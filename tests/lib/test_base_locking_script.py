"""
base_locking_script: a byte pre-filter in front of the opcode walk, and the enum binding that
made every script parser ~5x faster.

Found by profiling a stalled ref-history rescan on mainnet (2026-09-07). It had decelerated to
1.53 blocks/s with ~30h remaining, and 8 of 10 py-spy samples sat in this one function. Two
separate causes:

  1. `OpCodes` is an Enumeration INSTANCE holding its members in `self.lookup`, so every
     `OpCodes.OP_DUP` missed normal attribute lookup and fell through to a Python-level
     __getattr__ -- roughly three per opcode inside the parsing loops. cProfile counted 122,100
     such calls per 300 invocations on a real 238-byte dMint contract script. Binding the
     members as real instance attributes made script parsing ~5x faster tree-wide.

  2. Even then the function walked every opcode of every token output. dMint contract scripts
     are the longest ones AND every mint spends and re-creates its contract singleton, so they
     dominate the cost. They contain no address template at all, which a byte search can prove
     without walking: 105us -> 0.49us on the real contract script.

The result is PERSISTED (the GO owner index, and the b'rb' side table whose value must agree at
create and spend time), so what matters most here is that the optimisation changed no answers.
That is what the differential corpus below is for.

Run: PYTHONPATH=. python3 -m pytest tests/lib/test_base_locking_script.py
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from electrumx.lib.enum import Enumeration, EnumError  # noqa: E402
from electrumx.lib.script import (  # noqa: E402
    INPUT_REF_OPS, OpCodes, P2PKH_LEAD_BYTES, P2SH_LEAD_BYTES, Script,
)

P2PKH = (bytes([OpCodes.OP_DUP, OpCodes.OP_HASH160, 0x14]) + bytes(20)
         + bytes([OpCodes.OP_EQUALVERIFY, OpCodes.OP_CHECKSIG]))
P2SH = bytes([OpCodes.OP_HASH160, 0x14]) + bytes(20) + bytes([OpCodes.OP_EQUAL])
REF = bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(36)
SREF = bytes([OpCodes.OP_PUSHINPUTREFSINGLETON]) + bytes(36)
DROP = bytes([OpCodes.OP_DROP])


def reference(script):
    """base_locking_script exactly as it was before the pre-filter: walk every opcode, scan for
    a P2PKH, then for a P2SH, then strip a leading ref preamble."""
    try:
        ops = Script._walk_ops(script)
    except Exception:
        return script
    for i in range(len(ops) - 4):
        if (ops[i][0] == OpCodes.OP_DUP and ops[i + 1][0] == OpCodes.OP_HASH160
                and ops[i + 2][0] == 0x14 and ops[i + 2][1] == 20
                and ops[i + 3][0] == OpCodes.OP_EQUALVERIFY
                and ops[i + 4][0] == OpCodes.OP_CHECKSIG):
            return script[ops[i][2]:ops[i + 4][3]]
    for i in range(len(ops) - 2):
        if (ops[i][0] == OpCodes.OP_HASH160 and ops[i + 1][0] == 0x14
                and ops[i + 1][1] == 20 and ops[i + 2][0] == OpCodes.OP_EQUAL):
            return script[ops[i][2]:ops[i + 2][3]]
    try:
        n, length = 0, len(script)
        while n < length:
            op = script[n]
            if op in INPUT_REF_OPS:
                if n + 1 + 36 > length:
                    break
                n += 1 + 36
            elif op == OpCodes.OP_DROP or op == OpCodes.OP_2DROP:
                n += 1
            else:
                break
        remainder = script[n:]
        return remainder if remainder else script
    except Exception:
        return script


def _contract(nops, seed=1):
    """A dMint-style contract body: pushes, refs and arithmetic, and no address template."""
    rnd = random.Random(seed)
    out = bytearray()
    for _ in range(nops):
        c = rnd.randrange(4)
        if c == 0:
            d = rnd.randrange(1, 60)
            out += bytes([d]) + bytes(d)
        elif c == 1:
            out += REF
        elif c == 2:
            out += DROP
        else:
            out += bytes([0x93])
    return bytes(out)


def _corpus():
    cases = {
        'nft (address last)': SREF + DROP + P2PKH,
        'ft (address first)': P2PKH + bytes([0xbd]) + REF,
        'p2sh only': SREF + DROP + P2SH,
        'p2sh before p2pkh': P2SH + _contract(20) + P2PKH,
        'contract then p2pkh': _contract(400) + P2PKH,
        'contract, no address': _contract(400),
        'empty': b'',
        'truncated push': bytes([0x4b]) + bytes(10),
        'p2pkh then truncated': P2PKH + bytes([0x4b]) + bytes(10),
        'truncated ref': bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(10),
        'p2pkh then truncated ref': P2PKH + bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(10),
        'ref preamble only': SREF + DROP,
        'pushdata1': bytes([OpCodes.OP_PUSHDATA1, 40]) + bytes(40) + P2PKH,
        'pushdata2': bytes([OpCodes.OP_PUSHDATA2, 0x10, 0x00]) + bytes(16) + P2PKH,
        'template hidden inside a push': bytes([25]) + P2PKH + P2SH,
        'bare p2sh': P2SH,
    }
    # The pre-filter's carve-out: a ref preamble IS stripped and no address bytes are present.
    # That is the only shape where skipping the walk could have diverged, so truncate everywhere.
    for bi, body in enumerate([REF, SREF, REF + DROP, SREF + DROP,
                               REF + REF + DROP + DROP, SREF + DROP + REF + DROP]):
        for tail in range(0, 40):
            for fi, filler in enumerate((b'', bytes([0x93]) * 3, bytes([0x4b]) + bytes(20))):
                base = body + filler + bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(tail)
                cases['carve%d_%d_%d' % (bi, tail, fi)] = base
                cases['carve%d_%d_%d_trunc' % (bi, tail, fi)] = base[:-1]
    rnd = random.Random(7)
    for i in range(600):
        cases['fuzz%d' % i] = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 80)))
    for i in range(600):
        body = rnd.choice([REF, SREF, REF + DROP, SREF + DROP])
        junk = bytes(rnd.choice([0x93, 0x75, 0x51, 0x00, 0x4b, 0xd0, 0xd8])
                     for _ in range(rnd.randrange(0, 60)))
        cases['carvefuzz%d' % i] = body + junk
    for i in range(300):
        cases['mix%d' % i] = (rnd.choice([SREF, P2PKH, P2SH, b''])
                              + bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 50)))
                              + rnd.choice([P2PKH, P2SH, b'']))
    return cases


CORPUS = _corpus()


def test_optimised_matches_the_reference_on_every_script():
    """The value is persisted, so an optimisation that changes any answer is a data bug."""
    mismatches = [name for name, s in CORPUS.items()
                  if reference(s) != Script.base_locking_script(s)]
    assert not mismatches, '%d diverged, e.g. %s' % (len(mismatches), mismatches[:5])
    assert len(CORPUS) > 2000, 'corpus should be broad enough to be worth trusting'


@pytest.mark.parametrize('name', [
    'nft (address last)', 'ft (address first)', 'p2sh only', 'p2sh before p2pkh',
    'contract, no address', 'template hidden inside a push', 'p2pkh then truncated ref',
])
def test_named_shapes_individually(name):
    """Named separately from the bulk comparison so a failure says which shape broke."""
    assert reference(CORPUS[name]) == Script.base_locking_script(CORPUS[name])


def test_prefilter_bytes_are_a_necessary_condition_for_the_templates():
    """The whole shortcut rests on this: the opcodes appear in the script as these literal
    bytes, so their absence proves the template is absent whatever the op boundaries are."""
    assert P2PKH.startswith(P2PKH_LEAD_BYTES)
    assert P2SH.startswith(P2SH_LEAD_BYTES)
    # A P2PKH's lead contains a P2SH's, so one `in` test gates both.
    assert P2SH_LEAD_BYTES in P2PKH_LEAD_BYTES


def test_a_contract_script_skips_the_walk_entirely():
    """The shortcut fires only when nothing is stripped, which is the real dMint shape: those
    scripts open with a push, not a ref preamble (mainnet reveal 8736b2bd..., vout 0-9)."""
    body = bytes([0x04]) + bytes(4) + _contract(400)
    assert P2SH_LEAD_BYTES not in body, 'this fixture must have no address template'
    assert Script._ref_preamble_end(body) == 0, 'must take the no-strip shortcut'
    assert Script.base_locking_script(body) == body
    assert reference(body) == body, 'and agree with the pre-optimisation answer'


def test_a_contract_script_behind_a_ref_preamble_still_walks():
    """Counterpart: when a preamble IS stripped the shortcut must be declined, because a later
    truncation would have made the old code return the whole script instead."""
    body = REF + DROP + _contract(200)
    assert P2SH_LEAD_BYTES not in body
    assert Script._ref_preamble_end(body) != 0
    assert Script.base_locking_script(body) == reference(body)


def test_ref_preamble_end_finds_the_boundary():
    assert Script._ref_preamble_end(SREF + DROP + P2PKH) == len(SREF) + 1
    assert Script._ref_preamble_end(P2PKH) == 0, 'no preamble'
    assert Script._ref_preamble_end(b'') == 0
    # A truncated ref stops the scan rather than running past the end.
    assert Script._ref_preamble_end(bytes([OpCodes.OP_PUSHINPUTREF]) + bytes(10)) == 0


# --------------------------------------------------------------------------- the enum binding

def test_enum_members_resolve_without_falling_through_to_getattr():
    """The fix that gave ~5x. Unbound, every OpCodes.X in the parsing loops is a Python-level
    __getattr__ call plus a dict get."""
    assert 'OP_DUP' in vars(OpCodes), 'members must be real instance attributes'
    assert OpCodes.OP_DUP == OpCodes.lookup['OP_DUP']
    assert len(vars(OpCodes)) >= len(OpCodes.lookup)


def test_enum_still_raises_for_unknown_members():
    with pytest.raises(AttributeError):
        OpCodes.OP_NOT_A_REAL_OPCODE


def test_enum_reverse_lookup_is_unaffected():
    assert OpCodes.whatis(OpCodes.OP_DUP) == 'OP_DUP'


def test_enum_refuses_a_member_that_would_shadow_an_attribute():
    """Binding members into __dict__ would otherwise silently clobber `lookup`."""
    with pytest.raises(EnumError):
        Enumeration('bad', ['lookup'])
    with pytest.raises(EnumError):
        Enumeration('bad', ['reverseLookup'])


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
