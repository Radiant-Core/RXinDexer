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
    Parse a Glyph envelope from raw script data.
    
    Returns a dict with envelope details or None if not a valid Glyph envelope.
    """
    magic_pos = find_glyph_magic(data)
    if magic_pos == -1:
        return None

    try:
        # Position after magic bytes
        pos = magic_pos + 3

        if pos >= len(data):
            return None

        # Version byte
        version = data[pos]
        pos += 1

        if version not in (GlyphVersion.V1, GlyphVersion.V2):
            return None

        if pos >= len(data):
            return None

        # Flags byte
        flags = data[pos]
        pos += 1

        is_reveal = (flags & EnvelopeFlags.IS_REVEAL) != 0

        result = {
            'version': version,
            'flags': flags,
            'is_reveal': is_reveal,
        }

        if is_reveal:
            # Reveal envelope - remaining data is metadata
            if pos < len(data):
                result['metadata_bytes'] = data[pos:]
        else:
            # Commit envelope - next 32 bytes are commit hash
            if pos + 32 <= len(data):
                result['commit_hash'] = data[pos:pos+32].hex()
                pos += 32

                # Optional content root
                if flags & EnvelopeFlags.HAS_CONTENT_ROOT:
                    if pos + 32 <= len(data):
                        result['content_root'] = data[pos:pos+32].hex()
                        pos += 32

                # Optional controller
                if flags & EnvelopeFlags.HAS_CONTROLLER:
                    if pos + 36 <= len(data):
                        result['controller'] = data[pos:pos+36].hex()

        return result

    except Exception:
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
    """Extract a normalized token-info dict from decoded metadata."""
    protocols = metadata.get('p', []) or []
    token_info: Dict[str, Any] = {
        'protocols': protocols,
        'version': (envelope or {}).get('version', GlyphVersion.V2),
        'name': metadata.get('name') or metadata.get('n'),
        'ticker': metadata.get('ticker') or metadata.get('tk'),
        'decimals': metadata.get('decimals') or metadata.get('dc', 0),
    }
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


def format_glyph_id(txid: str, vout: int) -> str:
    """Format a Glyph ID from txid and vout."""
    return f'{txid}:{vout}'


def parse_glyph_id(glyph_id: str) -> Tuple[str, int]:
    """Parse a Glyph ID into txid and vout."""
    parts = glyph_id.split(':')
    return parts[0], int(parts[1])
