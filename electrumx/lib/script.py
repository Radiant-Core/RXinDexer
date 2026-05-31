# Copyright (c) 2022 The Radiant Blockchain Developers
# Copyright (c) 2016-2017, Neil Booth
# Copyright (c) 2017, the ElectrumX authors
#
# All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# and warranty status of this software.


'''Script-related classes and functions.'''

from electrumx.lib.enum import Enumeration
from electrumx.lib.util import unpack_le_uint16_from, unpack_le_uint32_from, \
    pack_le_uint16, pack_le_uint32

class ScriptError(Exception):
    '''Exception used for script errors.'''

OpCodes = Enumeration("Opcodes", [
    ("OP_0", 0), ("OP_PUSHDATA1", 76),
    "OP_PUSHDATA2", "OP_PUSHDATA4", "OP_1NEGATE",
    "OP_RESERVED",
    "OP_1", "OP_2", "OP_3", "OP_4", "OP_5", "OP_6", "OP_7", "OP_8",
    "OP_9", "OP_10", "OP_11", "OP_12", "OP_13", "OP_14", "OP_15", "OP_16",
    "OP_NOP", "OP_VER", "OP_IF", "OP_NOTIF", "OP_VERIF", "OP_VERNOTIF",
    "OP_ELSE", "OP_ENDIF", "OP_VERIFY", "OP_RETURN",
    "OP_TOALTSTACK", "OP_FROMALTSTACK", "OP_2DROP", "OP_2DUP", "OP_3DUP",
    "OP_2OVER", "OP_2ROT", "OP_2SWAP", "OP_IFDUP", "OP_DEPTH", "OP_DROP",
    "OP_DUP", "OP_NIP", "OP_OVER", "OP_PICK", "OP_ROLL", "OP_ROT",
    "OP_SWAP", "OP_TUCK",
    "OP_CAT", "OP_SUBSTR", "OP_LEFT", "OP_RIGHT", "OP_SIZE",
    "OP_INVERT", "OP_AND", "OP_OR", "OP_XOR", "OP_EQUAL", "OP_EQUALVERIFY",
    "OP_RESERVED1", "OP_RESERVED2",
    "OP_1ADD", "OP_1SUB", "OP_2MUL", "OP_2DIV", "OP_NEGATE", "OP_ABS",
    "OP_NOT", "OP_0NOTEQUAL", "OP_ADD", "OP_SUB", "OP_MUL", "OP_DIV", "OP_MOD",
    "OP_LSHIFT", "OP_RSHIFT", "OP_BOOLAND", "OP_BOOLOR", "OP_NUMEQUAL",
    "OP_NUMEQUALVERIFY", "OP_NUMNOTEQUAL", "OP_LESSTHAN", "OP_GREATERTHAN",
    "OP_LESSTHANOREQUAL", "OP_GREATERTHANOREQUAL", "OP_MIN", "OP_MAX",
    "OP_WITHIN",
    "OP_RIPEMD160", "OP_SHA1", "OP_SHA256", "OP_HASH160", "OP_HASH256",
    "OP_CODESEPARATOR", "OP_CHECKSIG", "OP_CHECKSIGVERIFY", "OP_CHECKMULTISIG",
    "OP_CHECKMULTISIGVERIFY",
    "OP_NOP1",
    "OP_CHECKLOCKTIMEVERIFY", "OP_CHECKSEQUENCEVERIFY",

    ("OP_CHECKDATASIG", 186), 
    ("OP_CHECKDATASIGVERIFY", 187), 
    ("OP_REVERSEBYTES", 188), 
    
    ("OP_STATESEPERATOR", 189), 
    ("OP_STATESEPERATORINDEX_UTXO", 190), 
    ("OP_STATESEPERATORINDEX_OUTPUT", 191), 

    ("OP_SHA512_256", 206), 
    ("OP_HASH512_256", 207),
    ("OP_PUSHINPUTREF", 208), 
    ("OP_REQUIREINPUTREF", 209), 
    ("OP_DISALLOWPUSHINPUTREF", 210),
    ("OP_DISALLOWPUSHINPUTREFSIBLING", 211),

    ("OP_REFHASHDATASUMMARY_UTXO", 212),
    ("OP_REFHASHVALUESUM_UTXOS", 213),
    ("OP_REFHASHDATASUMMARY_OUTPUT", 214),
    ("OP_REFHASHVALUESUM_OUTPUTS", 215),

    ("OP_PUSHINPUTREFSINGLETON", 216),
    ("OP_REFTYPE_UTXO", 217),
    ("OP_REFTYPE_OUTPUT", 218),

    ("OP_REFVALUESUM_UTXOS", 219),
    ("OP_REFVALUESUM_OUTPUTS", 220),
    ("OP_REFOUTPUTCOUNT_UTXOS", 221),
    ("OP_REFOUTPUTCOUNT_OUTPUTS", 222),
    ("OP_REFOUTPUTCOUNTZEROVALUED_UTXOS", 223),
    ("OP_REFOUTPUTCOUNTZEROVALUED_OUTPUTS", 224),
    ("OP_REFDATASUMMARY_UTXO", 225),
    ("OP_REFDATASUMMARY_OUTPUT", 226),

    ("OP_CODESCRIPTHASHVALUESUM_UTXOS", 227),
    ("OP_CODESCRIPTHASHVALUESUM_OUTPUTS", 228),
    ("OP_CODESCRIPTHASHOUTPUTCOUNT_UTXOS", 229),
    ("OP_CODESCRIPTHASHOUTPUTCOUNT_OUTPUTS", 230),
    ("OP_CODESCRIPTHASHZEROVALUEDOUTPUTCOUNT_UTXOS", 231),
    ("OP_CODESCRIPTHASHZEROVALUEDOUTPUTCOUNT_OUTPUTS", 232),
    ("OP_CODESCRIPTBYTECODE_UTXO", 233),
    ("OP_CODESCRIPTBYTECODE_OUTPUT", 234),
    ("OP_STATESCRIPTBYTECODE_UTXO", 235),
    ("OP_STATESCRIPTBYTECODE_OUTPUT", 236),
    ("OP_PUSH_TX_STATE", 237)
])

# Cached in hash set for improved parsing performance
INPUT_REF_OPS = {
    OpCodes.OP_PUSHINPUTREF,
    OpCodes.OP_PUSHINPUTREFSINGLETON,
    OpCodes.OP_REQUIREINPUTREF,
    OpCodes.OP_DISALLOWPUSHINPUTREF,
    OpCodes.OP_DISALLOWPUSHINPUTREFSIBLING,
}
PUSH_INPUT_REF_OPS = {
    OpCodes.OP_PUSHINPUTREF,
    OpCodes.OP_PUSHINPUTREFSINGLETON
}
CHECKSIG_OPS = {
    OpCodes.OP_CHECKSIG,
    OpCodes.OP_CHECKSIGVERIFY,
    OpCodes.OP_CHECKMULTISIG,
    OpCodes.OP_CHECKMULTISIGVERIFY
}


# Paranoia to make it hard to create bad scripts
assert OpCodes.OP_DUP == 0x76
assert OpCodes.OP_HASH160 == 0xa9
assert OpCodes.OP_EQUAL == 0x87
assert OpCodes.OP_EQUALVERIFY == 0x88
assert OpCodes.OP_CHECKSIG == 0xac
assert OpCodes.OP_CHECKMULTISIG == 0xae

# Added for Radiant
assert OpCodes.OP_CHECKDATASIG == 0xba
assert OpCodes.OP_CHECKDATASIGVERIFY == 0xbb
assert OpCodes.OP_REVERSEBYTES == 0xbc
assert OpCodes.OP_STATESEPERATOR == 0xbd
assert OpCodes.OP_STATESEPERATORINDEX_UTXO == 0xbe
assert OpCodes.OP_STATESEPERATORINDEX_OUTPUT == 0xbf

assert OpCodes.OP_SHA512_256 == 0xce
assert OpCodes.OP_HASH512_256 == 0xcf
assert OpCodes.OP_PUSHINPUTREF == 0xd0
assert OpCodes.OP_REQUIREINPUTREF == 0xd1
assert OpCodes.OP_DISALLOWPUSHINPUTREF == 0xd2
assert OpCodes.OP_DISALLOWPUSHINPUTREFSIBLING == 0xd3
assert OpCodes.OP_REFHASHDATASUMMARY_UTXO == 0xd4
assert OpCodes.OP_REFHASHVALUESUM_UTXOS == 0xd5
assert OpCodes.OP_REFHASHDATASUMMARY_OUTPUT == 0xd6
assert OpCodes.OP_REFHASHVALUESUM_OUTPUTS == 0xd7
assert OpCodes.OP_PUSHINPUTREFSINGLETON == 0xd8
assert OpCodes.OP_REFTYPE_UTXO == 0xd9
assert OpCodes.OP_REFTYPE_OUTPUT == 0xda
assert OpCodes.OP_REFVALUESUM_UTXOS == 0xdb
assert OpCodes.OP_REFVALUESUM_OUTPUTS == 0xdc
assert OpCodes.OP_REFOUTPUTCOUNT_UTXOS == 0xdd
assert OpCodes.OP_REFOUTPUTCOUNT_OUTPUTS == 0xde
assert OpCodes.OP_REFOUTPUTCOUNTZEROVALUED_UTXOS == 0xdf
assert OpCodes.OP_REFOUTPUTCOUNTZEROVALUED_OUTPUTS == 0xe0
assert OpCodes.OP_REFDATASUMMARY_UTXO == 0xe1
assert OpCodes.OP_REFDATASUMMARY_OUTPUT == 0xe2
assert OpCodes.OP_CODESCRIPTHASHVALUESUM_UTXOS == 0xe3
assert OpCodes.OP_CODESCRIPTHASHVALUESUM_OUTPUTS == 0xe4
assert OpCodes.OP_CODESCRIPTHASHOUTPUTCOUNT_UTXOS == 0xe5
assert OpCodes.OP_CODESCRIPTHASHOUTPUTCOUNT_OUTPUTS == 0xe6
assert OpCodes.OP_CODESCRIPTHASHZEROVALUEDOUTPUTCOUNT_UTXOS == 0xe7
assert OpCodes.OP_CODESCRIPTHASHZEROVALUEDOUTPUTCOUNT_OUTPUTS == 0xe8
assert OpCodes.OP_CODESCRIPTBYTECODE_UTXO == 0xe9
assert OpCodes.OP_CODESCRIPTBYTECODE_OUTPUT == 0xea
assert OpCodes.OP_STATESCRIPTBYTECODE_UTXO == 0xeb
assert OpCodes.OP_STATESCRIPTBYTECODE_OUTPUT == 0xec
assert OpCodes.OP_PUSH_TX_STATE == 0xed

def is_unspendable_legacy(script):
    # OP_FALSE OP_RETURN or OP_RETURN
    return script[:2] == b'\x00\x6a' or (script and script[0] == 0x6a)


def is_unspendable_genesis(script):
    # OP_FALSE OP_RETURN
    return script[:2] == b'\x00\x6a'


def _match_ops(ops, pattern):
    if len(ops) != len(pattern):
        return False
    for op, pop in zip(ops, pattern):
        if pop != op:
            # -1 means 'data push', whose op is an (op, data) tuple
            if pop == -1 and isinstance(op, tuple):
                continue
            return False

    return True


class ScriptPubKey(object):
    '''A class for handling a tx output script that gives conditions
    necessary for spending.
    '''

    TO_ADDRESS_OPS = [OpCodes.OP_DUP, OpCodes.OP_HASH160, -1,
                      OpCodes.OP_EQUALVERIFY, OpCodes.OP_CHECKSIG]
    TO_P2SH_OPS = [OpCodes.OP_HASH160, -1, OpCodes.OP_EQUAL]
    TO_PUBKEY_OPS = [-1, OpCodes.OP_CHECKSIG]

    @classmethod
    def P2SH_script(cls, hash160):
        return (bytes([OpCodes.OP_HASH160])
                + Script.push_data(hash160)
                + bytes([OpCodes.OP_EQUAL]))

    @classmethod
    def P2PKH_script(cls, hash160):
        return (bytes([OpCodes.OP_DUP, OpCodes.OP_HASH160])
                + Script.push_data(hash160)
                + bytes([OpCodes.OP_EQUALVERIFY, OpCodes.OP_CHECKSIG]))


class Script(object):

    @classmethod
    def get_stateseperator_index(cls, script):
        try:
            n = 0
            while n < len(script):
                op = script[n]
                # Found the state seperator
                if op == OpCodes.OP_STATESEPERATOR:
                    return n
                
                n += 1
                if op <= OpCodes.OP_PUSHDATA4:
                    # Raw bytes follow
                    if op < OpCodes.OP_PUSHDATA1:
                        dlen = op
                    elif op == OpCodes.OP_PUSHDATA1:
                        dlen = script[n]
                        n += 1
                    elif op == OpCodes.OP_PUSHDATA2:
                        dlen, = unpack_le_uint16_from(script[n: n + 2])
                        n += 2
                    elif op == OpCodes.OP_PUSHDATA4:
                        dlen, = unpack_le_uint32_from(script[n: n + 4])
                        n += 4
                    if n + dlen > len(script):
                        raise IndexError
                    n += dlen 

                elif op == OpCodes.OP_PUSHINPUTREF or op == OpCodes.OP_REQUIREINPUTREF or op == OpCodes.OP_DISALLOWPUSHINPUTREF or op == OpCodes.OP_DISALLOWPUSHINPUTREFSIBLING or op == OpCodes.OP_PUSHINPUTREFSINGLETON:
                    dlen = 36 # Grab 36 bytes for the hash
                    if n + dlen > len(script):
                        raise IndexError
                    n += dlen 

        except Exception as e:
            print(f'e {e}')
            raise ScriptError('truncated script') from None
        # No state seperator found
        return 0

    @classmethod
    def get_ops(cls, script):
        ops = []

        # The unpacks or script[n] below throw on truncated scripts
        try:
            n = 0
            dlen = 0
            while n < len(script):
                op = script[n]
                n += 1

                if op <= OpCodes.OP_PUSHDATA4:
                    # Raw bytes follow
                    if op < OpCodes.OP_PUSHDATA1:
                        dlen = op
                    elif op == OpCodes.OP_PUSHDATA1:
                        dlen = script[n]
                        n += 1
                    elif op == OpCodes.OP_PUSHDATA2:
                        dlen, = unpack_le_uint16_from(script[n: n + 2])
                        n += 2
                    elif op == OpCodes.OP_PUSHDATA4:
                        dlen, = unpack_le_uint32_from(script[n: n + 4])
                        n += 4

                    if n + dlen > len(script):
                        raise IndexError

                    op = (op, script[n:n + dlen])
                    n += dlen

                elif op in INPUT_REF_OPS:
                    dlen = 36  # Grab 36 bytes for the hash

                    if n + dlen > len(script):
                        raise IndexError
                    n += dlen 

                op = (op, script[n:n + dlen])
                ops.append(op)
        except Exception:
            # Truncated script; e.g. tx_hash
            # ebc9fa1196a59e192352d76c0f6e73167046b9d37b8302b6bb6968dfd279b767
            raise ScriptError('truncated script') from None

        return ops
    # Saves the push input refs of a script in the order they were encountered
    @classmethod
    def get_push_input_refs(cls, script):
        all_refs = []
        normal_refs = []
        singleton_refs = []

        # The unpacks or script[n] below throw on truncated scripts
        try:
            n = 0
            dlen = 0
            while n < len(script):
                op = script[n]
                n += 1

                if op <= OpCodes.OP_PUSHDATA4:
                    # Raw bytes follow
                    if op < OpCodes.OP_PUSHDATA1:
                        dlen = op
                    elif op == OpCodes.OP_PUSHDATA1:
                        dlen = script[n]
                        n += 1
                    elif op == OpCodes.OP_PUSHDATA2:
                        dlen, = unpack_le_uint16_from(script[n: n + 2])
                        n += 2
                    elif op == OpCodes.OP_PUSHDATA4:
                        dlen, = unpack_le_uint32_from(script[n: n + 4])
                        n += 4

                    if n + dlen > len(script):
                        raise IndexError

                    n += dlen

                elif op in INPUT_REF_OPS:
                    dlen = 36  # Grab 36 bytes

                    if op == OpCodes.OP_PUSHINPUTREF:
                        ref = script[n:n + dlen]
                        all_refs.append(ref)
                        normal_refs.append(ref)
                    elif op == OpCodes.OP_PUSHINPUTREFSINGLETON:
                        ref = script[n:n + dlen]
                        all_refs.append(ref)
                        singleton_refs.append(ref)

                    if n + dlen > len(script):
                        raise IndexError

                    n += dlen

        except Exception as e:
            raise ScriptError('get_push_input_refs script') from None

        return all_refs, normal_refs, singleton_refs
    
    @classmethod 
    def dedup_refs(cls, refs_list):
        dedup = {}
        for ref in refs_list: 
            dedup[ref] = ref
        return dedup


    @classmethod
    def zero_refs(cls, script):
        ops = bytearray()
        requires_sig = False

        # The unpacks or script[n] below throw on truncated scripts
        try:
            n = 0
            dlen = 0
            while n < len(script):
                op = script[n]
                ops.append(op)
                n += 1

                # Refs are only zeroed when a check sig opcode is used
                if op in CHECKSIG_OPS:
                    requires_sig = True

                elif op <= OpCodes.OP_PUSHDATA4:
                    # Raw bytes follow
                    if op < OpCodes.OP_PUSHDATA1:
                        dlen = op
                    elif op == OpCodes.OP_PUSHDATA1:
                        dlen = script[n]
                        n += 1
                    elif op == OpCodes.OP_PUSHDATA2:
                        dlen, = unpack_le_uint16_from(script[n: n + 2])
                        n += 2
                    elif op == OpCodes.OP_PUSHDATA4:
                        dlen, = unpack_le_uint32_from(script[n: n + 4])
                        n += 4
                    if n + dlen > len(script):
                        raise IndexError

                    ops.extend(script[n:n + dlen])
                    n += dlen

                elif op in INPUT_REF_OPS:
                    dlen = 36  # Grab 36 bytes

                    if n + dlen > len(script):
                        raise IndexError

                    ops.extend(bytes(36))
                    n += dlen

        except Exception:
            # Truncated script; e.g. tx_hash
            # ebc9fa1196a59e192352d76c0f6e73167046b9d37b8302b6bb6968dfd279b767
            raise ScriptError('truncated script') from None

        if requires_sig:
            return bytes(ops)
        return script

    @classmethod
    def _walk_ops(cls, script):
        '''Yield (opcode, data_len, op_start, op_end) for each opcode in the
        script, consuming push operands and 36-byte input-ref operands so that
        operand bytes are never mis-parsed as opcodes. Raises on truncation.'''
        ops = []
        n = 0
        length = len(script)
        while n < length:
            start = n
            op = script[n]
            n += 1
            if op <= OpCodes.OP_PUSHDATA4:
                if op < OpCodes.OP_PUSHDATA1:
                    dlen = op
                elif op == OpCodes.OP_PUSHDATA1:
                    dlen = script[n]; n += 1
                elif op == OpCodes.OP_PUSHDATA2:
                    dlen, = unpack_le_uint16_from(script[n:n + 2]); n += 2
                else:
                    dlen, = unpack_le_uint32_from(script[n:n + 4]); n += 4
                n += dlen
                if n > length:
                    raise IndexError('truncated push')
                ops.append((op, dlen, start, n))
            elif op in INPUT_REF_OPS:
                n += 36
                if n > length:
                    raise IndexError('truncated ref')
                ops.append((op, 36, start, n))
            else:
                ops.append((op, 0, start, n))
        return ops

    @classmethod
    def base_locking_script(cls, script):
        '''Return the owner's base locking script (the spendable P2PKH/P2SH)
        embedded in a Radiant token output.

        Token scripts wrap a standard pay-to-address with Glyph/token machinery,
        and the address sits in different places depending on the contract:

            NFT:  OP_PUSHINPUTREFSINGLETON <ref> OP_DROP <P2PKH>      (address last)
            FT:   <P2PKH> OP_STATESEPARATOR OP_PUSHINPUTREF <ref> ...  (address first)

        For per-address ownership we want the hashX/scripthash of the underlying
        address so a wallet can list the tokens it holds using its normal address
        scripthash. We therefore locate the standard P2PKH (or P2SH) template
        wherever it appears — walking opcodes so ref/data bytes can't produce a
        false match — and return just that sub-script. Falls back to stripping a
        leading input-ref preamble (and then the whole script) for exotic
        non-standard scripts; credit/debit symmetry only needs the value to be
        computed identically at create and spend time (the b'rb' side table
        stores it verbatim).
        '''
        try:
            ops = cls._walk_ops(script)
        except Exception:
            return script

        # P2PKH: OP_DUP OP_HASH160 <push20> OP_EQUALVERIFY OP_CHECKSIG
        for i in range(len(ops) - 4):
            if (ops[i][0] == OpCodes.OP_DUP
                    and ops[i + 1][0] == OpCodes.OP_HASH160
                    and ops[i + 2][0] == 0x14 and ops[i + 2][1] == 20
                    and ops[i + 3][0] == OpCodes.OP_EQUALVERIFY
                    and ops[i + 4][0] == OpCodes.OP_CHECKSIG):
                return script[ops[i][2]:ops[i + 4][3]]
        # P2SH: OP_HASH160 <push20> OP_EQUAL
        for i in range(len(ops) - 2):
            if (ops[i][0] == OpCodes.OP_HASH160
                    and ops[i + 1][0] == 0x14 and ops[i + 1][1] == 20
                    and ops[i + 2][0] == OpCodes.OP_EQUAL):
                return script[ops[i][2]:ops[i + 2][3]]

        # Fallback: strip a leading input-ref preamble (+ DROP/2DROP), then
        # return the remainder (or the whole script if nothing remains).
        try:
            n = 0
            length = len(script)
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

    @classmethod
    def push_data(cls, data):
        '''Returns the opcodes to push the data on the stack.'''
        assert isinstance(data, (bytes, bytearray))

        n = len(data)
        if n < OpCodes.OP_PUSHDATA1:
            return bytes([n]) + data
        if n < 256:
            return bytes([OpCodes.OP_PUSHDATA1, n]) + data
        if n < 65536:
            return bytes([OpCodes.OP_PUSHDATA2]) + pack_le_uint16(n) + data
        return bytes([OpCodes.OP_PUSHDATA4]) + pack_le_uint32(n) + data

    @classmethod
    def opcode_name(cls, opcode):
        if OpCodes.OP_0 < opcode < OpCodes.OP_PUSHDATA1:
            return 'OP_{:d}'.format(opcode)
        try:
            return OpCodes.whatis(opcode)
        except KeyError:
            return 'OP_UNKNOWN:{:d}'.format(opcode)

    @classmethod
    def dump(cls, script):
        opcodes, datas = cls.get_ops(script)
        for opcode, data in zip(opcodes, datas):
            name = cls.opcode_name(opcode)
            if data is None:
                print(name)
            else:
                print('{} {} ({:d} bytes)'
                      .format(name, data.hex(), len(data)))
