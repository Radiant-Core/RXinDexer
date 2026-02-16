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
        """Test parsing a minimal dMint contract script."""
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
        assert result.get('height') is not None
        assert result.get('reward') is not None


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
