#!/usr/bin/env python3
"""
Glyph v1 / v2 Envelope Parsing Tests

Tests the real ``parse_glyph_envelope`` from ``electrumx.lib.glyph`` against
synthetic byte-level samples that mirror every known on-chain format:

  * v1  — OP_PUSHBYTES_3 'gly' + push(CBOR)  (scriptSig)
  * v2 Style A — OP_RETURN push('gly'||ver||flags) + push(CBOR)  (output)
  * v2 Style B — OP_3 + push('gly') + push(CBOR)  (scriptSig)

Also covers commit envelopes, edge cases, and helper utilities.
"""

import os
import struct
import pytest

import cbor2

from electrumx.lib.glyph import (
    GLYPH_MAGIC,
    GlyphVersion,
    GlyphProtocol,
    GlyphTokenType,
    EnvelopeFlags,
    contains_glyph_magic,
    is_glyph_op_return,
    parse_glyph_envelope,
    parse_glyph_metadata,
    extract_token_info,
    get_token_type_id,
    _parse_script_pushes,
)


# ============================================================================
# Script-building helpers
# ============================================================================

def _push(data: bytes) -> bytes:
    """Encode *data* as a minimal Bitcoin data-push."""
    n = len(data)
    if n <= 75:
        return bytes([n]) + data
    if n <= 0xFF:
        return bytes([0x4C, n]) + data
    if n <= 0xFFFF:
        return bytes([0x4D]) + struct.pack('<H', n) + data
    return bytes([0x4E]) + struct.pack('<I', n) + data


def _build_v1_scriptsig(cbor_payload: bytes,
                         *,
                         sig: bytes = None,
                         pubkey: bytes = None) -> bytes:
    """Build a v1-style scriptSig with optional sig+pubkey prefix.

    Layout: [push(sig)] [push(pubkey)] OP_PUSHBYTES_3 'gly' push(cbor)
    """
    parts = b''
    if sig is not None:
        parts += _push(sig)
    if pubkey is not None:
        parts += _push(pubkey)
    # 0x03 = OP_PUSHBYTES_3, followed by the literal 'gly'
    parts += bytes([0x03]) + b'gly'
    parts += _push(cbor_payload)
    return parts


def _build_v2a_commit(flags: int, commit_hash: bytes,
                       *,
                       content_root: bytes = None,
                       controller: bytes = None,
                       op_false_prefix: bool = False) -> bytes:
    """Build a v2 Style A commit OP_RETURN output script."""
    inner = b'gly' + bytes([GlyphVersion.V2, flags]) + commit_hash
    if content_root is not None:
        inner += content_root
    if controller is not None:
        inner += controller
    script = b''
    if op_false_prefix:
        script += bytes([0x00])        # OP_FALSE
    script += bytes([0x6A])            # OP_RETURN
    script += _push(inner)
    return script


def _build_v2a_reveal(cbor_payload: bytes,
                       flags: int = 0,
                       *,
                       file_chunks: list = None,
                       op_false_prefix: bool = False) -> bytes:
    """Build a v2 Style A reveal OP_RETURN output script."""
    reveal_flags = flags | EnvelopeFlags.IS_REVEAL
    header = b'gly' + bytes([GlyphVersion.V2, reveal_flags])
    script = b''
    if op_false_prefix:
        script += bytes([0x00])
    script += bytes([0x6A])
    script += _push(header)
    script += _push(cbor_payload)
    for chunk in (file_chunks or []):
        script += _push(chunk)
    return script


def _build_v2b_reveal(cbor_payload: bytes,
                       *,
                       file_chunks: list = None) -> bytes:
    """Build a v2 Style B reveal scriptSig (OP_3 chunked)."""
    script = bytes([0x53])             # OP_3
    script += bytes([0x03]) + b'gly'   # push('gly')
    script += _push(cbor_payload)
    for chunk in (file_chunks or []):
        script += _push(chunk)
    return script


def _build_v2b_commit(flags: int, commit_hash: bytes) -> bytes:
    """Build a v2 Style B commit scriptSig (OP_3 chunked)."""
    payload = bytes([GlyphVersion.V2, flags]) + commit_hash
    script = bytes([0x53])             # OP_3
    script += bytes([0x03]) + b'gly'
    script += _push(payload)
    return script


# ============================================================================
# Sample CBOR payloads
# ============================================================================

_NFT_METADATA   = {'p': [2], 'name': 'TestNFT', 'desc': 'A test NFT'}
_FT_METADATA    = {'p': [1], 'name': 'TestFT', 'ticker': 'TFT', 'decimals': 8}
_DMINT_METADATA = {'p': [1, 4], 'name': 'MineCoin', 'ticker': 'MINE',
                   'maxSupply': 21_000_000, 'reward': 50}
_WAVE_METADATA  = {'p': [2, 5, 11], 'name': 'test.rxd'}
_CONTAINER_META = {'p': [2, 7], 'name': 'My Collection'}
_MUT_METADATA   = {'p': [2, 5], 'name': 'Mutable NFT'}
_DAT_METADATA   = {'p': [3], 'name': 'DataBlob'}
_AUTHORITY_META = {'p': [2, 10], 'name': 'AuthToken'}
_EMBEDDED_FILE  = {'p': [2], 'name': 'NFT with file',
                   'main': {'t': 'image/png', 'b': b'\x89PNG\r\n\x1a\n' + b'\x00' * 50}}

_ALL_METADATA_SAMPLES = [
    _NFT_METADATA, _FT_METADATA, _DMINT_METADATA, _WAVE_METADATA,
    _CONTAINER_META, _MUT_METADATA, _DAT_METADATA, _AUTHORITY_META,
    _EMBEDDED_FILE,
]


# ============================================================================
# _parse_script_pushes tests
# ============================================================================

class TestParseScriptPushes:
    """Unit tests for the low-level push extractor."""

    def test_single_small_push(self):
        script = bytes([0x03]) + b'gly'
        pushes = _parse_script_pushes(script)
        assert pushes == [b'gly']

    def test_multiple_pushes(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        script = bytes([0x03]) + b'gly' + _push(cbor)
        pushes = _parse_script_pushes(script)
        assert len(pushes) == 2
        assert pushes[0] == b'gly'
        assert pushes[1] == cbor

    def test_skips_non_push_opcodes(self):
        # OP_RETURN(6a) + push(data) + OP_DROP(75) should yield 1 push
        data = b'hello'
        script = bytes([0x6A]) + _push(data) + bytes([0x75])
        pushes = _parse_script_pushes(script)
        assert pushes == [data]

    def test_skips_radiant_ref_opcodes(self):
        # d8 + 36 bytes (singleton ref) should be skipped; then a push
        ref_data = bytes(36)
        after = b'payload'
        script = bytes([0xD8]) + ref_data + _push(after)
        pushes = _parse_script_pushes(script)
        assert pushes == [after]

    def test_pushdata1(self):
        data = bytes(range(200))  # 200 bytes — needs OP_PUSHDATA1
        script = _push(data)
        pushes = _parse_script_pushes(script)
        assert len(pushes) == 1
        assert pushes[0] == data

    def test_pushdata2(self):
        data = bytes(300)
        script = bytes([0x4D]) + struct.pack('<H', len(data)) + data
        pushes = _parse_script_pushes(script)
        assert len(pushes) == 1
        assert len(pushes[0]) == 300

    def test_empty_script(self):
        assert _parse_script_pushes(b'') == []

    def test_truncated_push_graceful(self):
        # OP_PUSHBYTES_10 but only 3 bytes of data
        script = bytes([10, 1, 2, 3])
        pushes = _parse_script_pushes(script)
        assert pushes == []  # should break gracefully


# ============================================================================
# v1 FORMAT tests  (OP_PUSHBYTES_3 'gly' + push(CBOR) in scriptSig)
# ============================================================================

class TestV1Format:
    """v1 Glyph envelope — the format used by all mainnet tokens today."""

    def test_minimal_nft(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        assert env['metadata_bytes'] == cbor
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [2]
        assert meta['name'] == 'TestNFT'

    def test_ft_with_ticker(self):
        cbor = cbor2.dumps(_FT_METADATA)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [1]
        assert meta['ticker'] == 'TFT'
        assert meta['decimals'] == 8

    def test_dmint_ft(self):
        cbor = cbor2.dumps(_DMINT_METADATA)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [1, 4]
        assert meta['maxSupply'] == 21_000_000
        info = extract_token_info(meta, env)
        assert info['protocols'] == [1, 4]
        assert get_token_type_id(info['protocols']) == GlyphTokenType.DMINT

    def test_wave_name(self):
        cbor = cbor2.dumps(_WAVE_METADATA)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [2, 5, 11]
        assert get_token_type_id(meta['p']) == GlyphTokenType.WAVE

    def test_embedded_file(self):
        cbor = cbor2.dumps(_EMBEDDED_FILE)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        assert 'main' in meta
        assert meta['main']['t'] == 'image/png'

    def test_with_sig_and_pubkey_prefix(self):
        """Realistic scriptSig: sig + pubkey before the glyph envelope."""
        fake_sig = bytes(72)
        fake_pk  = bytes(33)
        cbor = cbor2.dumps(_NFT_METADATA)
        script = _build_v1_scriptsig(cbor, sig=fake_sig, pubkey=fake_pk)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        meta = parse_glyph_metadata(env)
        assert meta['name'] == 'TestNFT'

    def test_large_cbor_pushdata1(self):
        """CBOR payload > 75 bytes triggers OP_PUSHDATA1."""
        big = {'p': [2], 'name': 'X' * 200, 'desc': 'Y' * 300}
        cbor = cbor2.dumps(big)
        assert len(cbor) > 75
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta['name'] == 'X' * 200

    @pytest.mark.parametrize("sample", _ALL_METADATA_SAMPLES,
                             ids=lambda m: m.get('name', '?'))
    def test_all_protocol_combos_v1(self, sample):
        """Every known protocol combination parses correctly in v1 format."""
        cbor = cbor2.dumps(sample)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta is not None
        assert meta['p'] == sample['p']


# ============================================================================
# v2 STYLE A tests  (OP_RETURN concatenated format, in output scripts)
# ============================================================================

class TestV2StyleA:
    """v2 Style A — 'gly' concatenated with version+flags in OP_RETURN."""

    # ----- Reveals -----

    def test_reveal_nft(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        script = _build_v2a_reveal(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        assert env['version'] == GlyphVersion.V2
        assert env['metadata_bytes'] == cbor
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [2]
        assert meta['name'] == 'TestNFT'

    def test_reveal_ft(self):
        cbor = cbor2.dumps(_FT_METADATA)
        script = _build_v2a_reveal(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        assert meta['ticker'] == 'TFT'

    def test_reveal_with_file_chunks(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        chunks = [b'\x89PNG' + bytes(100), b'\x00IDAT' + bytes(200)]
        script = _build_v2a_reveal(cbor, file_chunks=chunks)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        assert env.get('file_chunks') == chunks

    def test_reveal_op_false_prefix(self):
        """OP_FALSE OP_RETURN variant (0x00 0x6a)."""
        cbor = cbor2.dumps(_NFT_METADATA)
        script = _build_v2a_reveal(cbor, op_false_prefix=True)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        meta = parse_glyph_metadata(env)
        assert meta['name'] == 'TestNFT'

    def test_reveal_with_content_root_flag(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        flags = EnvelopeFlags.HAS_CONTENT_ROOT
        script = _build_v2a_reveal(cbor, flags=flags)
        env = parse_glyph_envelope(script)
        assert env is not None
        # IS_REVEAL should be ORed in by the builder
        assert env['flags'] & EnvelopeFlags.IS_REVEAL

    @pytest.mark.parametrize("sample", _ALL_METADATA_SAMPLES,
                             ids=lambda m: m.get('name', '?'))
    def test_all_protocol_combos_v2a(self, sample):
        cbor = cbor2.dumps(sample)
        script = _build_v2a_reveal(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta is not None
        assert meta['p'] == sample['p']

    # ----- Commits -----

    def test_commit_basic(self):
        commit_hash = bytes(range(32))
        script = _build_v2a_commit(0x00, commit_hash)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is False
        assert env['commit_hash'] == commit_hash.hex()

    def test_commit_with_content_root(self):
        commit_hash = bytes(32)
        content_root = bytes(range(32, 64))
        flags = EnvelopeFlags.HAS_CONTENT_ROOT
        script = _build_v2a_commit(flags, commit_hash, content_root=content_root)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is False
        assert env['commit_hash'] == commit_hash.hex()
        assert env['content_root'] == content_root.hex()

    def test_commit_with_controller(self):
        commit_hash = bytes(32)
        controller = bytes(range(36))
        flags = EnvelopeFlags.HAS_CONTROLLER
        script = _build_v2a_commit(flags, commit_hash, controller=controller)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['controller'] == controller.hex()

    def test_commit_op_false_prefix(self):
        commit_hash = bytes(32)
        script = _build_v2a_commit(0x00, commit_hash, op_false_prefix=True)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is False


# ============================================================================
# v2 STYLE B tests  (OP_3 chunked, in scriptSig)
# ============================================================================

class TestV2StyleB:
    """v2 Style B — OP_3 delimiter with 'gly' in its own push."""

    # ----- Reveals -----

    def test_reveal_nft(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        script = _build_v2b_reveal(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True
        assert env['metadata_bytes'] == cbor
        meta = parse_glyph_metadata(env)
        assert meta['p'] == [2]

    def test_reveal_ft(self):
        cbor = cbor2.dumps(_FT_METADATA)
        script = _build_v2b_reveal(cbor)
        meta = parse_glyph_metadata(parse_glyph_envelope(script))
        assert meta['ticker'] == 'TFT'

    def test_reveal_with_file_chunks(self):
        cbor = cbor2.dumps(_NFT_METADATA)
        chunks = [b'chunk1', b'chunk2']
        script = _build_v2b_reveal(cbor, file_chunks=chunks)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is True

    @pytest.mark.parametrize("sample", _ALL_METADATA_SAMPLES,
                             ids=lambda m: m.get('name', '?'))
    def test_all_protocol_combos_v2b(self, sample):
        cbor = cbor2.dumps(sample)
        script = _build_v2b_reveal(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta is not None
        assert meta['p'] == sample['p']

    # ----- Commits -----

    def test_commit_basic(self):
        commit_hash = bytes(range(32))
        script = _build_v2b_commit(0x00, commit_hash)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is False
        assert env['commit_hash'] == commit_hash.hex()


# ============================================================================
# is_glyph_op_return helper tests
# ============================================================================

class TestIsGlyphOpReturn:

    def test_op_return_with_magic(self):
        script = bytes([0x6A]) + _push(b'gly\x02\x80') + _push(b'\xa0')
        assert is_glyph_op_return(script) is True

    def test_op_false_op_return_with_magic(self):
        script = bytes([0x00, 0x6A]) + _push(b'gly\x02\x80') + _push(b'\xa0')
        assert is_glyph_op_return(script) is True

    def test_op_return_without_magic(self):
        script = bytes([0x6A]) + _push(b'no_magic_here')
        assert is_glyph_op_return(script) is False

    def test_p2pkh_with_magic_in_data(self):
        # 'gly' in payload but NOT an OP_RETURN script
        script = bytes([0x76, 0xA9, 0x14]) + b'gly' + bytes(17) + bytes([0x88, 0xAC])
        assert is_glyph_op_return(script) is False

    def test_empty_script(self):
        assert is_glyph_op_return(b'') is False


# ============================================================================
# Edge cases and negative tests
# ============================================================================

class TestEdgeCases:

    def test_non_glyph_data_returns_none(self):
        p2pkh = bytes([0x76, 0xA9, 0x14]) + bytes(20) + bytes([0x88, 0xAC])
        assert parse_glyph_envelope(p2pkh) is None

    def test_empty_bytes(self):
        assert parse_glyph_envelope(b'') is None

    def test_gly_at_end_with_no_payload(self):
        script = bytes([0x03]) + b'gly'
        assert parse_glyph_envelope(script) is None

    def test_gly_with_tiny_payload(self):
        # Payload too small (1 byte)
        script = bytes([0x03]) + b'gly' + bytes([0x01, 0xFF])
        assert parse_glyph_envelope(script) is None

    def test_gly_with_invalid_cbor(self):
        script = bytes([0x03]) + b'gly' + _push(b'\xFF\xFE\xFD\xFC\xFB')
        env = parse_glyph_envelope(script)
        # Should either be None (CBOR failed) or a v2 structured parse
        # Since 0xFF is not a valid version, should return None
        assert env is None

    def test_v2a_bad_version_byte(self):
        # Version 0x05 is not V1/V2 — should be rejected
        inner = b'gly' + bytes([0x05, 0x00]) + bytes(32)
        script = bytes([0x6A]) + _push(inner)
        assert parse_glyph_envelope(script) is None

    def test_commit_not_flagged_as_reveal(self):
        commit_hash = bytes(32)
        script = _build_v2a_commit(0x00, commit_hash)
        env = parse_glyph_envelope(script)
        assert env is not None
        assert env['is_reveal'] is False
        # parse_glyph_metadata should return None for commits
        assert parse_glyph_metadata(env) is None

    def test_script_with_radiant_refs_before_glyph(self):
        """Ensure Radiant ref opcodes (d8+36) before 'gly' don't break parsing."""
        ref = bytes(36)
        cbor = cbor2.dumps(_NFT_METADATA)
        # d8 + 36-byte ref, then v1 glyph envelope
        script = bytes([0xD8]) + ref + bytes([0x03]) + b'gly' + _push(cbor)
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta['name'] == 'TestNFT'

    def test_multiple_radiant_refs(self):
        """Multiple ref opcodes (d0, d8) before glyph don't interfere."""
        cbor = cbor2.dumps(_FT_METADATA)
        script = (bytes([0xD0]) + bytes(36) +
                  bytes([0xD8]) + bytes(36) +
                  bytes([0x75]) +               # OP_DROP
                  bytes([0x03]) + b'gly' + _push(cbor))
        env = parse_glyph_envelope(script)
        assert env is not None
        meta = parse_glyph_metadata(env)
        assert meta['ticker'] == 'TFT'


# ============================================================================
# extract_token_info / get_token_type_id integration
# ============================================================================

class TestTokenInfoExtraction:

    @pytest.mark.parametrize("protocols, expected_type", [
        ([1],       GlyphTokenType.FT),
        ([2],       GlyphTokenType.NFT),
        ([3],       GlyphTokenType.DAT),
        ([1, 4],    GlyphTokenType.DMINT),
        ([2, 5, 11], GlyphTokenType.WAVE),
        ([2, 7],    GlyphTokenType.CONTAINER),
        ([2, 10],   GlyphTokenType.AUTHORITY),
    ])
    def test_type_mapping(self, protocols, expected_type):
        assert get_token_type_id(protocols) == expected_type

    def test_extract_from_v1_reveal(self):
        cbor = cbor2.dumps(_FT_METADATA)
        script = _build_v1_scriptsig(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        info = extract_token_info(meta, env)
        assert info['name'] == 'TestFT'
        assert info['ticker'] == 'TFT'
        assert info['decimals'] == 8
        assert info['protocols'] == [1]

    def test_extract_from_v2a_reveal(self):
        cbor = cbor2.dumps(_DMINT_METADATA)
        script = _build_v2a_reveal(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        info = extract_token_info(meta, env)
        assert info['protocols'] == [1, 4]
        assert info['dmint']['max_supply'] == 21_000_000

    def test_extract_from_v2b_reveal(self):
        cbor = cbor2.dumps(_WAVE_METADATA)
        script = _build_v2b_reveal(cbor)
        env = parse_glyph_envelope(script)
        meta = parse_glyph_metadata(env)
        info = extract_token_info(meta, env)
        assert info['protocols'] == [2, 5, 11]
        assert info['name'] == 'test.rxd'


# ============================================================================
# Cross-format consistency: same metadata must parse identically in all formats
# ============================================================================

class TestCrossFormatConsistency:
    """The same CBOR payload must yield identical metadata regardless of
    envelope format (v1, v2A, v2B)."""

    @pytest.mark.parametrize("sample", _ALL_METADATA_SAMPLES,
                             ids=lambda m: m.get('name', '?'))
    def test_same_metadata_all_formats(self, sample):
        cbor = cbor2.dumps(sample)

        v1_env  = parse_glyph_envelope(_build_v1_scriptsig(cbor))
        v2a_env = parse_glyph_envelope(_build_v2a_reveal(cbor))
        v2b_env = parse_glyph_envelope(_build_v2b_reveal(cbor))

        for label, env in [('v1', v1_env), ('v2A', v2a_env), ('v2B', v2b_env)]:
            assert env is not None, f"{label} envelope is None for {sample.get('name')}"
            assert env['is_reveal'] is True, f"{label} not reveal"
            meta = parse_glyph_metadata(env)
            assert meta is not None, f"{label} metadata is None"
            assert meta['p'] == sample['p'], (
                f"{label} protocols mismatch: {meta['p']} != {sample['p']}"
            )
            assert meta.get('name') == sample.get('name'), (
                f"{label} name mismatch: {meta.get('name')} != {sample.get('name')}"
            )
