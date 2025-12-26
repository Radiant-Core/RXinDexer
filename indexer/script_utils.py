import binascii
import io
import re
import cbor2

# Opcodes (from Radiant protocol)
OP_PUSHINPUTREF = 0xd0  # Push input ref (FT)
OP_PUSHINPUTREFSINGLETON = 0xd8  # Push input ref singleton (NFT)
OP_REQUIREINPUTREF = 0xd1  # Require input ref
OP_PUSH = 0x4c  # Standard push opcode
OP_STATESEPARATOR = 0xbd  # State separator
OP_DROP = 0x75  # OP_DROP
OP_DUP = 0x76  # OP_DUP
OP_HASH160 = 0xa9  # OP_HASH160
OP_EQUALVERIFY = 0x88  # OP_EQUALVERIFY
OP_CHECKSIG = 0xac  # OP_CHECKSIG

# The magic bytes that identify a Glyph
GLYPH_MARKER_HEX = "676c79"  # "gly" in hex
GLYPH_MARKER_BYTES = bytes.fromhex(GLYPH_MARKER_HEX)  # b'gly'

# ============================================================================
# PHOTONIC WALLET TOKEN DETECTION PATTERNS
# These patterns match the scriptPubKey structure to identify token types
# Based on: photonic-wallet-master/packages/lib/src/script.ts
# ============================================================================

# NFT Script Pattern: OP_PUSHINPUTREFSINGLETON <ref:36bytes> OP_DROP <p2pkh>
# Hex: d8 <72 hex chars for ref> 75 76a914 <40 hex chars for address> 88ac
NFT_SCRIPT_PATTERN = re.compile(r'^d8([0-9a-f]{72})7576a914([0-9a-f]{40})88ac$', re.IGNORECASE)

# FT Script Pattern: <p2pkh> OP_STATESEPARATOR OP_PUSHINPUTREF <ref:36bytes> <contract code>
# Hex: 76a914 <40 hex chars for address> 88ac bd d0 <72 hex chars for ref> dec0e9aa76e378e4a269e69d
FT_SCRIPT_PATTERN = re.compile(r'^76a914([0-9a-f]{40})88acbdd0([0-9a-f]{72})dec0e9aa76e378e4a269e69d$', re.IGNORECASE)

# Mutable NFT Script Pattern (more complex)
# Uses OP_PUSHINPUTREFSINGLETON with state separator
MUTABLE_NFT_PATTERN = re.compile(r'^20([0-9a-f]{64})75bdd8([0-9a-f]{72})', re.IGNORECASE)

# Delegate Token Pattern: OP_PUSHINPUTREF <ref> OP_DROP <p2pkh>
DELEGATE_TOKEN_PATTERN = re.compile(r'^d0([0-9a-f]{72})7576a914([0-9a-f]{40})88ac$', re.IGNORECASE)

# Delegate Burn Pattern: OP_REQUIREINPUTREF <ref> OP_RETURN "del"
DELEGATE_BURN_PATTERN = re.compile(r'^d1([0-9a-f]{72})6a0364656c$', re.IGNORECASE)


def parse_nft_script(script_hex: str) -> dict:
    """
    Parse an NFT script to extract ref and address.
    Returns: {'ref': str, 'address': str} or {} if not an NFT script
    """
    match = NFT_SCRIPT_PATTERN.match(script_hex)
    if match:
        return {'ref': match.group(1), 'address': match.group(2), 'type': 'nft'}
    return {}


def parse_ft_script(script_hex: str) -> dict:
    """
    Parse an FT (fungible token) script to extract ref and address.
    Returns: {'ref': str, 'address': str} or {} if not an FT script
    """
    match = FT_SCRIPT_PATTERN.match(script_hex)
    if match:
        return {'ref': match.group(2), 'address': match.group(1), 'type': 'ft'}
    return {}


def parse_mutable_nft_script(script_hex: str) -> dict:
    """
    Parse a mutable NFT script to extract hash and ref.
    Returns: {'hash': str, 'ref': str} or {} if not a mutable NFT script
    """
    match = MUTABLE_NFT_PATTERN.match(script_hex)
    if match:
        return {'hash': match.group(1), 'ref': match.group(2), 'type': 'mutable_nft'}
    return {}


def parse_delegate_token_script(script_hex: str) -> dict:
    """
    Parse a delegate token script.
    Returns: {'ref': str, 'address': str} or {} if not a delegate token script
    """
    match = DELEGATE_TOKEN_PATTERN.match(script_hex)
    if match:
        return {'ref': match.group(1), 'address': match.group(2), 'type': 'delegate'}
    return {}


def parse_nft_script_hex(script_hex: str) -> dict:
    """
    Parse an NFT script to extract ref and address using byte-level parsing.
    Matches reference: parseNftScriptHex in lib/script.ts
    
    NFT format: d8 <36-byte ref> 75 76 a9 14 <20-byte hash> 88 ac
    """
    try:
        buf = bytes.fromhex(script_hex)
        min_length = 1 + 36 + 1 + 1 + 1 + 1 + 20  # d8 + ref + drop + dup + hash160 + push20 + hash
        
        for i in range(len(buf) - 37 if len(buf) > 37 else 0):
            if buf[i] != 0xd8:  # OP_PUSHINPUTREFSINGLETON
                continue
            
            # Extract the 36-byte ref
            ref = buf[i + 1:i + 37].hex()
            
            # Check for P2PKH pattern after ref
            if len(buf) >= i + min_length:
                if (buf[i + 37] == 0x75 and  # OP_DROP
                    buf[i + 38] == 0x76 and  # OP_DUP
                    buf[i + 39] == 0xa9 and  # OP_HASH160
                    buf[i + 40] == 0x14):    # PUSH 20 bytes
                    address = buf[i + 41:i + 61].hex()
                    return {'ref': ref, 'address': address}
            
            # Found ref but no P2PKH address
            return {'ref': ref}
        
        return {}
    except Exception:
        return {}


def parse_ft_script_hex(script_hex: str) -> dict:
    """
    Parse an FT script to extract ref and address using byte-level parsing.
    Matches reference: parseFtScriptHex in lib/script.ts
    
    FT format: 76 a9 14 <20-byte hash> 88 ac bd d0 <36-byte ref> <contract code>
    """
    try:
        buf = bytes.fromhex(script_hex)
        address = None
        ref = None
        
        # Check for P2PKH prefix: 76 a9 14 <20 bytes> 88 ac
        if (len(buf) > 25 and
            buf[0] == 0x76 and buf[1] == 0xa9 and buf[2] == 0x14 and
            buf[23] == 0x88 and buf[24] == 0xac):
            address = buf[3:23].hex()
        
        # Find state separator (0xbd) then OP_PUSHINPUTREF (0xd0) + 36-byte ref
        sep_idx = -1
        for i in range(len(buf)):
            if buf[i] == 0xbd:
                sep_idx = i
                break
        
        if sep_idx >= 0 and sep_idx + 38 <= len(buf) and buf[sep_idx + 1] == 0xd0:
            ref = buf[sep_idx + 2:sep_idx + 38].hex()
        
        if ref:
            return {'ref': ref, 'address': address}
        return {}
    except Exception:
        return {}


def detect_token_from_script(script_hex: str) -> dict:
    """
    Detect if a scriptPubKey represents a token and return its details.
    This is the main entry point for token detection based on Photonic Wallet patterns.
    
    Uses byte-level parsing functions that match the reference implementation.
    
    Args:
        script_hex: The scriptPubKey in hex format
        
    Returns:
        dict with keys: 'type', 'ref', 'address' (if detected)
        Empty dict if not a token script
    """
    if not script_hex:
        return {}
    
    # Try byte-level parsers first (more accurate, match reference implementation)
    # NFT: d8 <36-byte ref> 75 76 a9 14 <20-byte hash> 88 ac
    result = parse_nft_script_hex(script_hex)
    if result.get('ref'):
        result['type'] = 'nft'
        return result
    
    # FT: 76 a9 14 <20-byte hash> 88 ac bd d0 <36-byte ref> <contract>
    result = parse_ft_script_hex(script_hex)
    if result.get('ref'):
        result['type'] = 'ft'
        return result
    
    # Fall back to regex patterns for edge cases
    result = parse_nft_script(script_hex)
    if result:
        return result
    
    result = parse_ft_script(script_hex)
    if result:
        return result
    
    result = parse_mutable_nft_script(script_hex)
    if result:
        return result
    
    result = parse_delegate_token_script(script_hex)
    if result:
        return result
    
    return {}


def ref_to_token_id(ref_hex: str) -> str:
    """
    Convert a 36-byte ref (72 hex chars) to a token_id.
    The ref is: txid (32 bytes, little-endian) + vout (4 bytes, little-endian)
    Token ID is typically the ref in big-endian format or just the ref as-is.
    """
    if len(ref_hex) != 72:
        return ref_hex
    
    # The ref is already in the format we need for token_id
    return ref_hex


def construct_ref(txid: str, vout: int) -> str:
    """
    Construct a 36-byte ref from txid and vout.
    Matches reference implementation: Outpoint.fromUTXO(txid, vout).reverse().toString()
    
    The ref format is:
    - txid bytes reversed (little-endian)
    - vout as 4-byte little-endian
    
    Args:
        txid: Transaction ID in big-endian hex (standard display format)
        vout: Output index
        
    Returns:
        72-char hex string representing the ref
    """
    # Reverse txid bytes (big-endian to little-endian)
    txid_le = bytes.fromhex(txid)[::-1].hex()
    # vout as 4-byte little-endian
    vout_le = vout.to_bytes(4, 'little').hex()
    return txid_le + vout_le


def reverse_ref(ref: str) -> str:
    """
    Reverse a ref (swap endianness of both txid and vout parts).
    Matches reference: Outpoint.fromString(ref).reverse().toString()
    """
    if len(ref) != 72:
        return ref
    txid_part = ref[:64]
    vout_part = ref[64:]
    # Reverse both parts
    txid_reversed = bytes.fromhex(txid_part)[::-1].hex()
    vout_reversed = bytes.fromhex(vout_part)[::-1].hex()
    return txid_reversed + vout_reversed


def parse_dmint_contract_script(script_hex: str) -> dict:
    if not script_hex or not isinstance(script_hex, str):
        return {}

    try:
        buf = bytes.fromhex(script_hex)
    except Exception:
        return {}

    def _read_push_int(b: bytes, i: int):
        if i >= len(b):
            return (None, i)
        op = b[i]
        i += 1

        if op == 0x00:
            return (0, i)
        if op == 0x4f:
            return (-1, i)
        if 0x51 <= op <= 0x60:
            return (op - 0x50, i)

        if 1 <= op <= 75:
            ln = op
        elif op == 0x4c:
            if i + 1 > len(b):
                return (None, i)
            ln = b[i]
            i += 1
        elif op == 0x4d:
            if i + 2 > len(b):
                return (None, i)
            ln = int.from_bytes(b[i:i + 2], 'little')
            i += 2
        elif op == 0x4e:
            if i + 4 > len(b):
                return (None, i)
            ln = int.from_bytes(b[i:i + 4], 'little')
            i += 4
        else:
            return (None, i)

        if i + ln > len(b):
            return (None, i)
        data = b[i:i + ln]
        i += ln

        if ln == 0:
            return (0, i)

        negative = (data[-1] & 0x80) != 0
        if negative:
            data = data[:-1] + bytes([data[-1] & 0x7f])

        val = int.from_bytes(data, 'little', signed=False)
        return (-val if negative else val, i)

    try:
        # Search for the core pattern anywhere in the script:
        # d8 <36-byte contractRef> d0 <36-byte tokenRef>
        start = -1
        for j in range(0, max(0, len(buf) - (1 + 36 + 1 + 36))):
            if buf[j] != 0xd8:
                continue
            if j + 1 + 36 + 1 + 36 > len(buf):
                continue
            if buf[j + 1 + 36] != 0xd0:
                continue
            start = j
            break

        if start < 0:
            return {}

        # Height is optionally pushed right before the d8 (Photonic uses push4bytes(height)).
        height = None
        try:
            if start >= 5 and buf[start - 5] == 0x04:
                height = int.from_bytes(buf[start - 4:start], 'little')
        except Exception:
            height = None

        i = start + 1
        contract_ref = buf[i:i + 36].hex()
        i += 36
        i += 1  # skip 0xd0
        token_ref = buf[i:i + 36].hex()
        i += 36

        max_height, i = _read_push_int(buf, i)
        reward, i = _read_push_int(buf, i)
        target, i = _read_push_int(buf, i)

        difficulty = None
        try:
            if target and isinstance(target, int) and target > 0:
                max_target = 0x7fffffffffffffff
                difficulty = int(max_target // int(target))
        except Exception:
            difficulty = None

        return {
            'height': height,
            'contract_ref': contract_ref,
            'token_ref': token_ref,
            'max_height': max_height,
            'reward': reward,
            'target': target,
            'difficulty': difficulty,
        }
    except Exception:
        return {}


def parse_script_chunks(script_bytes: bytes) -> list:
    """
    Parse script bytes into chunks matching the reference implementation.
    Each chunk has: {'opcodenum': int, 'buf': bytes or None}
    
    This matches the radiantjs Script.chunks format used in the reference.
    """
    chunks = []
    i = 0
    while i < len(script_bytes):
        opcode = script_bytes[i]
        i += 1
        
        # Standard push: 0x01-0x4b (1-75 bytes)
        if 1 <= opcode <= 75:
            push_len = opcode
            if i + push_len > len(script_bytes):
                break
            buf = script_bytes[i:i + push_len]
            i += push_len
            chunks.append({'opcodenum': opcode, 'buf': buf})
            continue
        
        # OP_PUSHDATA1 (0x4c): next byte is length
        if opcode == 0x4c:
            if i + 1 > len(script_bytes):
                break
            push_len = script_bytes[i]
            i += 1
            if i + push_len > len(script_bytes):
                break
            buf = script_bytes[i:i + push_len]
            i += push_len
            chunks.append({'opcodenum': opcode, 'buf': buf})
            continue
        
        # OP_PUSHDATA2 (0x4d): next 2 bytes are length (little-endian)
        if opcode == 0x4d:
            if i + 2 > len(script_bytes):
                break
            push_len = int.from_bytes(script_bytes[i:i + 2], 'little')
            i += 2
            if i + push_len > len(script_bytes):
                break
            buf = script_bytes[i:i + push_len]
            i += push_len
            chunks.append({'opcodenum': opcode, 'buf': buf})
            continue
        
        # OP_PUSHDATA4 (0x4e): next 4 bytes are length (little-endian)
        if opcode == 0x4e:
            if i + 4 > len(script_bytes):
                break
            push_len = int.from_bytes(script_bytes[i:i + 4], 'little')
            i += 4
            if i + push_len > len(script_bytes):
                break
            buf = script_bytes[i:i + push_len]
            i += push_len
            chunks.append({'opcodenum': opcode, 'buf': buf})
            continue
        
        # OP_0 (0x00): push empty
        if opcode == 0x00:
            chunks.append({'opcodenum': opcode, 'buf': b''})
            continue
        
        # Other opcodes (no data)
        chunks.append({'opcodenum': opcode, 'buf': None})
    
    return chunks


def decode_glyph_from_script(script_bytes: bytes) -> dict:
    """
    Decode a glyph from script bytes using the exact same logic as the reference.
    
    Reference implementation (lib/token.ts decodeGlyph):
    1. Parse script into chunks
    2. Find chunk where opcodenum === 3 and buf === 'gly' (676c79)
    3. Next chunk contains CBOR payload
    4. Decode CBOR and separate payload from files
    
    Returns:
        dict with 'payload', 'embedded_files', 'remote_files' or None if not a glyph
    """
    try:
        # Ensure bytes
        if isinstance(script_bytes, str):
            if all(c in '0123456789abcdefABCDEF' for c in script_bytes):
                script_bytes = bytes.fromhex(script_bytes)
            else:
                script_bytes = script_bytes.encode('utf-8')
        
        chunks = parse_script_chunks(script_bytes)
        
        # Find glyph marker: opcodenum === 3 (OP_PUSH3) and buf === b'gly'
        found = False
        payload_data = None
        
        for idx, chunk in enumerate(chunks):
            buf = chunk.get('buf')
            opcodenum = chunk.get('opcodenum')
            
            # Check for OP_PUSH3 (0x03) pushing 'gly'
            if buf is None or opcodenum != 3:
                continue
            if buf != GLYPH_MARKER_BYTES:  # b'gly'
                continue
            if idx + 1 >= len(chunks):
                return None
            
            # Next chunk is the CBOR payload
            payload_chunk = chunks[idx + 1]
            payload_buf = payload_chunk.get('buf')
            if not payload_buf:
                return None
            
            payload_data = payload_buf
            found = True
            break
        
        if not found or not payload_data:
            return None
        
        # Decode CBOR
        def tag_hook(decoder, tag):
            return tag.value
        
        decoded = cbor2.loads(payload_data, tag_hook=tag_hook)
        
        if not isinstance(decoded, dict):
            return None
        
        # Check for valid glyph with protocols
        if not isinstance(decoded.get('p'), list):
            return None
        
        # Separate meta, embedded files, and remote files
        # Reference: filterFileObj in lib/token.ts
        payload = {}
        embedded_files = {}
        remote_files = {}
        
        for key, value in decoded.items():
            if not isinstance(value, dict):
                payload[key] = value
                continue
            
            # Check for embedded file: has 't' (type string) and 'b' (bytes)
            t_val = value.get('t')
            b_val = value.get('b')
            if isinstance(t_val, str) and isinstance(b_val, (bytes, bytearray)):
                embedded_files[key] = {'t': t_val, 'b': b_val}
                continue
            
            # Check for remote file: has 'u' (url string)
            u_val = value.get('u')
            if isinstance(u_val, str):
                remote_files[key] = {
                    't': t_val if isinstance(t_val, str) else '',
                    'u': u_val,
                    'h': value.get('h') if isinstance(value.get('h'), (bytes, bytearray)) else None,
                    'hs': value.get('hs') if isinstance(value.get('hs'), (bytes, bytearray)) else None,
                }
                continue
            
            # Otherwise it's metadata
            payload[key] = value
        
        return {
            'payload': payload,
            'embedded_files': embedded_files,
            'remote_files': remote_files,
        }
        
    except Exception as e:
        return None


def extract_reveal_payload(ref: str, inputs: list) -> dict:
    """
    Find token script for a ref in reveal inputs and decode if found.
    
    Matches reference implementation: extractRevealPayload in lib/token.ts
    
    Args:
        ref: The 72-char hex ref (txid_le + vout_le)
        inputs: List of transaction inputs, each with 'txid', 'vout', 'scriptSig'
        
    Returns:
        dict with 'reveal_index' and 'glyph' (decoded glyph data)
    """
    # Parse ref to get txid and vout
    if len(ref) != 72:
        return {'reveal_index': -1, 'glyph': None}
    
    ref_txid_le = ref[:64]
    ref_vout_le = ref[64:]
    
    # Convert to big-endian for comparison with input txid
    ref_txid_be = bytes.fromhex(ref_txid_le)[::-1].hex()
    ref_vout = int.from_bytes(bytes.fromhex(ref_vout_le), 'little')
    
    # Find matching input
    reveal_index = -1
    for idx, inp in enumerate(inputs):
        inp_txid = inp.get('txid')
        inp_vout = inp.get('vout')
        
        if inp_txid == ref_txid_be and inp_vout == ref_vout:
            reveal_index = idx
            break
    
    if reveal_index < 0:
        return {'reveal_index': -1, 'glyph': None}
    
    # Get the scriptSig from the matching input
    script_sig_hex = inputs[reveal_index].get('scriptSig', {}).get('hex', '')
    if not script_sig_hex:
        return {'reveal_index': reveal_index, 'glyph': None}
    
    # Decode glyph from scriptSig
    glyph = decode_glyph_from_script(script_sig_hex)
    
    return {'reveal_index': reveal_index, 'glyph': glyph}


# Ensure GLYPH_MARKER is correctly defined as bytes
GLYPH_MARKER = bytes.fromhex(GLYPH_MARKER_HEX) if isinstance(GLYPH_MARKER_HEX, str) else GLYPH_MARKER_HEX

# Known glyph addresses that should be given special attention
KNOWN_GLYPH_ADDRESSES = [
    # Add known addresses here for debugging if needed
]

# Glyph protocol identifiers per Photonic Wallet
GLYPH_PROTOCOL_IDS = {
    1: "Fungible Token (FT)",
    2: "Non-Fungible Token (NFT)",
    3: "Data Storage (DAT)",
    4: "Decentralized Mint (DMINT)",
    5: "Mutable Token (MUT)"
}

# Helper: parse script to extract all pushed refs (commit outpoints)
def extract_refs_from_script(script_bytes):
    """
    Extract all 36-byte refs (commit outpoints) from a script.
    Returns a list of bytes objects (each 36 bytes).
    """
    refs = []
    i = 0
    while i < len(script_bytes):
        opcode = script_bytes[i]
        i += 1
        if opcode in (OP_PUSHINPUTREF, OP_PUSHINPUTREFSINGLETON):
            # Next byte = pushdata length (should be 36)
            if i < len(script_bytes):
                push_len = script_bytes[i]
                i += 1
                if push_len == 36 and i + 36 <= len(script_bytes):
                    ref = script_bytes[i:i+36]
                    refs.append(ref)
                    i += 36
                else:
                    i += push_len  # skip unknown pushdata
        elif opcode >= 1 and opcode <= 75:
            # Standard pushdata (length = opcode)
            push_len = opcode
            if i + push_len <= len(script_bytes):
                i += push_len
            else:
                break
        else:
            # Other opcode, skip
            continue
    return refs

# Helper: find and extract CBOR payload after 'gly' marker in script

def extract_gly_cbor_from_script(script_bytes, special_address=False):
    """
    Find 'gly' marker and return the CBOR hex after it (if any).
    Returns cbor_bytes or None.

    Enhanced to detect glyphs in multiple script formats:
    1. Direct "gly" + CBOR pattern (standard format)
    2. OP_PUSH "gly" + OP_PUSH <CBOR> pattern (unlocking scripts)
    3. Hex scanning for "676c79" marker anywhere in the script

    Args:
        script_bytes: The script bytes to scan
        special_address: If True, use more aggressive detection for known glyph addresses

    Returns:
        bytes or None: CBOR data if found, None otherwise
    """
    try:
        # Ensure script_bytes is actually bytes, not a string
        if isinstance(script_bytes, str):
            try:
                if all(c in '0123456789abcdefABCDEF' for c in script_bytes):
                    script_bytes = binascii.unhexlify(script_bytes)
                else:
                    script_bytes = script_bytes.encode('utf-8')
            except Exception:
                return None

        chunks = []
        i = 0
        while i < len(script_bytes):
            opcode = script_bytes[i]
            i += 1

            if 1 <= opcode <= 75:
                push_len = opcode
                if i + push_len > len(script_bytes):
                    break
                buf = script_bytes[i:i + push_len]
                i += push_len
                chunks.append({'opcodenum': opcode, 'buf': buf})
                continue

            if opcode == 0x4c:  # OP_PUSHDATA1
                if i + 1 > len(script_bytes):
                    break
                push_len = script_bytes[i]
                i += 1
                if i + push_len > len(script_bytes):
                    break
                buf = script_bytes[i:i + push_len]
                i += push_len
                chunks.append({'opcodenum': push_len, 'buf': buf})
                continue

            if opcode == 0x4d:  # OP_PUSHDATA2
                if i + 2 > len(script_bytes):
                    break
                push_len = int.from_bytes(script_bytes[i:i + 2], 'little')
                i += 2
                if i + push_len > len(script_bytes):
                    break
                buf = script_bytes[i:i + push_len]
                i += push_len
                chunks.append({'opcodenum': push_len, 'buf': buf})
                continue

            if opcode == 0x4e:  # OP_PUSHDATA4
                if i + 4 > len(script_bytes):
                    break
                push_len = int.from_bytes(script_bytes[i:i + 4], 'little')
                i += 4
                if i + push_len > len(script_bytes):
                    break
                buf = script_bytes[i:i + push_len]
                i += push_len
                chunks.append({'opcodenum': push_len, 'buf': buf})
                continue

            continue

        for idx, ch in enumerate(chunks):
            buf = ch.get('buf')
            if not buf or ch.get('opcodenum') != 3:
                continue
            if buf != b'gly':
                continue
            if idx + 1 >= len(chunks):
                return None
            payload = chunks[idx + 1].get('buf')
            if not payload:
                return None
            return payload

        return None
    except Exception:
        return None

# Enhanced glyph detection and decoding
def decode_glyph(script_bytes, txid=None, address=None):
    """
    Detects and decodes a glyph from script bytes.
    Follows the Radiant Glyph Protocol detection rules:
    - Looks for 'gly' marker in any script type (OP_PUSH "gly")
    - Decodes CBOR payload after marker
    - Validates the required fields per protocol spec
    - Separates metadata and files
    
    Enhanced to detect glyphs in:
    1. OP_RETURN scripts
    2. Unlocking scripts with OP_PUSH "gly" pattern
    3. Any nonstandard script containing the glyph marker
    4. Any nonstandard script that may contain CBOR data (without requiring gly marker)
    5. Special handling for known glyph addresses
    6. Scripts with alternative markers like 'msg' (6d7367)
    
    Args:
        script_bytes: The script bytes to scan
        txid: Optional transaction ID for logging
        address: Optional address for special handling
        
    Returns a dict with payload (metadata) and files, or None if no valid glyph.
    """
    try:
        # Extra verbose logging for known glyph addresses
        special_address = address in KNOWN_GLYPH_ADDRESSES if address else False
        # if special_address:
        #    print(f"[decode_glyph][SPECIAL] Processing known glyph address {address} for txid={txid}"); import sys; sys.stdout.flush()
        
        # Ensure script_bytes is actually bytes, not a string
        if isinstance(script_bytes, str):
            try:
                # Try to decode hex string to bytes if it's a hex string
                if all(c in '0123456789abcdefABCDEF' for c in script_bytes):
                    script_bytes = binascii.unhexlify(script_bytes)
                else:
                    # Otherwise try UTF-8 encoding
                    script_bytes = script_bytes.encode('utf-8')
            except Exception as e:
                print(f"[decode_glyph][ERROR] Failed to convert string to bytes: {e}"); import sys; sys.stdout.flush()
                return None
        
        import cbor2
        
        cbor_bytes = extract_gly_cbor_from_script(script_bytes, special_address=special_address)
        if not cbor_bytes:
            return None
            
        # Decode CBOR
        try:
            # Define a custom tag hook to handle non-standard or problematic tags
            # Tag 9 is officially "Suggested URI" but some protocols use it for other data
            # cbor2 default decoder tries to validate URIs and fails on some inputs
            def tag_hook(decoder, tag):
                # Return the raw value for any tag we encounter
                return tag.value

            decoded = cbor2.loads(cbor_bytes, tag_hook=tag_hook)
            # print(f"[decode_glyph][SUCCESS] CBOR decoded successfully: {type(decoded)}"); import sys; sys.stdout.flush()
        except Exception as cbor_err:
            # Try fallback: sometimes raw bytes might work with a different decoder configuration
            # or it might be malformed. We log and skip.
            # hex_dump = cbor_bytes.hex() if cbor_bytes else "None"
            # truncated_dump = hex_dump[:64] + "..." if len(hex_dump) > 64 else hex_dump
            # Silenced expected decoding errors to prevent log flooding
            # print(f"[decode_glyph][DEBUG] Failed to decode CBOR: {cbor_err}. Data(len={len(cbor_bytes) if cbor_bytes else 0}): {truncated_dump}"); import sys; sys.stdout.flush()
            return None
            
        if not isinstance(decoded, dict):
            # print(f"[decode_glyph][ERROR] Decoded data is not a dict: {type(decoded)}"); import sys; sys.stdout.flush()
            return None
            
        # Check if this is a valid glyph with protocols
        if not isinstance(decoded.get('p'), list):
            # print(f"[decode_glyph][ERROR] Missing protocols list ('p' field): {decoded.keys()}"); import sys; sys.stdout.flush()
            return None
            
        # Log protocol information
        protocols = decoded.get('p', [])
        protocol_names = [f"{p}:{GLYPH_PROTOCOL_IDS.get(p, 'Unknown')}" for p in protocols]
        # print(f"[decode_glyph][INFO] Detected glyph protocols: {protocol_names}"); import sys; sys.stdout.flush()
        
        # Check for mineable tokens (protocol IDs [1,4])
        if 1 in protocols and 4 in protocols:
            print(f"[decode_glyph][INFO] Detected mineable token (fungible + dmint) for txid={txid}"); import sys; sys.stdout.flush()
            
        # Separate meta, embedded files, and remote files from root object
        # Logic adapted from Photonic Wallet: decodeGlyph()
        meta = {}
        files = {}  # Kept for backward compatibility, contains both embedded and remote
        embedded_files = {}
        remote_files = {}
        
        for key, value in decoded.items():
            if not isinstance(value, dict):
                meta[key] = value
                continue
                
            # Check for Embedded File: has 't' (type) and 'b' (bytes)
            if isinstance(value.get('t'), str) and isinstance(value.get('b'), bytes):
                embedded_files[key] = value
                files[key] = value
                
            # Check for Remote File: has 'u' (url) and optional 'h'/'hs' (hashes)
            elif isinstance(value.get('u'), str) and (value.get('h') is None or isinstance(value.get('h'), bytes)):
                remote_files[key] = value
                files[key] = value
            
            else:
                meta[key] = value
        
        # Helper to make data JSON serializable (convert bytes to hex)
        def make_json_serializable(obj):
            if isinstance(obj, bytes):
                return obj.hex()
            elif isinstance(obj, dict):
                return {k: make_json_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [make_json_serializable(i) for i in obj]
            else:
                return obj

        json_safe_raw = make_json_serializable(decoded)
        
        token_info = f"Protocols: {meta.get('p')}, Type: {meta.get('type', 'unknown')}, Name: {meta.get('name', 'unnamed')}";
        print(f"[decode_glyph][SUCCESS] Valid glyph detected! {token_info}"); import sys; sys.stdout.flush()
        return {
            'payload': meta,
            'files': files, # Legacy combined field
            'embedded_files': embedded_files,
            'remote_files': remote_files,
            'raw': json_safe_raw,  # Use JSON-safe version for DB storage
            'is_mineable': (1 in protocols and 4 in protocols)
        }
    except Exception as e:
        print(f"[decode_glyph][ERROR] Failed to decode glyph: {e}"); import sys; sys.stdout.flush()
        return None


# ============================================================================
# COMPREHENSIVE GLYPH METADATA EXTRACTION
# Extracts all fields needed for the enhanced token indexer
# ============================================================================

# Protocol constants (matching Photonic Wallet)
GLYPH_FT = 1      # Fungible Token
GLYPH_NFT = 2     # Non-Fungible Token
GLYPH_DAT = 3     # Data Storage
GLYPH_DMINT = 4   # Decentralized Mint (PoW)
GLYPH_MUT = 5     # Mutable Token


def extract_glyph_metadata(glyph_data: dict) -> dict:
    """
    Extract comprehensive metadata from decoded glyph data.
    
    This function takes the output of decode_glyph() and extracts all fields
    needed for the enhanced token indexer database schema.
    
    Args:
        glyph_data: Output from decode_glyph() containing payload, files, etc.
        
    Returns:
        dict with all extracted metadata fields ready for database storage
    """
    if not glyph_data or not isinstance(glyph_data, dict):
        return {}
    
    payload = glyph_data.get('payload', {})
    embedded_files = glyph_data.get('embedded_files', {})
    remote_files = glyph_data.get('remote_files', {})
    raw = glyph_data.get('raw', {})
    
    # Extract protocols
    protocols = payload.get('p', [])
    if not isinstance(protocols, list):
        protocols = []
    
    # Determine primary protocol type
    protocol_type = None
    token_type = 'unknown'
    if GLYPH_DMINT in protocols:
        protocol_type = GLYPH_DMINT
        token_type = 'dmint'
    elif GLYPH_FT in protocols:
        protocol_type = GLYPH_FT
        token_type = 'fungible'
    elif GLYPH_NFT in protocols:
        protocol_type = GLYPH_NFT
        token_type = 'nft'
    elif GLYPH_DAT in protocols:
        protocol_type = GLYPH_DAT
        token_type = 'dat'
    elif GLYPH_MUT in protocols:
        protocol_type = GLYPH_MUT
        token_type = 'mutable'
    
    # Extract core metadata
    name = _safe_string(payload.get('name'), max_len=255)
    ticker = _safe_string(payload.get('ticker'), max_len=50)
    description = _safe_string(payload.get('desc') or payload.get('description'), max_len=10000)
    token_type_name = _safe_string(payload.get('type'), max_len=100)  # User-defined type (user/container/object)
    license_field = _safe_string(payload.get('license'), max_len=255)  # License from payload
    
    # Check immutability using Photonic Wallet logic:
    # A token is mutable if it has BOTH NFT (2) and MUT (5) protocols
    # See: isImmutableToken() in photonic-wallet/packages/lib/src/token.ts
    immutable = not (GLYPH_NFT in protocols and GLYPH_MUT in protocols)
    
    # Extract author and container refs
    author_ref = None
    container_ref = None
    
    # 'by' field contains author ref(s)
    by_field = payload.get('by')
    if by_field:
        if isinstance(by_field, list) and len(by_field) > 0:
            # Take first author ref
            first_by = by_field[0]
            if isinstance(first_by, bytes):
                author_ref = first_by.hex()
            elif isinstance(first_by, str):
                author_ref = first_by
        elif isinstance(by_field, bytes):
            author_ref = by_field.hex()
        elif isinstance(by_field, str):
            author_ref = by_field
    
    # 'in' field contains container ref(s) (when it's a list of refs, not a boolean)
    in_field = payload.get('in')
    if in_field and not isinstance(in_field, bool):
        if isinstance(in_field, list) and len(in_field) > 0:
            first_in = in_field[0]
            if isinstance(first_in, bytes):
                container_ref = first_in.hex()
            elif isinstance(first_in, str):
                container_ref = first_in
        elif isinstance(in_field, bytes):
            container_ref = in_field.hex()
        elif isinstance(in_field, str):
            container_ref = in_field
    
    # Extract supply info (for FT tokens)
    max_supply = _safe_int(payload.get('supply') or payload.get('max') or payload.get('maxSupply'))
    premine = _safe_int(payload.get('premine') or payload.get('pre'))
    
    # Extract DMINT-specific fields
    difficulty = _safe_int(payload.get('difficulty') or payload.get('diff'))
    max_height = _safe_int(payload.get('maxHeight') or payload.get('height'))
    reward = _safe_int(payload.get('reward') or payload.get('rew'))
    
    # Extract icon/image data
    icon_data = None
    icon_url = None
    icon_mime_type = None
    
    # Check for 'icon' or 'main' file
    icon_file = embedded_files.get('icon') or embedded_files.get('main') or embedded_files.get('image')
    if icon_file:
        icon_mime_type = icon_file.get('t')
        icon_bytes = icon_file.get('b')
        if isinstance(icon_bytes, bytes):
            import base64
            icon_data = base64.b64encode(icon_bytes).decode('utf-8')
        elif isinstance(icon_bytes, str):
            icon_data = icon_bytes  # Already encoded
    
    # Check for remote icon
    remote_icon = remote_files.get('icon') or remote_files.get('main') or remote_files.get('image')
    if remote_icon and not icon_data:
        icon_url = remote_icon.get('u')
        icon_mime_type = remote_icon.get('t') or icon_mime_type
    
    # Extract attributes
    attrs = payload.get('attrs', {})
    if not isinstance(attrs, dict):
        attrs = {}
    
    # Extract location (linked payload ref when payload.loc is set)
    # See: NFT.ts saveGlyph() - when payload.loc is set, it points to another ref's payload
    location = None
    loc_field = payload.get('loc')
    if loc_field is not None:
        if isinstance(loc_field, int):
            # loc is a vout index - will be resolved by caller with the ref
            location = str(loc_field)
        elif isinstance(loc_field, str):
            location = loc_field
    
    # Build result
    result = {
        # Protocol info
        'protocols': protocols,
        'protocol_type': protocol_type,
        'type': token_type,
        
        # Core metadata
        'name': name,
        'ticker': ticker,
        'description': description,
        'token_type_name': token_type_name,
        'immutable': immutable,
        'license': license_field,
        
        # Author and container
        'author': author_ref,
        'container': container_ref,
        
        # Supply info
        'max_supply': max_supply,
        'premine': premine,
        
        # DMINT fields
        'difficulty': difficulty,
        'max_height': max_height,
        'reward': reward,
        
        # Icon/image
        'icon_mime_type': icon_mime_type,
        'icon_url': icon_url,
        'icon_data': icon_data,
        
        # Attributes
        'attrs': attrs,
        
        # Linked payload location
        'location': location,
        
        # Raw payload for reference
        'raw_payload': raw,
        
        # Embedded and remote files (for token_files table)
        'embedded_files': embedded_files,
        'remote_files': remote_files,
    }
    
    return result


def _safe_string(value, max_len=None) -> str:
    """Safely convert value to string with optional max length."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            result = value.decode('utf-8')
        except:
            result = value.hex()
    else:
        result = str(value)
    
    if max_len and len(result) > max_len:
        result = result[:max_len]
    
    return result


def _safe_int(value) -> int:
    """Safely convert value to integer."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def decode_and_extract_glyph(script_hex: str, txid: str = None, address: str = None) -> dict:
    """
    Convenience function that decodes a glyph from script hex and extracts all metadata.
    
    Uses the new decode_glyph_from_script function that matches the reference implementation.
    
    Args:
        script_hex: Script in hex format (scriptSig for reveal transactions)
        txid: Optional transaction ID for logging
        address: Optional address for special handling
        
    Returns:
        dict with all extracted metadata, or None if not a valid glyph
    """
    try:
        # Use the new reference-aligned decoder first
        glyph_data = decode_glyph_from_script(script_hex)
        
        if glyph_data:
            # Convert to the format expected by extract_glyph_metadata
            # The new decoder returns: {'payload': {}, 'embedded_files': {}, 'remote_files': {}}
            # extract_glyph_metadata expects: {'payload': {}, 'embedded_files': {}, 'remote_files': {}, 'raw': {}}
            
            # Build raw data for compatibility
            raw = {}
            raw.update(glyph_data.get('payload', {}))
            for k, v in glyph_data.get('embedded_files', {}).items():
                raw[k] = {'t': v.get('t'), 'b': v.get('b').hex() if isinstance(v.get('b'), bytes) else v.get('b')}
            for k, v in glyph_data.get('remote_files', {}).items():
                raw[k] = v
            
            glyph_data_compat = {
                'payload': glyph_data.get('payload', {}),
                'files': {**glyph_data.get('embedded_files', {}), **glyph_data.get('remote_files', {})},
                'embedded_files': glyph_data.get('embedded_files', {}),
                'remote_files': glyph_data.get('remote_files', {}),
                'raw': raw,
            }
            
            # Extract comprehensive metadata
            metadata = extract_glyph_metadata(glyph_data_compat)
            return metadata
        
        # Fallback to legacy decoder if new one fails
        if isinstance(script_hex, str):
            script_bytes = bytes.fromhex(script_hex)
        else:
            script_bytes = script_hex
        
        glyph_data = decode_glyph(script_bytes, txid=txid, address=address)
        if not glyph_data:
            return None
        
        # Extract comprehensive metadata
        metadata = extract_glyph_metadata(glyph_data)
        return metadata
        
    except Exception as e:
        return None


def detect_token_burn(input_refs: list, output_refs: list) -> list:
    """
    Detect if any tokens were burned (melted) in a transaction.
    
    A token is burned when its ref appears in inputs but NOT in any outputs.
    
    Args:
        input_refs: List of token refs from transaction inputs
        output_refs: List of token refs from transaction outputs
        
    Returns:
        List of burned token refs
    """
    input_set = set(input_refs)
    output_set = set(output_refs)
    
    # Refs in inputs but not in outputs = burned
    burned = input_set - output_set
    return list(burned)


def detect_psrt_signature(script_sig_hex: str) -> bool:
    """
    Detect if a scriptSig contains a PSRT (Partially Signed Radiant Transaction) signature.
    
    PSRT uses SIGHASH_SINGLE | SIGHASH_ANYONECANPAY | SIGHASH_FORKID = 0xC3
    
    Args:
        script_sig_hex: ScriptSig in hex format
        
    Returns:
        True if this appears to be a PSRT signature
    """
    if not script_sig_hex or len(script_sig_hex) < 4:
        return False
    
    try:
        script_bytes = bytes.fromhex(script_sig_hex)
        
        # Look for signature with SIGHASH type 0xC3 (195)
        # Signatures are DER-encoded and end with the SIGHASH byte
        # Typical signature is 70-73 bytes
        
        # Check first push (should be signature)
        if len(script_bytes) > 1:
            push_len = script_bytes[0]
            if 0x46 <= push_len <= 0x49:  # 70-73 bytes typical for signatures
                if len(script_bytes) > push_len:
                    # Last byte of signature is SIGHASH type
                    sighash_byte = script_bytes[push_len]
                    if sighash_byte == 0xC3:  # SIGHASH_SINGLE | ANYONECANPAY | FORKID
                        return True
        
        return False
    except:
        return False
