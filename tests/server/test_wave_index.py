"""
WAVE Index Integration Tests for RXinDexer

Tests for the WAVE naming system functionality including:
- Name validation
- Name normalization
- Character to index conversion
- Prefix tree indexing
- Name resolution
- Zone record handling
"""

import pytest
from unittest.mock import Mock, MagicMock
import struct


# Import WAVE constants and functions
WAVE_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789-'
WAVE_OUTPUT_COUNT = 38
WAVE_MIN_NAME_LENGTH = 1
WAVE_MAX_NAME_LENGTH = 63
WAVE_MAX_SUBDOMAIN_DEPTH = 127


def char_to_index(char: str) -> int:
    """Convert a character to its WAVE index (0-36)."""
    idx = WAVE_CHARS.find(char.lower())
    if idx == -1:
        raise ValueError(f'Invalid WAVE character: {char}')
    return idx


def index_to_char(index: int) -> str:
    """Convert a WAVE index (0-36) to its character."""
    if index < 0 or index >= len(WAVE_CHARS):
        raise ValueError(f'Invalid WAVE index: {index}')
    return WAVE_CHARS[index]


def char_to_output_index(char: str) -> int:
    """Get the output index for a character's branch (1-37)."""
    return char_to_index(char) + 1


def output_index_to_char(output_index: int) -> str:
    """Get the character for a branch output index."""
    if output_index < 1 or output_index > 37:
        raise ValueError(f'Invalid branch output index: {output_index}')
    return index_to_char(output_index - 1)


class TestWaveCharacterMapping:
    """Tests for WAVE character to index mapping."""

    def test_char_set_length(self):
        """Test WAVE character set has 37 characters."""
        assert len(WAVE_CHARS) == 37

    def test_char_set_contents(self):
        """Test WAVE character set contains correct characters."""
        assert 'a' in WAVE_CHARS
        assert 'z' in WAVE_CHARS
        assert '0' in WAVE_CHARS
        assert '9' in WAVE_CHARS
        assert '-' in WAVE_CHARS
        assert ' ' not in WAVE_CHARS
        assert '_' not in WAVE_CHARS

    def test_char_to_index_lowercase(self):
        """Test char_to_index for lowercase letters."""
        assert char_to_index('a') == 0
        assert char_to_index('z') == 25
        assert char_to_index('m') == 12

    def test_char_to_index_digits(self):
        """Test char_to_index for digits."""
        assert char_to_index('0') == 26
        assert char_to_index('9') == 35

    def test_char_to_index_hyphen(self):
        """Test char_to_index for hyphen."""
        assert char_to_index('-') == 36

    def test_char_to_index_uppercase(self):
        """Test char_to_index handles uppercase."""
        assert char_to_index('A') == 0
        assert char_to_index('Z') == 25

    def test_char_to_index_invalid(self):
        """Test char_to_index raises for invalid characters."""
        with pytest.raises(ValueError):
            char_to_index(' ')
        with pytest.raises(ValueError):
            char_to_index('_')
        with pytest.raises(ValueError):
            char_to_index('!')

    def test_index_to_char(self):
        """Test index_to_char conversion."""
        assert index_to_char(0) == 'a'
        assert index_to_char(25) == 'z'
        assert index_to_char(26) == '0'
        assert index_to_char(35) == '9'
        assert index_to_char(36) == '-'

    def test_index_to_char_invalid(self):
        """Test index_to_char raises for invalid indices."""
        with pytest.raises(ValueError):
            index_to_char(-1)
        with pytest.raises(ValueError):
            index_to_char(37)
        with pytest.raises(ValueError):
            index_to_char(100)

    def test_char_to_output_index(self):
        """Test character to output index mapping."""
        assert char_to_output_index('a') == 1
        assert char_to_output_index('z') == 26
        assert char_to_output_index('0') == 27
        assert char_to_output_index('-') == 37

    def test_output_index_to_char(self):
        """Test output index to character mapping."""
        assert output_index_to_char(1) == 'a'
        assert output_index_to_char(26) == 'z'
        assert output_index_to_char(27) == '0'
        assert output_index_to_char(37) == '-'

    def test_output_index_to_char_invalid(self):
        """Test output_index_to_char raises for invalid indices."""
        with pytest.raises(ValueError):
            output_index_to_char(0)  # 0 is claim token, not a branch
        with pytest.raises(ValueError):
            output_index_to_char(38)

    def test_roundtrip_char_index(self):
        """Test roundtrip conversion char -> index -> char."""
        for char in WAVE_CHARS:
            idx = char_to_index(char)
            result = index_to_char(idx)
            assert result == char

    def test_roundtrip_output_index(self):
        """Test roundtrip conversion char -> output_idx -> char."""
        for char in WAVE_CHARS:
            output_idx = char_to_output_index(char)
            result = output_index_to_char(output_idx)
            assert result == char


class TestWaveNameValidation:
    """Tests for WAVE name validation."""

    @pytest.fixture
    def validate_wave_name(self):
        """Import the validation function."""
        from electrumx.server.wave_index import validate_wave_name
        return validate_wave_name

    def test_valid_simple_name(self, validate_wave_name):
        """Test valid simple names."""
        valid, error = validate_wave_name('alice')
        assert valid is True
        assert error is None

    def test_valid_name_with_numbers(self, validate_wave_name):
        """Test valid names with numbers."""
        valid, error = validate_wave_name('user123')
        assert valid is True

    def test_valid_name_with_hyphen(self, validate_wave_name):
        """Test valid names with hyphens."""
        valid, error = validate_wave_name('my-name')
        assert valid is True

    def test_valid_punycode(self, validate_wave_name):
        """Test valid Punycode names (xn-- prefix allowed)."""
        valid, error = validate_wave_name('xn--nxasmq5b')
        assert valid is True

    def test_invalid_empty_name(self, validate_wave_name):
        """Test empty name is invalid."""
        valid, error = validate_wave_name('')
        assert valid is False
        assert 'empty' in error.lower()

    def test_invalid_too_long(self, validate_wave_name):
        """Test name exceeding max length is invalid."""
        long_name = 'a' * (WAVE_MAX_NAME_LENGTH + 1)
        valid, error = validate_wave_name(long_name)
        assert valid is False
        assert 'length' in error.lower()

    def test_invalid_starts_with_hyphen(self, validate_wave_name):
        """Test name starting with hyphen is invalid."""
        valid, error = validate_wave_name('-invalid')
        assert valid is False
        assert 'hyphen' in error.lower()

    def test_invalid_ends_with_hyphen(self, validate_wave_name):
        """Test name ending with hyphen is invalid."""
        valid, error = validate_wave_name('invalid-')
        assert valid is False
        assert 'hyphen' in error.lower()

    def test_invalid_consecutive_hyphens(self, validate_wave_name):
        """Test consecutive hyphens are invalid (except Punycode)."""
        valid, error = validate_wave_name('in--valid')
        assert valid is False
        assert 'hyphen' in error.lower()

    def test_invalid_special_characters(self, validate_wave_name):
        """Test special characters are invalid."""
        valid, error = validate_wave_name('name_here')
        assert valid is False
        assert 'character' in error.lower()

        valid, error = validate_wave_name('name.here')
        assert valid is False

        valid, error = validate_wave_name('name@here')
        assert valid is False


class TestWaveNameNormalization:
    """Tests for WAVE name normalization."""

    @pytest.fixture
    def normalize_name(self):
        """Import the normalization function."""
        from electrumx.server.wave_index import normalize_name
        return normalize_name

    def test_lowercase_conversion(self, normalize_name):
        """Test uppercase is converted to lowercase."""
        assert normalize_name('ALICE') == 'alice'
        assert normalize_name('Alice') == 'alice'
        assert normalize_name('ALiCe') == 'alice'

    def test_whitespace_stripping(self, normalize_name):
        """Test whitespace is stripped."""
        assert normalize_name(' alice ') == 'alice'
        assert normalize_name('  name  ') == 'name'

    def test_mixed_normalization(self, normalize_name):
        """Test combined normalization."""
        assert normalize_name('  ALICE  ') == 'alice'
        assert normalize_name(' My-Name ') == 'my-name'


class TestWaveNameHashing:
    """Tests for WAVE name hashing."""

    @pytest.fixture
    def name_to_hash(self):
        """Import the hash function."""
        from electrumx.server.wave_index import name_to_hash
        return name_to_hash

    def test_hash_length(self, name_to_hash):
        """Test hash is 16 bytes (truncated SHA256)."""
        result = name_to_hash('alice')
        assert len(result) == 16

    def test_hash_deterministic(self, name_to_hash):
        """Test same name produces same hash."""
        hash1 = name_to_hash('alice')
        hash2 = name_to_hash('alice')
        assert hash1 == hash2

    def test_hash_case_insensitive(self, name_to_hash):
        """Test hash is case-insensitive."""
        hash1 = name_to_hash('alice')
        hash2 = name_to_hash('ALICE')
        hash3 = name_to_hash('Alice')
        assert hash1 == hash2 == hash3

    def test_different_names_different_hash(self, name_to_hash):
        """Test different names produce different hashes."""
        hash1 = name_to_hash('alice')
        hash2 = name_to_hash('bob')
        assert hash1 != hash2


class TestWaveZoneRecords:
    """Tests for WAVE zone record handling."""

    @pytest.fixture
    def WaveZoneRecords(self):
        """Import WaveZoneRecords class."""
        from electrumx.server.wave_index import WaveZoneRecords
        return WaveZoneRecords

    def test_zone_records_init(self, WaveZoneRecords):
        """Test zone records initialization."""
        records = WaveZoneRecords()
        assert records.address is None
        assert records.avatar is None
        assert records.display is None
        assert records.url is None

    def test_zone_records_to_dict_empty(self, WaveZoneRecords):
        """Test empty zone records to dict."""
        records = WaveZoneRecords()
        result = records.to_dict()
        assert result == {}

    def test_zone_records_to_dict_with_data(self, WaveZoneRecords):
        """Test zone records with data to dict."""
        records = WaveZoneRecords()
        records.address = 'rxd1abc...'
        records.display = 'Alice'
        records.url = 'https://example.com'
        
        result = records.to_dict()
        assert result['address'] == 'rxd1abc...'
        assert result['display'] == 'Alice'
        assert result['url'] == 'https://example.com'
        assert 'avatar' not in result  # None values excluded

    def test_zone_records_from_metadata(self, WaveZoneRecords):
        """Test parsing zone records from metadata."""
        metadata = {
            'app': {
                'data': {
                    'zone': {
                        'address': 'rxd1xyz...',
                        'display': 'Bob',
                        'A': '192.168.1.1',
                        'x-custom': 'value'
                    }
                }
            }
        }
        
        records = WaveZoneRecords.from_metadata(metadata)
        assert records.address == 'rxd1xyz...'
        assert records.display == 'Bob'
        assert records.a_record == '192.168.1.1'
        assert records.custom == {'x-custom': 'value'}


class TestWaveIndex:
    """Tests for WaveIndex class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        return db

    @pytest.fixture
    def mock_env(self):
        """Create a mock environment."""
        env = Mock()
        env.wave_index = True
        env.wave_genesis_ref = 'a' * 64 + '_0'
        env.wave_hot_names = 1000
        return env

    @pytest.fixture
    def wave_index(self, mock_db, mock_env):
        """Create a WaveIndex instance."""
        from electrumx.server.wave_index import WaveIndex
        return WaveIndex(mock_db, mock_env)

    def test_wave_index_init(self, wave_index):
        """Test WaveIndex initialization."""
        assert wave_index.enabled is True
        assert wave_index.genesis_ref is not None

    def test_wave_index_disabled(self, mock_db, mock_env):
        """Test WaveIndex when disabled."""
        from electrumx.server.wave_index import WaveIndex
        mock_env.wave_index = False
        index = WaveIndex(mock_db, mock_env)
        assert index.enabled is False

    def test_wave_index_caches_initialized(self, wave_index):
        """Test caches are initialized empty."""
        assert wave_index.tree_cache == {}
        assert wave_index.name_cache == {}
        assert wave_index.zone_cache == {}
        assert wave_index.owner_cache == {}

    def test_resolve_unknown_name(self, wave_index):
        """Test resolving unknown name returns None/error."""
        result = wave_index.resolve('unknown-name')
        # Should return None or dict without data
        assert result is None or (isinstance(result, dict) and not result.get('address'))


class TestWaveNameInfo:
    """Tests for WaveNameInfo class."""

    @pytest.fixture
    def WaveNameInfo(self):
        """Import WaveNameInfo class."""
        from electrumx.server.wave_index import WaveNameInfo
        return WaveNameInfo

    def test_name_info_init(self, WaveNameInfo):
        """Test WaveNameInfo initialization."""
        info = WaveNameInfo()
        assert info.ref == b''
        assert info.name == ''
        assert info.parent_ref is None
        assert info.registration_height == 0
        assert info.is_spent is False

    def test_name_info_serialization(self, WaveNameInfo):
        """Test WaveNameInfo serialization roundtrip."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')
        
        info = WaveNameInfo()
        info.ref = bytes(36)
        info.name = 'testname'
        info.registration_height = 12345
        
        serialized = info.to_bytes()
        deserialized = WaveNameInfo.from_bytes(serialized)
        
        assert deserialized.ref == info.ref
        assert deserialized.name == info.name
        assert deserialized.registration_height == info.registration_height


class TestWaveOutputStructure:
    """Tests for WAVE output structure constants."""

    def test_output_count(self):
        """Test WAVE requires 38 outputs."""
        assert WAVE_OUTPUT_COUNT == 38

    def test_claim_output_is_zero(self):
        """Test claim token is output 0."""
        # Output 0 is claim, outputs 1-37 are branches
        assert char_to_output_index('a') == 1
        assert char_to_output_index('-') == 37

    def test_all_branches_covered(self):
        """Test all 37 characters map to outputs 1-37."""
        used_outputs = set()
        for char in WAVE_CHARS:
            output_idx = char_to_output_index(char)
            assert 1 <= output_idx <= 37
            assert output_idx not in used_outputs
            used_outputs.add(output_idx)
        
        assert len(used_outputs) == 37


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
