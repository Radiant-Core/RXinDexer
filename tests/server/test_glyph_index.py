"""
Glyph Index Tests for RXinDexer

Tests for the Glyph token indexing functionality including:
- Token registration and parsing
- Balance tracking
- History recording
- Database operations
"""

import pytest
from unittest.mock import Mock, MagicMock
import struct

# Import the modules under test
from electrumx.lib.glyph import (
    GLYPH_MAGIC,
    GlyphProtocol,
    GlyphVersion,
    GlyphTokenType,
    parse_glyph_envelope,
    contains_glyph_magic,
    find_glyph_magic,
    validate_protocols,
    get_token_type,
    get_token_type_id,
    is_fungible,
    is_nft,
    is_dmint,
    is_mutable,
    format_ref,
    parse_ref,
    decode_cbor_metadata,
    extract_token_info,
    is_glyph_op_return,
)


class TestGlyphMagic:
    """Tests for Glyph magic byte detection."""

    def test_magic_bytes_value(self):
        """Test magic bytes are correct."""
        assert GLYPH_MAGIC == b'gly'
        assert GLYPH_MAGIC == bytes.fromhex('676c79')

    def test_contains_glyph_magic_positive(self):
        """Test detecting Glyph magic in data."""
        data = b'\x00\x00gly\x02\x00test'
        assert contains_glyph_magic(data) is True

    def test_contains_glyph_magic_negative(self):
        """Test non-Glyph data."""
        data = b'\x00\x00\x00\x00\x00'
        assert contains_glyph_magic(data) is False

    def test_find_glyph_magic_position(self):
        """Test finding magic position."""
        data = b'\x00\x00gly\x02'
        assert find_glyph_magic(data) == 2

    def test_find_glyph_magic_not_found(self):
        """Test magic not found returns -1."""
        data = b'\x00\x00\x00\x00'
        assert find_glyph_magic(data) == -1


class TestGlyphEnvelope:
    """Tests for Glyph envelope parsing."""

    def test_parse_v2_commit_envelope(self):
        """Test parsing a v2 commit envelope."""
        # Build a commit envelope: magic + version + flags + commit_hash
        commit_hash = bytes(32)  # 32 zero bytes
        data = GLYPH_MAGIC + bytes([GlyphVersion.V2, 0x00]) + commit_hash
        
        result = parse_glyph_envelope(data)
        
        assert result is not None
        assert result['version'] == GlyphVersion.V2
        assert result['is_reveal'] is False
        assert result['commit_hash'] == '00' * 32

    def test_parse_v2_reveal_envelope(self):
        """Test parsing a v2 reveal envelope."""
        # Build a reveal envelope: magic + version + flags(reveal) + metadata
        metadata = b'\xa1\x01\x02'  # Simple CBOR: {1: 2}
        data = GLYPH_MAGIC + bytes([GlyphVersion.V2, 0x80]) + metadata
        
        result = parse_glyph_envelope(data)
        
        assert result is not None
        assert result['version'] == GlyphVersion.V2
        assert result['is_reveal'] is True
        assert result['metadata_bytes'] == metadata

    def test_parse_invalid_version(self):
        """Test parsing with invalid version returns None."""
        data = GLYPH_MAGIC + bytes([0x99, 0x00])  # Invalid version
        
        result = parse_glyph_envelope(data)
        
        assert result is None

    def test_parse_no_magic(self):
        """Test parsing without magic returns None."""
        data = b'\x00\x00\x00\x00'
        
        result = parse_glyph_envelope(data)
        
        assert result is None


class TestProtocolValidation:
    """Tests for protocol combination validation."""

    def test_valid_ft_protocol(self):
        """Test valid FT protocol."""
        valid, error = validate_protocols([GlyphProtocol.GLYPH_FT])
        assert valid is True
        assert error is None

    def test_valid_nft_protocol(self):
        """Test valid NFT protocol."""
        valid, error = validate_protocols([GlyphProtocol.GLYPH_NFT])
        assert valid is True
        assert error is None

    def test_valid_dmint_combination(self):
        """Test valid FT + DMINT combination."""
        valid, error = validate_protocols([
            GlyphProtocol.GLYPH_FT, 
            GlyphProtocol.GLYPH_DMINT
        ])
        assert valid is True
        assert error is None

    def test_valid_mutable_nft(self):
        """Test valid NFT + MUT combination."""
        valid, error = validate_protocols([
            GlyphProtocol.GLYPH_NFT, 
            GlyphProtocol.GLYPH_MUT
        ])
        assert valid is True
        assert error is None

    def test_valid_wave_combination(self):
        """Test valid WAVE combination (NFT + MUT + WAVE)."""
        valid, error = validate_protocols([
            GlyphProtocol.GLYPH_NFT,
            GlyphProtocol.GLYPH_MUT,
            GlyphProtocol.GLYPH_WAVE
        ])
        assert valid is True
        assert error is None

    def test_invalid_ft_nft_combination(self):
        """Test invalid FT + NFT combination."""
        valid, error = validate_protocols([
            GlyphProtocol.GLYPH_FT, 
            GlyphProtocol.GLYPH_NFT
        ])
        assert valid is False
        assert 'mutually exclusive' in error

    def test_invalid_dmint_without_ft(self):
        """Test invalid DMINT without FT."""
        valid, error = validate_protocols([GlyphProtocol.GLYPH_DMINT])
        assert valid is False
        assert 'requires FT' in error

    def test_invalid_mut_without_nft(self):
        """Test invalid MUT without NFT."""
        valid, error = validate_protocols([GlyphProtocol.GLYPH_MUT])
        assert valid is False
        assert 'requires NFT' in error

    def test_invalid_wave_without_mut(self):
        """Test invalid WAVE without MUT."""
        valid, error = validate_protocols([
            GlyphProtocol.GLYPH_NFT,
            GlyphProtocol.GLYPH_WAVE
        ])
        assert valid is False
        assert 'requires MUT' in error


class TestTokenTypeDetection:
    """Tests for token type detection."""

    def test_get_token_type_ft(self):
        """Test FT token type string."""
        assert get_token_type([GlyphProtocol.GLYPH_FT]) == 'Fungible Token'

    def test_get_token_type_dmint(self):
        """Test dMint token type string."""
        assert get_token_type([GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]) == 'dMint FT'

    def test_get_token_type_nft(self):
        """Test NFT token type string."""
        assert get_token_type([GlyphProtocol.GLYPH_NFT]) == 'NFT'

    def test_get_token_type_mutable_nft(self):
        """Test mutable NFT token type string."""
        assert get_token_type([GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_MUT]) == 'Mutable NFT'

    def test_get_token_type_wave(self):
        """Test WAVE token type string."""
        protocols = [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_MUT, GlyphProtocol.GLYPH_WAVE]
        assert get_token_type(protocols) == 'WAVE Name'

    def test_get_token_type_id_ft(self):
        """Test FT token type ID."""
        assert get_token_type_id([GlyphProtocol.GLYPH_FT]) == GlyphTokenType.FT

    def test_get_token_type_id_nft(self):
        """Test NFT token type ID."""
        assert get_token_type_id([GlyphProtocol.GLYPH_NFT]) == GlyphTokenType.NFT

    def test_get_token_type_id_dmint(self):
        """Test dMint token type ID."""
        assert get_token_type_id([GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]) == GlyphTokenType.DMINT

    def test_get_token_type_id_wave(self):
        """Test WAVE token type ID."""
        protocols = [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_MUT, GlyphProtocol.GLYPH_WAVE]
        assert get_token_type_id(protocols) == GlyphTokenType.WAVE


class TestProtocolHelpers:
    """Tests for protocol helper functions."""

    def test_is_fungible(self):
        """Test is_fungible helper."""
        assert is_fungible([GlyphProtocol.GLYPH_FT]) is True
        assert is_fungible([GlyphProtocol.GLYPH_NFT]) is False

    def test_is_nft(self):
        """Test is_nft helper."""
        assert is_nft([GlyphProtocol.GLYPH_NFT]) is True
        assert is_nft([GlyphProtocol.GLYPH_FT]) is False

    def test_is_dmint(self):
        """Test is_dmint helper."""
        assert is_dmint([GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]) is True
        assert is_dmint([GlyphProtocol.GLYPH_FT]) is False

    def test_is_mutable(self):
        """Test is_mutable helper."""
        assert is_mutable([GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_MUT]) is True
        assert is_mutable([GlyphProtocol.GLYPH_NFT]) is False


class TestRefFormatting:
    """Tests for ref string formatting."""

    def test_format_ref(self):
        """Test ref formatting."""
        txid = 'a' * 64
        vout = 0
        assert format_ref(txid, vout) == 'a' * 64 + '_0'

    def test_parse_ref(self):
        """Test ref parsing."""
        ref = 'a' * 64 + '_1'
        txid, vout = parse_ref(ref)
        assert txid == 'a' * 64
        assert vout == 1


class TestOpReturnDetection:
    """Tests for OP_RETURN script detection."""

    def test_is_glyph_op_return_positive(self):
        """Test detecting Glyph in OP_RETURN."""
        # OP_RETURN + push + glyph magic + data
        script = bytes([0x6a, 0x06]) + GLYPH_MAGIC + b'\x02\x00'
        assert is_glyph_op_return(script) is True

    def test_is_glyph_op_return_false_op_return(self):
        """Test OP_FALSE OP_RETURN with Glyph."""
        # OP_FALSE OP_RETURN + push + glyph magic + data
        script = bytes([0x00, 0x6a, 0x06]) + GLYPH_MAGIC + b'\x02\x00'
        assert is_glyph_op_return(script) is True

    def test_is_glyph_op_return_no_magic(self):
        """Test OP_RETURN without Glyph magic."""
        script = bytes([0x6a, 0x04, 0x00, 0x00, 0x00, 0x00])
        assert is_glyph_op_return(script) is False


class TestCBORMetadata:
    """Tests for CBOR metadata parsing."""

    def test_decode_cbor_valid(self):
        """Test decoding valid CBOR."""
        try:
            import cbor2
            # Simple CBOR: {"v": 2, "p": [2]}
            cbor_data = cbor2.dumps({'v': 2, 'p': [2]})
            result = decode_cbor_metadata(cbor_data)
            assert result is not None
            assert result['v'] == 2
            assert result['p'] == [2]
        except ImportError:
            pytest.skip('cbor2 not available')

    def test_decode_cbor_invalid(self):
        """Test decoding invalid CBOR returns None."""
        result = decode_cbor_metadata(b'\xff\xff\xff')
        assert result is None

    def test_decode_cbor_non_dict(self):
        """Test decoding CBOR that is not a map returns None."""
        try:
            import cbor2
            cbor_data = cbor2.dumps(1)
            result = decode_cbor_metadata(cbor_data)
            assert result is None
        except ImportError:
            pytest.skip('cbor2 not available')

    def test_extract_token_info_v2_nft(self):
        """Test extracting token info from v2 NFT metadata."""
        metadata = {
            'v': 2,
            'p': [2],
            'name': 'Test NFT',
            'attrs': [{'name': 'Rarity', 'value': 'Rare'}]
        }
        
        info = extract_token_info(metadata)
        
        assert info['version'] == 2
        assert info['protocols'] == [2]
        assert info['name'] == 'Test NFT'
        assert len(info['attrs']) == 1

    def test_extract_token_info_v2_ft(self):
        """Test extracting token info from v2 FT metadata."""
        metadata = {
            'v': 2,
            'p': [1],
            'name': 'Test Token',
            'ticker': 'TEST',
            'decimals': 8
        }
        
        info = extract_token_info(metadata)
        
        assert info['version'] == 2
        assert info['protocols'] == [1]
        assert info['name'] == 'Test Token'
        assert info['ticker'] == 'TEST'
        assert info['decimals'] == 8

    def test_extract_token_info_v1_legacy(self):
        """Test extracting token info from v1 legacy metadata."""
        metadata = {
            'v': 1,
            'type': 'nft',
            'name': 'Legacy NFT'
        }
        
        info = extract_token_info(metadata)
        
        assert info['version'] == 1
        assert info['protocols'] == [GlyphProtocol.GLYPH_NFT]
        assert info['name'] == 'Legacy NFT'

    def test_extract_token_info_dmint(self):
        """Test extracting dMint-specific info."""
        metadata = {
            'v': 2,
            'p': [1, 4],
            'name': 'dMint Token',
            'ticker': 'DMNT',
            'decimals': 8,
            'algorithm': 0x01,
            'startDiff': 500000,
            'maxSupply': 21000000,
            'reward': 50,
            'daa': {'mode': 0x02, 'halflife': 3600}
        }
        
        info = extract_token_info(metadata)
        
        assert 'dmint' in info
        assert info['dmint']['algorithm'] == 0x01
        assert info['dmint']['max_supply'] == 21000000


class TestGlyphIndexIntegration:
    """Integration tests for GlyphIndex class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        return db

    @pytest.fixture
    def mock_env(self):
        """Create a mock environment."""
        env = Mock()
        env.glyph_index = True
        env.reorg_limit = 10
        return env

    def test_glyph_index_init(self, mock_db, mock_env):
        """Test GlyphIndex initialization."""
        from electrumx.server.glyph_index import GlyphIndex
        
        index = GlyphIndex(mock_db, mock_env)
        
        assert index.enabled is True
        assert index.token_cache == {}
        assert index.balance_cache == {}

    def test_glyph_index_disabled(self, mock_db, mock_env):
        """Test GlyphIndex when disabled."""
        from electrumx.server.glyph_index import GlyphIndex
        
        mock_env.glyph_index = False
        index = GlyphIndex(mock_db, mock_env)
        
        assert index.enabled is False


class TestDmintContractStateParsing:
    """Level 2 tests: parse_dmint_contract_state() from glyph.py."""

    def test_scriptnum_to_int_positive(self):
        """Test CScriptNum decoding for positive values."""
        from electrumx.lib.glyph import _scriptnum_to_int
        assert _scriptnum_to_int(b'') == 0
        assert _scriptnum_to_int(b'\x01') == 1
        assert _scriptnum_to_int(b'\x7f') == 127
        assert _scriptnum_to_int(b'\x80\x00') == 128  # 128 needs sign byte
        assert _scriptnum_to_int(b'\xff\x00') == 255
        assert _scriptnum_to_int(b'\x00\x01') == 256

    def test_scriptnum_to_int_negative(self):
        """Test CScriptNum decoding for negative values."""
        from electrumx.lib.glyph import _scriptnum_to_int
        assert _scriptnum_to_int(b'\x81') == -1  # 0x80 sign | 0x01 magnitude
        assert _scriptnum_to_int(b'\xff') == -127
        assert _scriptnum_to_int(b'\x80\x80') == -128

    def test_parse_dmint_contract_state_too_short(self):
        """Test that short scripts return None."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        assert parse_dmint_contract_state(b'') is None
        assert parse_dmint_contract_state(b'\x00' * 50) is None

    def test_parse_dmint_contract_state_no_d8(self):
        """Test that scripts without OP_PUSHINPUTREFSINGLETON return None."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        script = b'\x00' * 100
        assert parse_dmint_contract_state(script) is None

    def test_parse_dmint_contract_state_basic(self):
        """Test parsing a minimal V1 dMint contract script."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        # Build a minimal contract script:
        # <height:4B push> d8<contractRef:36B> d0<tokenRef:36B> <maxHeight> <reward> <target> bd ...
        contract_ref = b'\xaa' * 36
        token_ref = b'\xbb' * 36
        script = (
            b'\x04\x01\x00\x00\x00'  # push 4 bytes: height=1
            + b'\xd8' + contract_ref    # OP_PUSHINPUTREFSINGLETON + 36B ref
            + b'\xd0' + token_ref        # OP_PUSHINPUTREF + 36B ref
            + b'\x02\xe8\x03'           # push 2 bytes: maxHeight=1000
            + b'\x01\x32'               # push 1 byte: reward=50
            + b'\x03\xa0\x86\x01'       # push 3 bytes: target=100000
            + b'\xbd'                    # OP_CHECKTEMPLATEVERIFY
            + b'\x00' * 20              # contract bytecode padding
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['contract_ref'] == contract_ref.hex()
        assert result['token_ref'] == token_ref.hex()
        assert result['height'] == 1
        assert result['max_height'] == 1000
        assert result['reward'] == 50
        assert result['target'] == 100000
        # V1 should NOT have V2-specific fields
        assert 'daa_mode' not in result

    def test_parse_dmint_contract_state_v2_blake3_asert(self):
        """Test parsing a V2 dMint contract with blake3 algo and ASERT DAA."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        contract_ref = b'\xaa' * 36
        token_ref = b'\xbb' * 36
        # V2 layout: height, contractRef, tokenRef, maxHeight, reward,
        #            algoId, daaMode, targetTime, lastTime, target
        script = (
            b'\x04\x05\x00\x00\x00'     # push 4 bytes: height=5
            + b'\xd8' + contract_ref      # OP_PUSHINPUTREFSINGLETON + 36B ref
            + b'\xd0' + token_ref          # OP_PUSHINPUTREF + 36B ref
            + b'\x02\xe8\x03'             # push 2 bytes: maxHeight=1000
            + b'\x01\x32'                 # push 1 byte: reward=50
            + b'\x51'                      # OP_1: algoId=1 (blake3)
            + b'\x52'                      # OP_2: daaMode=2 (asert)
            + b'\x01\x3c'                 # push 1 byte: targetTime=60
            + b'\x04\x00\xf1\x53\x65'    # push 4 bytes: lastTime=1700000000
            + b'\x03\x40\x42\x0f'         # push 3 bytes: target=1000000
            + b'\xbd'                      # OP_CHECKTEMPLATEVERIFY
            + b'\x00' * 20                # contract bytecode padding
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['height'] == 5
        assert result['max_height'] == 1000
        assert result['reward'] == 50
        assert result['algo_id'] == 1       # blake3
        assert result['daa_mode'] == 2      # asert
        assert result['target_time'] == 60
        assert result['last_time'] == 1700000000
        assert result['target'] == 1000000

    def test_parse_dmint_contract_state_v2_sha256d_fixed(self):
        """Test parsing a V2 dMint contract with sha256d algo and fixed DAA (both zero)."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        contract_ref = b'\xcc' * 36
        token_ref = b'\xdd' * 36
        # algoId=0 (sha256d) and daaMode=0 (fixed) are encoded as OP_0 (0x00)
        script = (
            b'\x04\x00\x00\x00\x00'     # push 4 bytes: height=0 (genesis)
            + b'\xd8' + contract_ref      # OP_PUSHINPUTREFSINGLETON + 36B ref
            + b'\xd0' + token_ref          # OP_PUSHINPUTREF + 36B ref
            + b'\x02\xe8\x03'             # push 2 bytes: maxHeight=1000
            + b'\x01\x32'                 # push 1 byte: reward=50
            + b'\x00'                      # OP_0: algoId=0 (sha256d)
            + b'\x00'                      # OP_0: daaMode=0 (fixed)
            + b'\x01\x3c'                 # push 1 byte: targetTime=60
            + b'\x04\x00\xf1\x53\x65'    # push 4 bytes: lastTime=1700000000
            + b'\x03\x40\x42\x0f'         # push 3 bytes: target=1000000
            + b'\xbd'                      # OP_CHECKTEMPLATEVERIFY
            + b'\x00' * 20                # contract bytecode padding
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['algo_id'] == 0       # sha256d
        assert result['daa_mode'] == 0      # fixed
        assert result['target'] == 1000000  # NOT algoId — target is at position [7]
        assert result['target_time'] == 60
        assert result['last_time'] == 1700000000

    def test_parse_dmint_contract_state_v2_k12_lwma(self):
        """Test parsing a V2 dMint contract with K12 algo and LWMA DAA."""
        from electrumx.lib.glyph import parse_dmint_contract_state
        contract_ref = b'\xee' * 36
        token_ref = b'\xff' * 36
        script = (
            b'\x04\x0a\x00\x00\x00'     # push 4 bytes: height=10
            + b'\xd8' + contract_ref
            + b'\xd0' + token_ref
            + b'\x02\x10\x27'             # push 2 bytes: maxHeight=10000
            + b'\x02\xc8\x00'             # push 2 bytes: reward=200
            + b'\x52'                      # OP_2: algoId=2 (k12)
            + b'\x53'                      # OP_3: daaMode=3 (lwma)
            + b'\x01\x2d'                 # push 1 byte: targetTime=45
            + b'\x04\x50\xf1\x53\x65'    # push 4 bytes: lastTime=1700000080
            + b'\x04\x80\x96\x98\x00'    # push 4 bytes: target=10000000
            + b'\xbd'
            + b'\x00' * 20
        )
        result = parse_dmint_contract_state(script)
        assert result is not None
        assert result['algo_id'] == 2       # k12
        assert result['daa_mode'] == 3      # lwma
        assert result['target_time'] == 45
        assert result['target'] == 10000000
        assert result['reward'] == 200


class TestDmintMetadataCopy:
    """Level 1 tests: dMint metadata fields copied to GlyphTokenInfo at deploy time."""

    def test_extract_token_info_dmint_fields(self):
        """Test that extract_token_info returns all dMint fields."""
        metadata = {
            'v': 2,
            'p': [1, 4],  # FT + DMINT
            'name': 'TestMint',
            'ticker': 'TMNT',
            'decimals': 8,
            'algorithm': 0x01,
            'startDiff': 500000,
            'maxSupply': 21000000,
            'reward': 50,
            'daa': {'mode': 0x02, 'halflife': 3600}
        }
        info = extract_token_info(metadata)
        assert 'dmint' in info
        dmint = info['dmint']
        assert dmint['algorithm'] == 0x01
        assert dmint['start_difficulty'] == 500000
        assert dmint['max_supply'] == 21000000
        assert dmint['reward'] == 50
        assert dmint['daa_mode'] == 0x02
        assert dmint['halflife'] == 3600

    def test_token_info_roundtrip(self):
        """Test GlyphTokenInfo serialize/deserialize preserves dMint fields."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')

        from electrumx.server.glyph_index import GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        token = GlyphTokenInfo()
        token.ref = b'\xaa' * 36
        token.protocols = [GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]
        token.token_type = GlyphTokenType.DMINT
        token.name = 'TestDmint'
        token.ticker = 'TDMT'
        token.algorithm = 0x01
        token.start_difficulty = 500000
        token.current_difficulty = 600000
        token.reward = 50
        token.daa_mode = 0x02
        token.halving_interval = 3600
        token.mint_count = 42
        token.total_supply = 21000000
        token.mined_supply = 2100
        token.current_supply = 2100
        token.contract_ref = 'cc' * 36

        data = token.to_bytes()
        restored = GlyphTokenInfo.from_bytes(data)

        assert restored.algorithm == 0x01
        assert restored.start_difficulty == 500000
        assert restored.current_difficulty == 600000
        assert restored.reward == 50
        assert restored.daa_mode == 0x02
        assert restored.halving_interval == 3600
        assert restored.mint_count == 42
        assert restored.mined_supply == 2100
        assert restored.contract_ref == 'cc' * 36
        assert restored.total_supply == 21000000


class TestDmintMintProcessing:
    """Level 3 tests: _process_mint() and mint event detection."""

    @pytest.fixture
    def mock_db(self):
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        return db

    @pytest.fixture
    def mock_env(self):
        env = Mock()
        env.glyph_index = True
        env.reorg_limit = 10
        return env

    def test_process_mint_non_dmint_token_skipped(self, mock_db, mock_env):
        """Test that _process_mint skips non-dMint tokens."""
        from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        index = GlyphIndex(mock_db, mock_env)

        # Create a regular FT token (not dMint)
        token_ref = b'\xaa' * 36
        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = [GlyphProtocol.GLYPH_FT]
        token.token_type = GlyphTokenType.FT
        index.token_cache[token_ref] = token
        index.token_height[token_ref] = 100

        # Create a mock tx with no real outputs
        tx = Mock()
        tx.outputs = []

        # Should not crash and should not add history
        index._process_mint(b'\x00' * 32, tx, 101, 0, token_ref, {})
        assert len(index.history_cache) == 0

    def test_process_mint_updates_supply(self, mock_db, mock_env):
        """Test that _process_mint correctly updates supply and mint count."""
        from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        index = GlyphIndex(mock_db, mock_env)

        token_ref = b'\xaa' * 36
        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = [GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]
        token.token_type = GlyphTokenType.DMINT
        token.total_supply = 21000000
        token.mined_supply = 0
        token.current_supply = 0
        token.mint_count = 0
        token.reward = 50
        index.token_cache[token_ref] = token
        index.token_height[token_ref] = 100

        # Build a mock tx with one FT output carrying the token ref
        mock_output = Mock()
        mock_output.value = 50  # 50 satoshis minted
        # Script: d0 + 36-byte ref (OP_PUSHINPUTREF)
        mock_output.pk_script = b'\xd0' + token_ref + b'\x00' * 20

        tx = Mock()
        tx.outputs = [mock_output]

        tx_hash = b'\xbb' * 32
        index._process_mint(tx_hash, tx, 101, 0, token_ref, {})

        # Verify updates
        updated_token = index.token_cache[token_ref]
        assert updated_token.mint_count == 1
        assert updated_token.mined_supply == 50
        assert updated_token.current_supply == 50

        # Verify MINT history event was recorded
        assert len(index.history_cache) == 1
        _, _, value = index.history_cache[0]
        assert value[0] == 1  # GlyphEventType.MINT

    def test_process_mint_no_minted_amount_skipped(self, mock_db, mock_env):
        """Test that _process_mint skips when minted amount is 0."""
        from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        index = GlyphIndex(mock_db, mock_env)

        token_ref = b'\xaa' * 36
        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = [GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]
        token.token_type = GlyphTokenType.DMINT
        token.mint_count = 0
        index.token_cache[token_ref] = token
        index.token_height[token_ref] = 100

        # Build a mock tx with NO FT outputs matching the token ref
        mock_output = Mock()
        mock_output.value = 100
        mock_output.pk_script = b'\xd8' + b'\xcc' * 36  # Different ref, singleton

        tx = Mock()
        tx.outputs = [mock_output]

        index._process_mint(b'\xbb' * 32, tx, 101, 0, token_ref, {})

        # Should NOT have updated
        assert token.mint_count == 0
        assert len(index.history_cache) == 0

    def test_fully_mined_marks_spent(self, mock_db, mock_env):
        """Test that token is marked as spent when fully mined."""
        from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        index = GlyphIndex(mock_db, mock_env)

        token_ref = b'\xaa' * 36
        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = [GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_DMINT]
        token.token_type = GlyphTokenType.DMINT
        token.total_supply = 100
        token.mined_supply = 50
        token.current_supply = 50
        token.mint_count = 1
        token.is_spent = False
        index.token_cache[token_ref] = token
        index.token_height[token_ref] = 100

        # Mint the remaining 50
        mock_output = Mock()
        mock_output.value = 50
        mock_output.pk_script = b'\xd0' + token_ref + b'\x00' * 20

        tx = Mock()
        tx.outputs = [mock_output]

        index._process_mint(b'\xbb' * 32, tx, 101, 0, token_ref, {})

        updated = index.token_cache[token_ref]
        assert updated.mined_supply == 100
        assert updated.is_spent is True


# =============================================================================
# Image / Content Field Tests
# =============================================================================

class TestImageExtraction:
    """Tests for remote/embed image field extraction in _index_token_reveal."""

    @pytest.fixture
    def index(self):
        """Create a GlyphIndex with mock DB and env."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')
        from electrumx.server.glyph_index import GlyphIndex
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        env = Mock()
        env.glyph_index = True
        env.reorg_limit = 10
        return GlyphIndex(db, env)

    def _make_reveal_envelope(self, cbor_payload: bytes) -> dict:
        """Build a minimal reveal envelope dict as produced by parse_glyph_envelope."""
        return {
            'version': 2,
            'flags': 0x80,
            'is_reveal': True,
            'metadata_bytes': cbor_payload,
        }

    def _make_tx(self, token_ref: bytes) -> Mock:
        """Build a minimal mock transaction with one NFT output."""
        output = Mock()
        output.value = 1000
        output.pk_script = b'\xd8' + token_ref
        tx = Mock()
        tx.outputs = [output]
        tx.inputs = []
        return tx

    # ------------------------------------------------------------------
    # remote field tests
    # ------------------------------------------------------------------

    def test_remote_ipfs_image_populates_icon_ref(self, index):
        """remote.u (IPFS URL) is stored in icon_ref."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x01' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'IPFS NFT',
            'remote': {'t': 'image/png', 'u': 'ipfs://QmTest123'},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_ref == 'ipfs://QmTest123'
        assert token.icon_type == 'image/png'

    def test_remote_http_image_populates_icon_ref(self, index):
        """remote.u (HTTP URL) is stored in icon_ref."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x02' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'HTTP NFT',
            'remote': {'t': 'image/webp', 'u': 'https://example.com/img.webp'},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_ref == 'https://example.com/img.webp'
        assert token.icon_type == 'image/webp'

    def test_remote_hashstamp_sets_icon_size(self, index):
        """remote.hs (hashstamp bytes) length is stored in icon_size."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x03' * 36
        hashstamp = b'\xff' * 512  # 512-byte on-chain thumbnail
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Hashstamp NFT',
            'remote': {'t': 'image/png', 'u': 'ipfs://QmAbc', 'hs': hashstamp},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_size == 512

    def test_remote_hash_stored_as_embedded_data_hash(self, index):
        """remote.h (SHA-256 bytes) is stored in embedded_data_hash."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x04' * 36
        sha256_bytes = b'\xab' * 32
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Hashed NFT',
            'remote': {'t': 'image/jpeg', 'u': 'ipfs://QmXyz', 'h': sha256_bytes},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.embedded_data_hash == sha256_bytes

    def test_remote_shorthand_rm_key(self, index):
        """remote stored under 'rm' shorthand key is also parsed."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x05' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'RM NFT',
            'rm': {'t': 'image/svg+xml', 'u': 'https://example.com/icon.svg'},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_ref == 'https://example.com/icon.svg'
        assert token.icon_type == 'image/svg+xml'

    # ------------------------------------------------------------------
    # embed field tests
    # ------------------------------------------------------------------

    def test_embed_sets_icon_type_and_size(self, index):
        """embed.t and len(embed.b) are stored in icon_type and icon_size."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x06' * 36
        img_bytes = b'\x89PNG' + b'\x00' * 100  # fake PNG
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Embedded NFT',
            'embed': {'t': 'image/png', 'b': img_bytes},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_type == 'image/png'
        assert token.icon_size == len(img_bytes)
        assert token.icon_ref == 'embedded'

    def test_embed_shorthand_em_key(self, index):
        """embed stored under 'em' shorthand key is also parsed."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x07' * 36
        img_bytes = b'\x00\x01\x02\x03' * 10
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'EM NFT',
            'em': {'t': 'image/gif', 'b': img_bytes},
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        tx = self._make_tx(token_ref)
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_type == 'image/gif'
        assert token.icon_size == 40
        assert token.icon_ref == 'embedded'

    # ------------------------------------------------------------------
    # no-image token
    # ------------------------------------------------------------------

    def test_no_image_fields_remain_none(self, index):
        """Tokens without remote/embed have None image fields."""
        import cbor2
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x08' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_FT],
            'name': 'Plain FT', 'ticker': 'PLN',
        }
        envelope = self._make_reveal_envelope(cbor2.dumps(metadata))
        output = Mock()
        output.value = 1000000
        output.pk_script = b'\xd0' + token_ref
        tx = Mock()
        tx.outputs = [output]
        tx.inputs = []
        index._index_token_reveal(token_ref, b'\x00' * 32, 0, 1, 0, envelope, metadata, tx)

        token = index.token_cache[token_ref]
        assert token.icon_ref is None
        assert token.icon_type is None
        assert token.icon_size == 0
        assert token.embedded_data_hash is None

    # ------------------------------------------------------------------
    # GlyphTokenInfo serialization roundtrip with image fields
    # ------------------------------------------------------------------

    def test_image_fields_survive_cbor_roundtrip(self):
        """icon_ref, icon_type, icon_size, embedded_data_hash survive to_bytes/from_bytes."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')
        from electrumx.server.glyph_index import GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType

        token = GlyphTokenInfo()
        token.ref = b'\xaa' * 36
        token.protocols = [GlyphProtocol.GLYPH_NFT]
        token.token_type = GlyphTokenType.NFT
        token.name = 'Image NFT'
        token.icon_ref = 'ipfs://QmTest'
        token.icon_type = 'image/png'
        token.icon_size = 512
        token.embedded_data_hash = b'\xab' * 32

        restored = GlyphTokenInfo.from_bytes(token.to_bytes())

        assert restored.icon_ref == 'ipfs://QmTest'
        assert restored.icon_type == 'image/png'
        assert restored.icon_size == 512
        assert restored.embedded_data_hash == b'\xab' * 32


class TestTokenToDictImages:
    """Tests for remote/embed fields in _token_to_dict API response."""

    @pytest.fixture
    def index(self):
        """Create a GlyphIndex with mock DB and env."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')
        from electrumx.server.glyph_index import GlyphIndex
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        env = Mock()
        env.glyph_index = True
        env.reorg_limit = 10
        return GlyphIndex(db, env)

    def _make_token_with_metadata(self, index, token_ref: bytes, metadata: dict):
        """Register a token and store its CBOR metadata in the index cache."""
        import cbor2
        from electrumx.server.glyph_index import GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType
        from electrumx.lib.hash import sha256

        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = metadata.get('p', [GlyphProtocol.GLYPH_NFT])
        token.token_type = GlyphTokenType.NFT
        token.name = metadata.get('name', 'Test')
        token.deploy_height = 100
        token.deploy_txid = b'\x00' * 32

        cbor_bytes = cbor2.dumps(metadata)
        token.metadata_hash = sha256(cbor_bytes)
        index.metadata_cache[token.metadata_hash] = cbor_bytes
        index.token_cache[token_ref] = token
        return token

    def test_remote_url_in_api_response(self, index):
        """_token_to_dict includes remote.url when token has remote image."""
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x10' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Remote NFT',
            'remote': {'t': 'image/png', 'u': 'ipfs://QmABC123'},
        }
        self._make_token_with_metadata(index, token_ref, metadata)
        result = index._token_to_dict(index.token_cache[token_ref])

        assert 'remote' in result
        assert result['remote']['url'] == 'ipfs://QmABC123'
        assert result['remote']['type'] == 'image/png'

    def test_remote_hashstamp_in_api_response(self, index):
        """_token_to_dict includes remote.hashstamp as hex string."""
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x11' * 36
        hs_bytes = b'\xde\xad\xbe\xef' * 8  # 32-byte fake hashstamp
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Hashstamp NFT',
            'remote': {'t': 'image/webp', 'u': 'ipfs://QmXYZ', 'hs': hs_bytes},
        }
        self._make_token_with_metadata(index, token_ref, metadata)
        result = index._token_to_dict(index.token_cache[token_ref])

        assert 'remote' in result
        assert result['remote']['hashstamp'] == hs_bytes.hex()

    def test_remote_hash_in_api_response(self, index):
        """_token_to_dict includes remote.hash as hex string."""
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x12' * 36
        sha_bytes = b'\xca\xfe' * 16  # 32-byte fake SHA-256
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Hashed NFT',
            'remote': {'t': 'image/jpeg', 'u': 'https://cdn.example.com/img.jpg', 'h': sha_bytes},
        }
        self._make_token_with_metadata(index, token_ref, metadata)
        result = index._token_to_dict(index.token_cache[token_ref])

        assert 'remote' in result
        assert result['remote']['hash'] == sha_bytes.hex()

    def test_embed_data_in_api_response(self, index):
        """_token_to_dict includes embed.data as hex and embed.type/size."""
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x13' * 36
        img_bytes = b'\x89PNG' + b'\x00' * 60
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_NFT],
            'name': 'Embedded NFT',
            'embed': {'t': 'image/png', 'b': img_bytes},
        }
        self._make_token_with_metadata(index, token_ref, metadata)
        result = index._token_to_dict(index.token_cache[token_ref])

        assert 'embed' in result
        assert result['embed']['type'] == 'image/png'
        assert result['embed']['size'] == len(img_bytes)
        assert result['embed']['data'] == img_bytes.hex()

    def test_no_image_no_remote_embed_keys(self, index):
        """_token_to_dict omits remote/embed keys when no image metadata."""
        from electrumx.lib.glyph import GlyphProtocol
        token_ref = b'\x14' * 36
        metadata = {
            'v': 2, 'p': [GlyphProtocol.GLYPH_FT],
            'name': 'Plain FT', 'ticker': 'PLN',
        }
        self._make_token_with_metadata(index, token_ref, metadata)
        result = index._token_to_dict(index.token_cache[token_ref])

        assert 'remote' not in result
        assert 'embed' not in result

    def test_no_metadata_hash_no_remote_embed(self, index):
        """_token_to_dict omits remote/embed when token has no metadata_hash."""
        from electrumx.server.glyph_index import GlyphTokenInfo
        from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType
        token_ref = b'\x15' * 36
        token = GlyphTokenInfo()
        token.ref = token_ref
        token.protocols = [GlyphProtocol.GLYPH_NFT]
        token.token_type = GlyphTokenType.NFT
        token.name = 'No Meta NFT'
        token.deploy_height = 100
        token.deploy_txid = b'\x00' * 32
        # metadata_hash left as default empty bytes
        index.token_cache[token_ref] = token

        result = index._token_to_dict(token)

        assert 'remote' not in result
        assert 'embed' not in result
