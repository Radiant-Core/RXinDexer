#!/usr/bin/env python3
"""
Test script to verify Glyph parsing logic.

This tests the parse_glyph_envelope function with sample data
that matches the actual Glyph format from Photonic Wallet.

This is a standalone test that doesn't require the full electrumx package.
"""

import sys
import struct
from typing import Optional, Dict, Any, List, Tuple

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False
    print("WARNING: cbor2 not installed, CBOR decoding will fail")

# ============================================================================
# Inline copy of glyph.py functions for standalone testing
# ============================================================================

GLYPH_MAGIC = b'gly'


def parse_script_chunks(script: bytes) -> List[Tuple[int, bytes]]:
    """Parse a Bitcoin script into chunks (opcode, data)."""
    chunks = []
    pos = 0
    
    while pos < len(script):
        opcode = script[pos]
        pos += 1
        
        if opcode <= 0x4b:
            length = opcode
            if pos + length <= len(script):
                chunks.append((opcode, script[pos:pos + length]))
                pos += length
            else:
                break
        elif opcode == 0x4c:
            if pos < len(script):
                length = script[pos]
                pos += 1
                if pos + length <= len(script):
                    chunks.append((opcode, script[pos:pos + length]))
                    pos += length
                else:
                    break
            else:
                break
        elif opcode == 0x4d:
            if pos + 2 <= len(script):
                length = struct.unpack('<H', script[pos:pos + 2])[0]
                pos += 2
                if pos + length <= len(script):
                    chunks.append((opcode, script[pos:pos + length]))
                    pos += length
                else:
                    break
            else:
                break
        elif opcode == 0x4e:
            if pos + 4 <= len(script):
                length = struct.unpack('<I', script[pos:pos + 4])[0]
                pos += 4
                if pos + length <= len(script):
                    chunks.append((opcode, script[pos:pos + length]))
                    pos += length
                else:
                    break
            else:
                break
        else:
            chunks.append((opcode, b''))
    
    return chunks


def contains_glyph_magic(data: bytes) -> bool:
    """Check if data contains Glyph magic bytes."""
    return GLYPH_MAGIC in data


def parse_glyph_envelope(script: bytes) -> Optional[Dict[str, Any]]:
    """Parse a Glyph envelope from a script."""
    if not contains_glyph_magic(script):
        return None
    
    try:
        chunks = parse_script_chunks(script)
        
        for i, (opcode, data) in enumerate(chunks):
            if data == GLYPH_MAGIC and i + 1 < len(chunks):
                next_opcode, payload_data = chunks[i + 1]
                
                if not payload_data:
                    continue
                
                return {
                    'is_reveal': True,
                    'metadata_bytes': payload_data,
                    'magic_index': i,
                    'payload_index': i + 1,
                }
        
        return None
        
    except Exception:
        return None


def parse_glyph_metadata(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse Glyph metadata from a reveal envelope."""
    if not envelope:
        return None
    
    if envelope.get('is_reveal'):
        metadata_bytes = envelope.get('metadata_bytes')
        if not metadata_bytes:
            return None
        if HAS_CBOR:
            try:
                return cbor2.loads(metadata_bytes)
            except Exception:
                return None
    
    return None


def test_parse_script_chunks():
    """Test script chunk parsing."""
    print("\n=== Testing parse_script_chunks ===")
    
    # Simple 3-byte push: 0x03 + 3 bytes
    script = bytes([0x03, 0x67, 0x6c, 0x79])  # Push 'gly'
    chunks = parse_script_chunks(script)
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert chunks[0][0] == 3, f"Expected opcode 3, got {chunks[0][0]}"
    assert chunks[0][1] == b'gly', f"Expected 'gly', got {chunks[0][1]}"
    print("✓ Simple 3-byte push works")
    
    # Multiple chunks: 'gly' + some data
    cbor_data = cbor2.dumps({'p': [2], 'name': 'Test NFT'}) if HAS_CBOR else b'\xa2ap\x82\x02dname\x08Test NFT'
    script = bytes([0x03, 0x67, 0x6c, 0x79])  # Push 'gly'
    if len(cbor_data) < 76:
        script += bytes([len(cbor_data)]) + cbor_data
    elif len(cbor_data) < 256:
        script += bytes([0x4c, len(cbor_data)]) + cbor_data
    
    chunks = parse_script_chunks(script)
    assert len(chunks) == 2, f"Expected 2 chunks, got {len(chunks)}"
    assert chunks[0][1] == b'gly', f"First chunk should be 'gly', got {chunks[0][1]}"
    print("✓ Multiple chunk parsing works")
    
    return True


def test_contains_glyph_magic():
    """Test Glyph magic detection."""
    print("\n=== Testing contains_glyph_magic ===")
    
    # Script with gly magic
    script_with_magic = bytes([0x03, 0x67, 0x6c, 0x79, 0x10]) + b'\x00' * 16
    assert contains_glyph_magic(script_with_magic), "Should find 'gly' magic"
    print("✓ Found 'gly' in script")
    
    # Script without magic
    script_without = bytes([0x76, 0xa9, 0x14]) + b'\x00' * 20 + bytes([0x88, 0xac])
    assert not contains_glyph_magic(script_without), "Should not find 'gly' in P2PKH"
    print("✓ Correctly rejected P2PKH without gly")
    
    return True


def test_parse_glyph_envelope():
    """Test Glyph envelope parsing."""
    print("\n=== Testing parse_glyph_envelope ===")
    
    if not HAS_CBOR:
        print("⚠ Skipping CBOR tests - cbor2 not installed")
        return True
    
    # Create a valid Glyph envelope (like Photonic Wallet creates)
    # Format: [3-byte push 'gly'] [push CBOR payload]
    payload = {'p': [2], 'name': 'Test NFT', 'desc': 'A test token'}
    cbor_data = cbor2.dumps(payload)
    
    # Build the script
    script = bytes([0x03, 0x67, 0x6c, 0x79])  # OP_PUSHBYTES_3 'gly'
    if len(cbor_data) < 76:
        script += bytes([len(cbor_data)]) + cbor_data
    elif len(cbor_data) < 256:
        script += bytes([0x4c, len(cbor_data)]) + cbor_data
    else:
        # OP_PUSHDATA2
        script += bytes([0x4d]) + len(cbor_data).to_bytes(2, 'little') + cbor_data
    
    print(f"Test script hex: {script.hex()}")
    print(f"Script length: {len(script)} bytes")
    
    # Parse the envelope
    envelope = parse_glyph_envelope(script)
    
    assert envelope is not None, "Envelope should not be None"
    assert envelope.get('is_reveal') == True, "Should be a reveal"
    assert 'metadata_bytes' in envelope, "Should have metadata_bytes"
    print(f"✓ Parsed envelope: is_reveal={envelope.get('is_reveal')}")
    
    # Parse the metadata
    metadata = parse_glyph_metadata(envelope)
    assert metadata is not None, "Metadata should not be None"
    assert metadata.get('p') == [2], f"Protocols should be [2], got {metadata.get('p')}"
    assert metadata.get('name') == 'Test NFT', f"Name should be 'Test NFT', got {metadata.get('name')}"
    print(f"✓ Parsed metadata: protocols={metadata.get('p')}, name={metadata.get('name')}")
    
    return True


def test_real_world_example():
    """Test with a real-world-like Glyph scriptSig."""
    print("\n=== Testing real-world example ===")
    
    if not HAS_CBOR:
        print("⚠ Skipping - cbor2 not installed")
        return True
    
    # Simulate a real NFT reveal scriptSig
    # In real transactions, this would be the scriptSig that spends a commit UTXO
    payload = {
        'p': [2],  # NFT protocol
        'name': 'Radiant NFT #1',
        'desc': 'First test NFT on Radiant',
        'attrs': {
            'rarity': 'legendary',
            'edition': 1
        }
    }
    cbor_data = cbor2.dumps(payload)
    
    # Build scriptSig: signature + pubkey + 'gly' + cbor (simplified)
    # In reality, the sig/pubkey come first, then 'gly' + CBOR
    fake_sig = bytes(72)  # Fake signature
    fake_pubkey = bytes(33)  # Fake compressed pubkey
    
    script = bytes([72]) + fake_sig  # Push signature
    script += bytes([33]) + fake_pubkey  # Push pubkey
    script += bytes([0x03, 0x67, 0x6c, 0x79])  # Push 'gly'
    if len(cbor_data) < 76:
        script += bytes([len(cbor_data)]) + cbor_data
    elif len(cbor_data) < 256:
        script += bytes([0x4c, len(cbor_data)]) + cbor_data
    
    print(f"Real-world script length: {len(script)} bytes")
    
    # Should still find and parse the Glyph envelope
    envelope = parse_glyph_envelope(script)
    assert envelope is not None, "Should find envelope in complex script"
    
    metadata = parse_glyph_metadata(envelope)
    assert metadata is not None, "Should parse metadata"
    assert metadata.get('p') == [2], f"Should be NFT protocol"
    assert metadata.get('name') == 'Radiant NFT #1', f"Name mismatch"
    print(f"✓ Parsed real-world example: {metadata.get('name')}")
    print(f"  Protocols: {metadata.get('p')}")
    print(f"  Attrs: {metadata.get('attrs')}")
    
    return True


def test_v1_vs_v2_protocols():
    """Test parsing of v1 and v2 protocol combinations."""
    print("\n=== Testing v1 vs v2 protocol combinations ===")
    
    if not HAS_CBOR:
        print("⚠ Skipping - cbor2 not installed")
        return True
    
    # v1 protocols: FT=1, NFT=2, DAT=3, DMINT=4, MUT=5
    v1_examples = [
        {'p': [1], 'name': 'v1 FT', 'ticker': 'V1FT'},  # Simple FT
        {'p': [2], 'name': 'v1 NFT'},  # Simple NFT
        {'p': [1, 4], 'name': 'v1 dMint', 'ticker': 'DMINT'},  # dMint FT
        {'p': [2, 5], 'name': 'v1 Mutable NFT'},  # Mutable NFT
    ]
    
    # v2 adds: BURN=6, CONTAINER=7, ENCRYPTED=8, TIMELOCK=9, AUTHORITY=10, WAVE=11
    v2_examples = [
        {'p': [2, 7], 'name': 'v2 Container'},  # Container
        {'p': [2, 10], 'name': 'v2 Authority'},  # Authority
        {'p': [2, 5, 11], 'name': 'test.rxd'},  # WAVE name
    ]
    
    all_examples = v1_examples + v2_examples
    
    for payload in all_examples:
        cbor_data = cbor2.dumps(payload)
        script = bytes([0x03, 0x67, 0x6c, 0x79])
        if len(cbor_data) < 76:
            script += bytes([len(cbor_data)]) + cbor_data
        else:
            script += bytes([0x4c, len(cbor_data)]) + cbor_data
        
        envelope = parse_glyph_envelope(script)
        assert envelope is not None, f"Failed to parse: {payload.get('name')}"
        
        metadata = parse_glyph_metadata(envelope)
        assert metadata is not None, f"Failed metadata: {payload.get('name')}"
        assert metadata.get('p') == payload['p'], f"Protocol mismatch for {payload.get('name')}"
        
        print(f"✓ {payload.get('name')}: protocols={payload['p']}")
    
    return True


# ============================================================================
# Protocol Validation Tests (Glyph v2 spec Section 3.5)
# ============================================================================

# Protocol IDs per Glyph v2 spec
class GlyphProtocol:
    GLYPH_FT = 1
    GLYPH_NFT = 2
    GLYPH_DAT = 3
    GLYPH_DMINT = 4
    GLYPH_MUT = 5
    GLYPH_BURN = 6
    GLYPH_CONTAINER = 7
    GLYPH_ENCRYPTED = 8
    GLYPH_TIMELOCK = 9
    GLYPH_AUTHORITY = 10
    GLYPH_WAVE = 11


def validate_protocols(protocols):
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


def test_protocol_validation_burn():
    """Test BURN protocol validation per Glyph v2 spec Section 3.5."""
    print("\n=== Testing BURN protocol validation ===")
    
    # Valid BURN combinations
    valid_cases = [
        ([1, 6], "FT + BURN"),           # FT burn
        ([2, 6], "NFT + BURN"),          # NFT burn
        ([1, 4, 6], "dMint FT + BURN"),  # dMint burn
    ]
    
    for protocols, name in valid_cases:
        valid, error = validate_protocols(protocols)
        assert valid, f"{name} should be valid, got error: {error}"
        print(f"✓ Valid: {name} {protocols}")
    
    # Invalid BURN combinations
    invalid_cases = [
        ([6], "BURN alone", "BURN alone is invalid"),
        ([3, 6], "DAT + BURN", "BURN must accompany FT or NFT"),
        ([6, 7], "BURN + CONTAINER", "BURN must accompany FT or NFT"),
    ]
    
    for protocols, name, expected_error in invalid_cases:
        valid, error = validate_protocols(protocols)
        assert not valid, f"{name} should be invalid"
        assert expected_error in error, f"Expected '{expected_error}' in error, got '{error}'"
        print(f"✓ Invalid: {name} {protocols} - {error}")
    
    return True


def test_protocol_validation_timelock():
    """Test TIMELOCK protocol validation per Glyph v2 spec Section 3.5."""
    print("\n=== Testing TIMELOCK protocol validation ===")
    
    # Valid TIMELOCK combinations
    valid_cases = [
        ([2, 8, 9], "NFT + ENCRYPTED + TIMELOCK"),  # Timelocked encrypted NFT
        ([2, 5, 8, 9], "NFT + MUT + ENCRYPTED + TIMELOCK"),  # Mutable timelocked NFT
    ]
    
    for protocols, name in valid_cases:
        valid, error = validate_protocols(protocols)
        assert valid, f"{name} should be valid, got error: {error}"
        print(f"✓ Valid: {name} {protocols}")
    
    # Invalid TIMELOCK combinations
    invalid_cases = [
        ([9], "TIMELOCK alone", "TIMELOCK requires ENCRYPTED"),
        ([2, 9], "NFT + TIMELOCK (no ENCRYPTED)", "TIMELOCK requires ENCRYPTED"),
        ([1, 9], "FT + TIMELOCK", "TIMELOCK requires ENCRYPTED"),
    ]
    
    for protocols, name, expected_error in invalid_cases:
        valid, error = validate_protocols(protocols)
        assert not valid, f"{name} should be invalid"
        assert expected_error in error, f"Expected '{expected_error}' in error, got '{error}'"
        print(f"✓ Invalid: {name} {protocols} - {error}")
    
    return True


def test_protocol_validation_encrypted():
    """Test ENCRYPTED protocol validation per Glyph v2 spec Section 3.5."""
    print("\n=== Testing ENCRYPTED protocol validation ===")
    
    # Valid ENCRYPTED combinations
    valid_cases = [
        ([2, 8], "NFT + ENCRYPTED"),
        ([2, 5, 8], "NFT + MUT + ENCRYPTED"),
    ]
    
    for protocols, name in valid_cases:
        valid, error = validate_protocols(protocols)
        assert valid, f"{name} should be valid, got error: {error}"
        print(f"✓ Valid: {name} {protocols}")
    
    # Invalid ENCRYPTED combinations
    invalid_cases = [
        ([8], "ENCRYPTED alone", "ENCRYPTED requires NFT"),
        ([1, 8], "FT + ENCRYPTED", "ENCRYPTED requires NFT"),
    ]
    
    for protocols, name, expected_error in invalid_cases:
        valid, error = validate_protocols(protocols)
        assert not valid, f"{name} should be invalid"
        assert expected_error in error, f"Expected '{expected_error}' in error, got '{error}'"
        print(f"✓ Invalid: {name} {protocols} - {error}")
    
    return True


def test_protocol_validation_all_rules():
    """Test all protocol combination rules from Glyph v2 spec Section 3.5."""
    print("\n=== Testing all protocol combination rules ===")
    
    # All valid combinations from the spec
    valid_combinations = [
        [1],           # FT only
        [2],           # NFT only
        [3],           # DAT only
        [1, 4],        # FT + dMint
        [2, 5],        # NFT + Mutable
        [2, 7],        # NFT + Container
        [2, 8],        # NFT + Encrypted
        [2, 10],       # NFT + Authority
        [2, 5, 11],    # NFT + Mutable + WAVE
        [2, 8, 9],     # NFT + Encrypted + Timelock
        [1, 6],        # FT + Burn
        [2, 6],        # NFT + Burn
    ]
    
    for protocols in valid_combinations:
        valid, error = validate_protocols(protocols)
        assert valid, f"{protocols} should be valid, got: {error}"
        print(f"✓ Valid: {protocols}")
    
    # All invalid combinations from the spec
    invalid_combinations = [
        ([1, 2], "FT + NFT mutually exclusive"),
        ([4], "dMint alone requires FT"),
        ([5], "Mutable alone requires NFT"),
        ([6], "Burn alone invalid"),
        ([7], "Container alone requires NFT"),
        ([8], "Encrypted alone requires NFT"),
        ([9], "Timelock alone requires ENCRYPTED"),
        ([2, 9], "NFT + Timelock requires ENCRYPTED"),
        ([11], "WAVE alone requires NFT + MUT"),
        ([2, 11], "WAVE requires MUT"),
    ]
    
    for protocols, reason in invalid_combinations:
        valid, error = validate_protocols(protocols)
        assert not valid, f"{protocols} should be invalid ({reason})"
        print(f"✓ Invalid: {protocols} - {reason}")
    
    return True


def main():
    print("=" * 60)
    print("Glyph Parsing Test Suite")
    print("=" * 60)
    
    tests = [
        test_parse_script_chunks,
        test_contains_glyph_magic,
        test_parse_glyph_envelope,
        test_real_world_example,
        test_v1_vs_v2_protocols,
        # Protocol validation tests (Glyph v2 spec Section 3.5)
        test_protocol_validation_burn,
        test_protocol_validation_timelock,
        test_protocol_validation_encrypted,
        test_protocol_validation_all_rules,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
                print(f"✗ {test.__name__} returned False")
        except AssertionError as e:
            failed += 1
            print(f"✗ {test.__name__} FAILED: {e}")
        except Exception as e:
            failed += 1
            print(f"✗ {test.__name__} ERROR: {e}")
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
