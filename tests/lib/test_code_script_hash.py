"""
codeScriptHash must agree with consensus.

Two independent defects were fixed here, and either alone still yields a mismatch:

  1. HASH: the node builds it with CHashWriter, whose CHash256::Finalize runs sha256 twice
     (Radiant-Core src/hash.h:34). RXinDexer used a single sha256.
  2. RANGE: the node hashes from stateSeperatorByteIndex, which GetOp has already advanced past
     the opcode by the time it is recorded (src/script/script.cpp:621), so OP_STATESEPARATOR
     itself is NOT part of the code section. RXinDexer sliced from the separator's own index and
     so included the 0xbd byte.

Consequence before the fix: a client computing the hash from script introspection, or via pyrxd
(which documents the hashed code script as `d0 <token_ref> || <12-byte epilogue>` — separator
excluded), got zero matches from codescripthash_* lookups.

The expectations below are derived from the node's algorithm, not from RXinDexer's output, so this
file fails if the implementation regresses toward either defect.

Run: PYTHONPATH=. python3 -m pytest tests/lib/test_code_script_hash.py
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from electrumx.lib.script import OpCodes, Script, ScriptError  # noqa: E402

OP_STATESEPARATOR = bytes([OpCodes.OP_STATESEPERATOR])          # 0xbd
OP_PUSHINPUTREFSINGLETON = bytes([OpCodes.OP_PUSHINPUTREFSINGLETON])   # 0xd8

STATE = bytes([0x04]) + b'\xde\xad\xbe\xef'          # <push 4> state
REF = bytes(range(36))
CODE = OP_PUSHINPUTREFSINGLETON + REF + b'\xac'      # d8 <ref> OP_CHECKSIG


def node_hash(code: bytes) -> bytes:
    """What CHashWriter/CHash256 produces over the code section: sha256 applied twice."""
    return hashlib.sha256(hashlib.sha256(code).digest()).digest()


def coin():
    # Imported lazily: coins.py pulls in the block processor, which needs optional deps.
    from electrumx.lib.coins import Radiant
    return Radiant


# --------------------------------------------------------------------------- separator index

def test_index_is_the_byte_after_the_separator():
    """The consensus index points at the first CODE byte, not at the 0xbd."""
    script = STATE + OP_STATESEPARATOR + CODE
    sep_at = len(STATE)
    assert script[sep_at] == OpCodes.OP_STATESEPERATOR
    assert Script.state_separator_byte_index(script) == sep_at + 1
    # The legacy helper still reports the separator's own index.
    assert Script.get_stateseperator_index(script) == sep_at


def test_index_is_zero_with_no_separator():
    script = STATE + CODE
    assert Script.state_separator_byte_index(script) == 0


def test_separator_at_index_zero_is_not_confused_with_absent():
    """The ambiguity the old helper could not express: a script beginning with 0xbd has an empty
    state section, and its code starts at 1 — not 0."""
    script = OP_STATESEPARATOR + CODE
    assert Script.state_separator_byte_index(script) == 1
    assert Script.get_stateseperator_index(script) == 0     # ambiguous, hence the new method


def test_separator_as_final_byte_gives_an_index_past_the_end():
    script = STATE + OP_STATESEPARATOR
    assert Script.state_separator_byte_index(script) == len(script)


def test_truncated_script_still_raises_scripterror():
    with_bad_push = bytes([OpCodes.OP_PUSHDATA4]) + b'\xff\xff\xff\xff'
    for fn in (Script.state_separator_byte_index, Script.get_stateseperator_index):
        try:
            fn(with_bad_push)
        except ScriptError:
            pass
        else:
            raise AssertionError(f'{fn.__name__} must raise ScriptError on a truncated script')


# --------------------------------------------------------------------------- the hash

def test_hash_is_double_sha_over_the_code_section():
    script = STATE + OP_STATESEPARATOR + CODE
    got = coin().codeScriptHash_from_script(script)
    assert got == node_hash(CODE)


def test_separator_byte_is_excluded():
    """The off-by-one: including the 0xbd changes the hash entirely."""
    script = STATE + OP_STATESEPARATOR + CODE
    got = coin().codeScriptHash_from_script(script)
    assert got != node_hash(OP_STATESEPARATOR + CODE), 'separator must not be hashed'


def test_single_sha_is_rejected():
    """The other half: a single pass over the right bytes is still wrong."""
    script = STATE + OP_STATESEPARATOR + CODE
    got = coin().codeScriptHash_from_script(script)
    assert got != hashlib.sha256(CODE).digest()


def test_neither_old_variant_matches():
    """Both defects together — the pre-fix value — must not be produced."""
    script = STATE + OP_STATESEPARATOR + CODE
    old = hashlib.sha256(OP_STATESEPARATOR + CODE).digest()
    assert coin().codeScriptHash_from_script(script) != old


def test_no_separator_hashes_the_whole_script():
    script = STATE + CODE
    assert coin().codeScriptHash_from_script(script) == node_hash(script)


def test_separator_as_final_byte_hashes_the_empty_script():
    """The node's explicit branch: stateSeperatorByteIndex >= script.size() hashes an empty
    CScript. RXinDexer previously hashed the lone 0xbd byte instead."""
    script = STATE + OP_STATESEPARATOR
    got = coin().codeScriptHash_from_script(script)
    assert got == node_hash(b'')
    assert got != node_hash(OP_STATESEPARATOR)


def test_separator_at_index_zero_hashes_everything_after_it():
    script = OP_STATESEPARATOR + CODE
    assert coin().codeScriptHash_from_script(script) == node_hash(CODE)


def test_state_section_does_not_affect_the_hash():
    """Two outputs sharing a covenant but holding different state must share a codeScriptHash —
    that is the whole point of the code/state split."""
    a = bytes([0x04]) + b'\x00\x00\x00\x01' + OP_STATESEPARATOR + CODE
    b = bytes([0x04]) + b'\xff\xff\xff\xff' + OP_STATESEPARATOR + CODE
    assert coin().codeScriptHash_from_script(a) == coin().codeScriptHash_from_script(b)


def test_ref_operands_are_walked_not_scanned():
    """A 0xbd byte occurring INSIDE a 36-byte ref operand is data, not a separator. The walker
    consumes ref operands wholesale, so such a byte must not split the script."""
    ref_with_bd = bytes([0xbd]) * 36
    script = (bytes([0x04]) + b'\x01\x02\x03\x04'
              + OP_PUSHINPUTREFSINGLETON + ref_with_bd
              + OP_STATESEPARATOR + CODE)
    # The only real separator is the explicit one, so the code section is CODE.
    assert coin().codeScriptHash_from_script(script) == node_hash(CODE)


def test_db_version_was_bumped():
    """The stored value changed, so a v9 DB must refuse to start rather than mix schemes."""
    src = open(os.path.join(os.path.dirname(__file__), '..', '..', 'electrumx', 'server',
                            'db.py'), encoding='utf-8').read()
    assert 'DB_VERSIONS = [10]' in src


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
