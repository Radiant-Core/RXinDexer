"""
Glyph v2 Token Standard Support for ElectrumX

This module provides parsing and indexing support for Glyph v2 tokens
on the Radiant blockchain.

Reference: https://github.com/Radiant-Core/Glyph-Token-Standards
"""

import struct
from typing import Optional, Dict, Any, List, Tuple

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False

# Glyph magic bytes
GLYPH_MAGIC = b'gly'
GLYPH_MAGIC_HEX = '676c79'

# Protocol versions
class GlyphVersion:
    V1 = 0x01
    V2 = 0x02

# Protocol IDs
class GlyphProtocol:
    GLYPH_FT = 1         # Fungible Token
    GLYPH_NFT = 2        # Non-Fungible Token
    GLYPH_DAT = 3        # Data Storage
    GLYPH_DMINT = 4      # Decentralized Minting
    GLYPH_MUT = 5        # Mutable State
    GLYPH_BURN = 6       # Explicit Burn
    GLYPH_CONTAINER = 7  # Container/Collection
    GLYPH_ENCRYPTED = 8  # Encrypted Content
    GLYPH_TIMELOCK = 9   # Timelocked Reveal
    GLYPH_AUTHORITY = 10 # Issuer Authority
    GLYPH_WAVE = 11      # WAVE Naming


# Token types for indexing / API
class GlyphTokenType:
    UNKNOWN = 0
    FT = 1
    NFT = 2
    DAT = 3
    DMINT = 4
    WAVE = 5
    CONTAINER = 6
    AUTHORITY = 7

# Protocol names for logging/display
PROTOCOL_NAMES = {
    1: 'Fungible Token',
    2: 'Non-Fungible Token',
    3: 'Data Storage',
    4: 'Decentralized Minting',
    5: 'Mutable State',
    6: 'Burn',
    7: 'Container',
    8: 'Encrypted',
    9: 'Timelock',
    10: 'Authority',
    11: 'WAVE Name',
}

# Envelope flags
class EnvelopeFlags:
    HAS_CONTENT_ROOT = 1 << 0
    HAS_CONTROLLER = 1 << 1
    HAS_PROFILE_HINT = 1 << 2
    IS_REVEAL = 1 << 7

# dMint Algorithm IDs
class DmintAlgorithm:
    SHA256D = 0x00
    BLAKE3 = 0x01
    K12 = 0x02
    ARGON2ID_LIGHT = 0x03
    RANDOMX_LIGHT = 0x04

# DAA Mode IDs
class DaaMode:
    FIXED = 0x00
    EPOCH = 0x01
    ASERT = 0x02
    LWMA = 0x03
    SCHEDULE = 0x04


def contains_glyph_magic(data: bytes) -> bool:
    """Check if data contains Glyph magic bytes."""
    return GLYPH_MAGIC in data


def find_glyph_magic(data: bytes) -> int:
    """Find the position of Glyph magic bytes in data. Returns -1 if not found."""
    return data.find(GLYPH_MAGIC)


def parse_glyph_envelope(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse a Glyph envelope from raw script bytes.

    Handles all known on-chain formats for both v1 and v2 tokens:

    FORMAT 1 — v1 / v2 Style B  (scriptSig, 'gly' in its own push)
    ---------------------------------------------------------------
    v1:  ... OP_PUSHBYTES_3(03) 'gly'  <push>(CBOR_metadata) ...
    v2B: ... OP_3(53) OP_PUSHBYTES_3(03) 'gly' <push>(data) ...

    The 'gly' magic is a standalone 3-byte data push.
    The NEXT push is either:
      • Raw CBOR metadata dict  → reveal  (v1 or v2 Style B reveal)
      • Version+flags+...       → v2 Style B commit

    FORMAT 2 — v2 Style A  (OP_RETURN output, 'gly' concatenated)
    ---------------------------------------------------------------
    Commit:  OP_RETURN <push>('gly' || V2 || flags || commit_hash [...])
    Reveal:  OP_RETURN <push>('gly' || V2 || flags) <push>(CBOR) [<push>(file)]...

    The 'gly' magic is the first 3 bytes of a larger data push,
    followed immediately by version (0x02) and flags bytes.
    For reveals (flags bit 7 set), metadata is in the next push.
    For commits (flags bit 7 clear), commit_hash follows inline.

    Returns a dict with envelope details, or None if not a valid
    Glyph envelope.
    """
    if GLYPH_MAGIC not in data:
        return None

    try:
        pushes = _parse_script_pushes(data)

        for i, push in enumerate(pushes):
            # -----------------------------------------------------------
            # Case A: 'gly' is a standalone 3-byte push
            # Matches v1 format and v2 Style B.
            # -----------------------------------------------------------
            if push == GLYPH_MAGIC:
                if i + 1 >= len(pushes):
                    continue
                payload = pushes[i + 1]
                if not payload or len(payload) < 2:
                    continue

                # Try decoding as CBOR reveal (most common case).
                if HAS_CBOR:
                    try:
                        decoded = cbor2.loads(payload)
                        if isinstance(decoded, dict):
                            v = decoded.get('v', GlyphVersion.V1)
                            return {
                                'version': v,
                                'flags': EnvelopeFlags.IS_REVEAL,
                                'is_reveal': True,
                                'metadata_bytes': payload,
                            }
                    except Exception:
                        pass

                # Try as v2 structured payload (commit: version+flags+data).
                if payload[0] in (GlyphVersion.V1, GlyphVersion.V2):
                    result = _parse_v2_structured(payload)
                    if result is not None:
                        return result
                continue

            # -----------------------------------------------------------
            # Case B: 'gly' is the prefix of a larger push
            # Matches v2 Style A (OP_RETURN concatenated format).
            # -----------------------------------------------------------
            if len(push) > 3 and push[:3] == GLYPH_MAGIC:
                inner = push[3:]  # bytes after 'gly'
                if len(inner) < 2:
                    continue
                version = inner[0]
                if version not in (GlyphVersion.V1, GlyphVersion.V2):
                    continue
                flags = inner[1]
                is_reveal = (flags & EnvelopeFlags.IS_REVEAL) != 0

                if is_reveal:
                    # Style A reveal — metadata is in the NEXT push
                    result: Dict[str, Any] = {
                        'version': version,
                        'flags': flags,
                        'is_reveal': True,
                    }
                    if i + 1 < len(pushes):
                        result['metadata_bytes'] = pushes[i + 1]
                        # Collect file-chunk pushes (if any)
                        if i + 2 < len(pushes):
                            result['file_chunks'] = pushes[i + 2:]
                    return result
                else:
                    # Style A commit — commit data follows inline
                    return _parse_v2_commit_inline(version, flags, inner[2:])

    except Exception:
        return None

    return None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _parse_script_pushes(data: bytes) -> list:
    """Extract an ordered list of data-push payloads from raw script bytes.

    Skips non-push opcodes (OP_RETURN, OP_3, OP_DROP, etc.).
    Handles:
      • OP_PUSHBYTES_N  (0x01-0x4b)
      • OP_PUSHDATA1    (0x4c)
      • OP_PUSHDATA2    (0x4d)
      • OP_PUSHDATA4    (0x4e)
      • Radiant ref opcodes 0xd0-0xd3, 0xd8  (skip 36-byte inline ref)
    """
    pushes: list = []
    pos = 0
    length = len(data)
    while pos < length:
        op = data[pos]
        pos += 1

        if 1 <= op <= 75:                            # OP_PUSHBYTES_N
            end = pos + op
            if end <= length:
                pushes.append(data[pos:end])
                pos = end
            else:
                break
        elif op == 0x4c:                             # OP_PUSHDATA1
            if pos < length:
                dlen = data[pos]; pos += 1
                end = pos + dlen
                if end <= length:
                    pushes.append(data[pos:end])
                    pos = end
                else:
                    break
            else:
                break
        elif op == 0x4d:                             # OP_PUSHDATA2
            if pos + 2 <= length:
                dlen = data[pos] | (data[pos + 1] << 8); pos += 2
                end = pos + dlen
                if end <= length:
                    pushes.append(data[pos:end])
                    pos = end
                else:
                    break
            else:
                break
        elif op == 0x4e:                             # OP_PUSHDATA4
            if pos + 4 <= length:
                dlen = (data[pos] | (data[pos + 1] << 8)
                        | (data[pos + 2] << 16) | (data[pos + 3] << 24))
                pos += 4
                end = pos + dlen
                if end <= length:
                    pushes.append(data[pos:end])
                    pos = end
                else:
                    break
            else:
                break
        elif op in (0xd0, 0xd1, 0xd2, 0xd3, 0xd8):  # Radiant ref ops
            pos += 36
        # else: non-push opcode — skip (OP_RETURN, OP_3, OP_DROP, …)

    return pushes


def _parse_v2_structured(payload: bytes) -> Optional[Dict[str, Any]]:
    """Parse a v2 structured payload that starts with version + flags.

    Used for v2 Style B commits where the push after 'gly' contains
    ``version || flags || commit_hash [|| optional fields]``.

    Also handles a v2 Style B reveal with the is_reveal flag set,
    where the remaining bytes after flags are the CBOR metadata.
    """
    if len(payload) < 2:
        return None
    version = payload[0]
    flags = payload[1]
    if version not in (GlyphVersion.V1, GlyphVersion.V2):
        return None
    is_reveal = (flags & EnvelopeFlags.IS_REVEAL) != 0
    result: Dict[str, Any] = {
        'version': version,
        'flags': flags,
        'is_reveal': is_reveal,
    }
    pos = 2
    if is_reveal:
        if pos < len(payload):
            result['metadata_bytes'] = payload[pos:]
    else:
        return _parse_v2_commit_inline(version, flags, payload[pos:])
    return result


def _parse_v2_commit_inline(version: int, flags: int,
                            remainder: bytes) -> Dict[str, Any]:
    """Build a commit envelope dict from the bytes after version+flags."""
    result: Dict[str, Any] = {
        'version': version,
        'flags': flags,
        'is_reveal': False,
    }
    pos = 0
    if pos + 32 <= len(remainder):
        result['commit_hash'] = remainder[pos:pos + 32].hex()
        pos += 32
        if flags & EnvelopeFlags.HAS_CONTENT_ROOT:
            if pos + 32 <= len(remainder):
                result['content_root'] = remainder[pos:pos + 32].hex()
                pos += 32
        if flags & EnvelopeFlags.HAS_CONTROLLER:
            if pos + 36 <= len(remainder):
                result['controller'] = remainder[pos:pos + 36].hex()
    return result


def _read_script_push(data: bytes, pos: int) -> Optional[bytes]:
    """Read a single script data push starting at *pos*.

    Handles OP_PUSHBYTES_N (1-75), OP_PUSHDATA1, OP_PUSHDATA2, OP_PUSHDATA4.
    Returns the pushed data bytes, or None on failure.

    .. note:: Prefer ``_parse_script_pushes`` for full script parsing.
              This helper remains for callers that need positional reads.
    """
    if pos >= len(data):
        return None
    op = data[pos]
    pos += 1
    if 1 <= op <= 75:
        end = pos + op
        if end <= len(data):
            return data[pos:end]
    elif op == 0x4c:
        if pos < len(data):
            dlen = data[pos]; pos += 1
            end = pos + dlen
            if end <= len(data):
                return data[pos:end]
    elif op == 0x4d:
        if pos + 2 <= len(data):
            dlen = data[pos] | (data[pos + 1] << 8); pos += 2
            end = pos + dlen
            if end <= len(data):
                return data[pos:end]
    elif op == 0x4e:
        if pos + 4 <= len(data):
            dlen = (data[pos] | (data[pos + 1] << 8)
                    | (data[pos + 2] << 16) | (data[pos + 3] << 24))
            pos += 4
            end = pos + dlen
            if end <= len(data):
                return data[pos:end]
    return None


def parse_glyph_metadata(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse Glyph metadata from a reveal envelope."""
    if not envelope:
        return None
    if not envelope.get('is_reveal'):
        return None
    metadata_bytes = envelope.get('metadata_bytes')
    if not metadata_bytes:
        return None
    if not HAS_CBOR:
        return None
    try:
        return cbor2.loads(metadata_bytes)
    except Exception:
        return None


def get_token_type_id(protocols: List[int]) -> int:
    """Map protocol list to a stable token type ID."""
    if not protocols:
        return GlyphTokenType.UNKNOWN

    if GlyphProtocol.GLYPH_FT in protocols:
        if GlyphProtocol.GLYPH_DMINT in protocols:
            return GlyphTokenType.DMINT
        return GlyphTokenType.FT

    if GlyphProtocol.GLYPH_NFT in protocols:
        if GlyphProtocol.GLYPH_WAVE in protocols:
            return GlyphTokenType.WAVE
        if GlyphProtocol.GLYPH_CONTAINER in protocols:
            return GlyphTokenType.CONTAINER
        if GlyphProtocol.GLYPH_AUTHORITY in protocols:
            return GlyphTokenType.AUTHORITY
        return GlyphTokenType.NFT

    if GlyphProtocol.GLYPH_DAT in protocols:
        return GlyphTokenType.DAT

    return GlyphTokenType.UNKNOWN


def format_ref(txid_hex: str, vout: int) -> str:
    """Format a ref string as txid_vout."""
    return f'{txid_hex}_{vout}'


def parse_ref(ref_str: str) -> Tuple[str, int]:
    """Parse a ref string formatted as txid_vout."""
    txid_hex, vout_str = ref_str.split('_')
    return txid_hex, int(vout_str)


def extract_token_info(metadata: Dict[str, Any], envelope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract a normalized token-info dict from decoded metadata.

    Handles both v1 (``type`` field) and v2 (``p`` list) formats.
    """
    version = metadata.get('v', (envelope or {}).get('version', GlyphVersion.V2))
    protocols = metadata.get('p', []) or []

    # v1 legacy: infer protocols from 'type' string
    if not protocols and 'type' in metadata:
        _type_map = {
            'ft': [GlyphProtocol.GLYPH_FT],
            'nft': [GlyphProtocol.GLYPH_NFT],
            'dat': [GlyphProtocol.GLYPH_DAT],
        }
        protocols = _type_map.get(str(metadata['type']).lower(), [])

    token_info: Dict[str, Any] = {
        'protocols': protocols,
        'version': version,
        'name': metadata.get('name') or metadata.get('n'),
        'ticker': metadata.get('ticker') or metadata.get('tk'),
        'decimals': metadata.get('decimals') or metadata.get('dc', 0),
    }

    # Pass through attrs if present
    if 'attrs' in metadata:
        token_info['attrs'] = metadata['attrs']

    # dMint fields — check both top-level and nested 'dmint' object
    if GlyphProtocol.GLYPH_DMINT in protocols:
        dm_nested = metadata.get('dmint', {}) if isinstance(metadata.get('dmint'), dict) else {}
        token_info['dmint'] = {
            'algorithm': metadata.get('algorithm') or dm_nested.get('algorithm'),
            'start_difficulty': metadata.get('startDiff') or dm_nested.get('startDiff'),
            'max_supply': metadata.get('maxSupply') or dm_nested.get('maxSupply'),
            'reward': metadata.get('reward') or dm_nested.get('reward'),
            'premine': metadata.get('premine') or dm_nested.get('premine', 0),
        }
        daa = metadata.get('daa') or dm_nested.get('daa')
        if daa and isinstance(daa, dict):
            token_info['dmint']['daa_mode'] = daa.get('mode')
            token_info['dmint']['halflife'] = daa.get('halflife')

    return token_info


def parse_glyph_from_output(script: bytes) -> Optional[Dict[str, Any]]:
    """Best-effort parse for glyph commit/reveal info from an output script."""
    env = parse_glyph_envelope(script)
    if not env:
        return None
    if env.get('is_reveal'):
        return {'is_reveal': True, 'metadata_bytes': env.get('metadata_bytes', b'')}
    # Commit envelope
    return {
        'is_commit': True,
        'commit_hash': env.get('commit_hash'),
        'content_root': env.get('content_root'),
        'controller': env.get('controller'),
    }


def get_protocol_name(protocol_id: int) -> str:
    """Get human-readable name for a protocol ID."""
    return PROTOCOL_NAMES.get(protocol_id, f'Unknown({protocol_id})')


def get_token_type(protocols: List[int]) -> str:
    """Get token type string from protocol list."""
    if GlyphProtocol.GLYPH_FT in protocols:
        if GlyphProtocol.GLYPH_DMINT in protocols:
            return 'dMint FT'
        return 'Fungible Token'
    
    if GlyphProtocol.GLYPH_NFT in protocols:
        if GlyphProtocol.GLYPH_WAVE in protocols:
            return 'WAVE Name'
        if GlyphProtocol.GLYPH_AUTHORITY in protocols:
            return 'Authority'
        if GlyphProtocol.GLYPH_CONTAINER in protocols:
            return 'Container'
        if GlyphProtocol.GLYPH_ENCRYPTED in protocols:
            return 'Encrypted NFT'
        if GlyphProtocol.GLYPH_MUT in protocols:
            return 'Mutable NFT'
        return 'NFT'
    
    if GlyphProtocol.GLYPH_DAT in protocols:
        return 'Data'
    
    return 'Unknown'


def is_fungible(protocols: List[int]) -> bool:
    """Check if protocols indicate a fungible token."""
    return GlyphProtocol.GLYPH_FT in protocols


def is_nft(protocols: List[int]) -> bool:
    """Check if protocols indicate an NFT."""
    return GlyphProtocol.GLYPH_NFT in protocols


def is_dmint(protocols: List[int]) -> bool:
    """Check if protocols indicate a dMint token."""
    return GlyphProtocol.GLYPH_DMINT in protocols


def is_mutable(protocols: List[int]) -> bool:
    """Check if protocols indicate a mutable token."""
    return GlyphProtocol.GLYPH_MUT in protocols


def is_container(protocols: List[int]) -> bool:
    """Check if protocols indicate a container."""
    return GlyphProtocol.GLYPH_CONTAINER in protocols


def parse_dmint_contract_state(script: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse dMint contract state from a UTXO output script.

    The v1/v2 dMint contract output script encodes live state as data pushes
    before the contract bytecode (which starts at ``OP_CHECKTEMPLATEVERIFY``
    0xbd or the first non-push opcode).  The layout is:

        <height:4B> d8<contractRef:36B> d0<tokenRef:36B>
        <maxHeight> <reward> <target>
        [<algoId:1B> <lastTime:4B> <targetTime:minimal>]
        bd <contract_bytecode>

    All numeric values are minimal CScriptNum encoded pushes.

    Returns a dict with parsed fields, or None if the script does not look
    like a dMint contract output.
    """
    if not script or len(script) < 80:
        return None

    # Must contain OP_PUSHINPUTREFSINGLETON (0xd8)
    if b'\xd8' not in script:
        return None

    try:
        pushes = _parse_script_pushes(script)
        if len(pushes) < 4:
            return None

        # Heuristic: find the contract ref (36 bytes after a d8 opcode) and
        # token ref (36 bytes after a d0 opcode) by scanning the raw script.
        contract_ref = None
        token_ref = None
        pos = 0
        while pos < len(script) - 36:
            op = script[pos]
            if op == 0xd8 and pos + 37 <= len(script):
                contract_ref = script[pos + 1:pos + 37]
                pos += 37
            elif op == 0xd0 and pos + 37 <= len(script):
                token_ref = script[pos + 1:pos + 37]
                pos += 37
            elif 1 <= op <= 75:
                pos += 1 + op
            elif op == 0x4c and pos + 1 < len(script):
                pos += 2 + script[pos + 1]
            elif op == 0x4d and pos + 2 < len(script):
                dlen = script[pos + 1] | (script[pos + 2] << 8)
                pos += 3 + dlen
            elif op == 0x4e and pos + 4 < len(script):
                dlen = (script[pos + 1] | (script[pos + 2] << 8)
                        | (script[pos + 3] << 16) | (script[pos + 4] << 24))
                pos += 5 + dlen
            else:
                pos += 1

        if not contract_ref:
            return None

        # Now extract the numeric data pushes that precede the contract
        # bytecode.  Filter out the 36-byte ref pushes.
        numeric_pushes = [p for p in pushes if len(p) <= 8 and len(p) != 36]

        result: Dict[str, Any] = {
            'contract_ref': contract_ref.hex() if contract_ref else None,
            'token_ref': token_ref.hex() if token_ref else None,
        }

        # First numeric push is often the height (4 bytes LE)
        if len(numeric_pushes) >= 1:
            result['height'] = _scriptnum_to_int(numeric_pushes[0])

        # Subsequent pushes: maxHeight, reward, target
        if len(numeric_pushes) >= 2:
            result['max_height'] = _scriptnum_to_int(numeric_pushes[1])
        if len(numeric_pushes) >= 3:
            result['reward'] = _scriptnum_to_int(numeric_pushes[2])
        if len(numeric_pushes) >= 4:
            result['target'] = _scriptnum_to_int(numeric_pushes[3])

        # Optional v2 fields: algo_id, lastTime, targetTime
        if len(numeric_pushes) >= 5:
            result['algo_id'] = _scriptnum_to_int(numeric_pushes[4])
        if len(numeric_pushes) >= 6:
            result['last_time'] = _scriptnum_to_int(numeric_pushes[5])
        if len(numeric_pushes) >= 7:
            result['target_time'] = _scriptnum_to_int(numeric_pushes[6])

        return result
    except Exception:
        return None


def _scriptnum_to_int(data: bytes) -> int:
    """Convert a CScriptNum-encoded byte string to a Python int.

    CScriptNum uses minimal little-endian encoding with the MSB of the
    last byte as a sign bit.  An empty byte string encodes 0.
    """
    if not data:
        return 0
    # Little-endian magnitude with sign bit in top bit of last byte
    negative = (data[-1] & 0x80) != 0
    # Strip sign bit for magnitude
    raw = bytearray(data)
    raw[-1] &= 0x7f
    value = int.from_bytes(raw, 'little')
    return -value if negative else value


def is_dmint_reveal(script_or_envelope) -> bool:
    """Check if a script/envelope contains a dMint reveal (DMINT protocol).

    Accepts either raw script bytes or a pre-parsed envelope dict.
    """
    if isinstance(script_or_envelope, dict):
        env = script_or_envelope
    else:
        env = parse_glyph_envelope(script_or_envelope)
    if not env:
        return False
    # Already-parsed metadata takes priority
    metadata = env.get('metadata')
    if metadata is None:
        if not env.get('is_reveal'):
            return False
        metadata = parse_glyph_metadata(env)
    if not metadata or not isinstance(metadata, dict):
        return False
    protocols = metadata.get('p', [])
    return GlyphProtocol.GLYPH_DMINT in protocols


def is_wave_claim(script_or_envelope) -> bool:
    """Check if a script/envelope contains a WAVE name claim (WAVE protocol).

    Accepts either raw script bytes or a pre-parsed envelope dict.
    """
    if isinstance(script_or_envelope, dict):
        env = script_or_envelope
    else:
        env = parse_glyph_envelope(script_or_envelope)
    if not env:
        return False
    metadata = env.get('metadata')
    if metadata is None:
        if not env.get('is_reveal'):
            return False
        metadata = parse_glyph_metadata(env)
    if not metadata or not isinstance(metadata, dict):
        return False
    protocols = metadata.get('p', [])
    return GlyphProtocol.GLYPH_WAVE in protocols


def validate_protocols(protocols: List[int]) -> Tuple[bool, Optional[str]]:
    """
    Validate a protocol combination per Glyph v2 spec Section 3.5.
    
    Returns (valid, error_message).
    """
    # FT and NFT are mutually exclusive
    if GlyphProtocol.GLYPH_FT in protocols and GlyphProtocol.GLYPH_NFT in protocols:
        return False, 'FT and NFT are mutually exclusive'
    
    # BURN alone is invalid (it's an action marker, not a token type)
    if protocols == [GlyphProtocol.GLYPH_BURN]:
        return False, 'BURN alone is invalid - it is an action marker, not a token type'
    
    # BURN must accompany a token type (FT or NFT)
    if GlyphProtocol.GLYPH_BURN in protocols:
        if GlyphProtocol.GLYPH_FT not in protocols and GlyphProtocol.GLYPH_NFT not in protocols:
            return False, 'BURN must accompany FT or NFT'
    
    # DMINT requires FT
    if GlyphProtocol.GLYPH_DMINT in protocols and GlyphProtocol.GLYPH_FT not in protocols:
        return False, 'DMINT requires FT'
    
    # MUT requires NFT
    if GlyphProtocol.GLYPH_MUT in protocols and GlyphProtocol.GLYPH_NFT not in protocols:
        return False, 'MUT requires NFT'
    
    # CONTAINER requires NFT
    if GlyphProtocol.GLYPH_CONTAINER in protocols and GlyphProtocol.GLYPH_NFT not in protocols:
        return False, 'CONTAINER requires NFT'
    
    # ENCRYPTED requires NFT
    if GlyphProtocol.GLYPH_ENCRYPTED in protocols and GlyphProtocol.GLYPH_NFT not in protocols:
        return False, 'ENCRYPTED requires NFT'
    
    # TIMELOCK requires ENCRYPTED
    if GlyphProtocol.GLYPH_TIMELOCK in protocols and GlyphProtocol.GLYPH_ENCRYPTED not in protocols:
        return False, 'TIMELOCK requires ENCRYPTED'
    
    # AUTHORITY requires NFT
    if GlyphProtocol.GLYPH_AUTHORITY in protocols and GlyphProtocol.GLYPH_NFT not in protocols:
        return False, 'AUTHORITY requires NFT'
    
    # WAVE requires NFT and MUT
    if GlyphProtocol.GLYPH_WAVE in protocols:
        if GlyphProtocol.GLYPH_NFT not in protocols:
            return False, 'WAVE requires NFT'
        if GlyphProtocol.GLYPH_MUT not in protocols:
            return False, 'WAVE requires MUT'
    
    return True, None


def decode_cbor_metadata(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode raw CBOR bytes into a metadata dict.

    Returns None if data is invalid CBOR or not a dict.
    """
    if not HAS_CBOR:
        return None
    try:
        result = cbor2.loads(data)
        if not isinstance(result, dict):
            return None
        return result
    except Exception:
        return None


def is_glyph_op_return(script: bytes) -> bool:
    """Check if an output script is an OP_RETURN containing Glyph magic.

    Handles both OP_RETURN and OP_FALSE OP_RETURN patterns.
    """
    if not script:
        return False
    # Must start with OP_RETURN (0x6a) or OP_FALSE OP_RETURN (0x00 0x6a)
    if script[0] == 0x6a:
        return GLYPH_MAGIC in script
    if len(script) >= 2 and script[0] == 0x00 and script[1] == 0x6a:
        return GLYPH_MAGIC in script
    return False


def format_glyph_id(txid: str, vout: int) -> str:
    """Format a Glyph ID from txid and vout."""
    return f'{txid}:{vout}'


def parse_glyph_id(glyph_id: str) -> Tuple[str, int]:
    """Parse a Glyph ID into txid and vout."""
    parts = glyph_id.split(':')
    return parts[0], int(parts[1])
