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

    def test_zone_to_dict_coerces_exotic_cbor_values(self, WaveZoneRecords):
        """to_dict() must never leak a non-JSON-native value.

        Zone fields (custom x-* records, TXT lists, desc, ...) come verbatim
        from on-chain CBOR metadata, so an attacker can register a WAVE name
        whose zone embeds a CBOR ``undefined`` (CBOR simple value 23), raw
        ``bytes``, a ``CBORTag``, or a ``set``. Returning that from
        ``wave.resolve`` / the REST zone routes makes the reply un-serialisable
        and hangs the client (aiorpcX silently drops a reply it cannot
        JSON-encode — the exact glyph.get_metadata footgun). to_dict() must
        coerce the whole dict to JSON-safe form.
        """
        import json
        import cbor2

        records = WaveZoneRecords()
        records.address = 'rxd1abc'
        # Truthy non-JSON-native values that pass to_dict()'s `if field:` guards
        # (an empty/undefined value is simply dropped by the guard, which is also
        # safe). These exercise the coercion that prevents the hang.
        records.description = b'raw-desc-bytes'        # raw bytes desc
        records.txt = [b'raw-bytes-record']            # bytes in a TXT list
        records.custom = {
            'x-blob': b'\xde\xad',                     # raw bytes custom record
            'x-tag': cbor2.CBORTag(64, b'\x01'),       # typed-array tag
            'x-set': {7, 8},                           # set
        }

        out = records.to_dict()

        # Must round-trip through json.dumps exactly like the RPC framing does.
        json.dumps(out)
        assert out['address'] == 'rxd1abc'
        assert out['desc'] == b'raw-desc-bytes'.hex()
        assert out['TXT'] == [b'raw-bytes-record'.hex()]
        assert out['x-blob'] == 'dead'
        assert out['x-tag'] == '01'
        assert sorted(out['x-set']) == [7, 8]


class TestWaveIndex:
    """Tests for WaveIndex class."""

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
        env.wave_index = True
        env.wave_genesis_ref = 'a' * 64 + '_0'
        env.wave_hot_names = 1000
        env.reorg_limit = 10
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

    def test_name_info_to_dict_includes_target(self, wave_index):
        """_name_info_to_dict must include top-level 'target' matching cold-cache resolve().
        Photonic Wallet reads result.target || result.zone?.address — both paths must work."""
        from electrumx.server.wave_index import WaveNameInfo, WaveZoneRecords
        info = WaveNameInfo()
        info.ref = bytes(36)
        info.name = 'alice'
        zone = WaveZoneRecords()
        zone.address = 'RXDAliceAddressHere123'
        info.zone = zone
        result = wave_index._name_info_to_dict(info)
        assert 'target' in result
        assert result['target'] == 'RXDAliceAddressHere123'
        assert result['zone'].get('address') == 'RXDAliceAddressHere123'

    def test_name_info_to_dict_target_none_when_no_address(self, wave_index):
        """target should be None when zone has no address set."""
        from electrumx.server.wave_index import WaveNameInfo, WaveZoneRecords
        info = WaveNameInfo()
        info.ref = bytes(36)
        info.name = 'bob'
        info.zone = WaveZoneRecords()
        result = wave_index._name_info_to_dict(info)
        assert 'target' in result
        assert result['target'] is None


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


class TestWaveStats:
    """Tests for WaveIndex.stats() with DB counts."""

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
        env.wave_index = True
        env.wave_genesis_ref = 'a' * 64 + '_0'
        env.wave_hot_names = 1000
        env.reorg_limit = 10
        return env

    @pytest.fixture
    def wave_index(self, mock_db, mock_env):
        from electrumx.server.wave_index import WaveIndex
        return WaveIndex(mock_db, mock_env)

    def test_stats_includes_total_names(self, wave_index):
        """stats() must include total_names (DB + cache)."""
        result = wave_index.stats()
        assert 'total_names' in result
        assert result['total_names'] == 0  # empty DB + empty cache

    def test_stats_includes_genesis_configured(self, wave_index):
        """stats() must report genesis_configured."""
        result = wave_index.stats()
        assert result['genesis_configured'] is True

    def test_stats_cache_counts_add_to_totals(self, wave_index):
        """In-memory cache entries should be reflected in totals."""
        from electrumx.server.wave_index import name_to_hash
        name_hash = name_to_hash('alice')
        wave_index.name_cache[name_hash] = bytes(36)
        wave_index.zone_cache[bytes(36)] = b'\xa0'
        result = wave_index.stats()
        assert result['total_names'] == 1
        assert result['total_zones'] == 1
        assert result['cache_names'] == 1

    def test_stats_db_entries_counted(self, wave_index, mock_db):
        """DB entries should be counted via prefix scan."""
        from electrumx.server.wave_index import WaveDBKeys
        # Simulate 2 WN entries and 1 WZ entry in DB
        def fake_iterator(prefix=None):
            if prefix == WaveDBKeys.NAME:
                return iter([(b'WNfakekey1', b'ref1'), (b'WNfakekey2', b'ref2')])
            elif prefix == WaveDBKeys.ZONE:
                return iter([(b'WZfakekey1', b'zone1')])
            elif prefix == WaveDBKeys.OWNER:
                return iter([])
            return iter([])
        mock_db.utxo_db.iterator = Mock(side_effect=fake_iterator)
        result = wave_index.stats()
        assert result['total_names'] == 2
        assert result['total_zones'] == 1
        assert result['total_owners'] == 0


class TestWaveHotNames:
    """Tests for hot_names cache population on resolve()."""

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
        env.wave_index = True
        env.wave_genesis_ref = 'a' * 64 + '_0'
        env.wave_hot_names = 1000
        env.reorg_limit = 10
        return env

    @pytest.fixture
    def wave_index(self, mock_db, mock_env):
        from electrumx.server.wave_index import WaveIndex
        return WaveIndex(mock_db, mock_env)

    def test_hot_names_empty_initially(self, wave_index):
        """hot_names cache starts empty."""
        assert len(wave_index.hot_names) == 0

    def test_resolve_populates_hot_names(self, wave_index, mock_db):
        """Successful resolve() should populate hot_names cache."""
        try:
            import cbor2
        except ImportError:
            pytest.skip('cbor2 not available')
        from electrumx.server.wave_index import WaveDBKeys, name_to_hash
        ref = bytes(36)
        name_hash = name_to_hash('alice')
        zone_dict = {'address': 'RXDAliceAddress'}
        zone_cbor = cbor2.dumps(zone_dict)
        owner = bytes.fromhex('aa' * 11)

        def fake_get(key):
            if key == WaveDBKeys.NAME + name_hash:
                return ref
            if key == WaveDBKeys.ZONE + ref:
                return zone_cbor
            if key == WaveDBKeys.OWNER + ref:
                return owner
            return None
        mock_db.utxo_db.get = Mock(side_effect=fake_get)

        result = wave_index.resolve('alice')
        assert result is not None
        assert result['target'] == 'RXDAliceAddress'
        assert 'alice' in wave_index.hot_names
        assert wave_index.hot_names['alice'].zone.address == 'RXDAliceAddress'

    def test_resolve_unknown_does_not_populate_hot(self, wave_index):
        """resolve() for unknown name should not populate hot_names."""
        result = wave_index.resolve('unknown')
        assert result is None
        assert len(wave_index.hot_names) == 0


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


class _FakeOutput:
    def __init__(self, pk_script):
        self.pk_script = pk_script


class _FakeTx:
    def __init__(self, outputs):
        self.outputs = outputs
        self.inputs = []


class TestWaveTargetUpdate:
    """A mutable "mod" target update must re-point the canonical name.

    Regression for the bug where a WAVE name target-update confirmed on-chain
    but the indexer kept resolving the GENESIS target forever (mod payloads
    carry no protocol list, so they were skipped), making the name appear to
    "still resolve to the old address" even though it was updated.
    """

    OLD_TARGET = '1BLZiLHCV17EqLWA9S42aFZScCnF1zbnPE'
    NEW_TARGET = '1489r9fYzC9VgueuT16CPWiRRx4HKacYbB'
    # The name's own NFT singleton lives on the CLAIM output (vout 0).
    SINGLETON_REF = bytes.fromhex('22' * 32) + struct.pack('<I', 0)  # 36 bytes

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
        from electrumx.lib.coins import Radiant
        env = Mock()
        env.wave_index = True
        env.wave_genesis_ref = 'a' * 64 + '_0'
        env.wave_hot_names = 1000
        env.reorg_limit = 10
        # Use the real coin so target-address base58 validation is exercised.
        env.coin = Radiant
        env.glyph_index = None
        return env

    @pytest.fixture
    def wave_index(self, mock_db, mock_env):
        from electrumx.server.wave_index import WaveIndex
        return WaveIndex(mock_db, mock_env)

    def _tx(self):
        return _FakeTx([_FakeOutput(bytes.fromhex('76a914' + '11' * 20 + '88ac'))])

    def _register(self, wave_index):
        envelope = {
            'protocols': [2, 5, 11],  # NFT + MUT + WAVE
            'metadata': {'attrs': {
                'name': '12345', 'domain': 'rxd',
                'target': self.OLD_TARGET, 'target_type': 'address',
            }},
        }
        wave_index.process_tx(
            bytes.fromhex('e5' * 32), self._tx(), 410000, 0, envelope,
            output_refs_by_vout={0: [(self.SINGLETON_REF, 1)]},
            spent_singleton_refs=set(),
        )

    def _update(self, wave_index, target=NEW_TARGET):
        envelope = {
            'protocols': [],  # mod payload: no protocol list
            'metadata': {'attrs': {
                'name': '12345', 'domain': 'rxd',
                'target': target, 'target_type': 'address',
            }},
        }
        wave_index.process_tx(
            bytes.fromhex('f7' * 32), self._tx(), 435095, 0, envelope,
            output_refs_by_vout={0: [(self.SINGLETON_REF, 1)]},
            spent_singleton_refs={self.SINGLETON_REF},
        )

    def test_registration_records_singleton(self, wave_index):
        self._register(wave_index)
        from electrumx.server.wave_index import name_to_hash
        assert wave_index.singleton_cache[self.SINGLETON_REF] == name_to_hash('12345')
        assert wave_index.resolve('12345')['target'] == self.OLD_TARGET

    def test_mod_update_repoints_canonical(self, wave_index):
        self._register(wave_index)
        # Prime the hot cache (simulates a resolve before the update).
        assert wave_index.resolve('12345')['target'] == self.OLD_TARGET
        self._update(wave_index)
        result = wave_index.resolve('12345')
        assert result['target'] == self.NEW_TARGET
        assert result['available'] is False

    def test_mod_update_is_not_a_duplicate(self, wave_index):
        self._register(wave_index)
        self._update(wave_index)
        # Updating must not create a duplicate registration.
        assert wave_index._has_duplicates('12345') is False

    def test_mod_update_unknown_singleton_ignored(self, wave_index):
        self._register(wave_index)
        # A mod that spends some OTHER singleton must not touch this name.
        # Use a VALID target so we're exercising the unknown-singleton path,
        # not target-validation rejection.
        envelope = {
            'protocols': [],
            'metadata': {'attrs': {
                'target': self.NEW_TARGET, 'target_type': 'address',
            }},
        }
        wave_index.process_tx(
            bytes.fromhex('cc' * 32), self._tx(), 435100, 0, envelope,
            output_refs_by_vout=None,
            spent_singleton_refs={bytes.fromhex('99' * 32) + struct.pack('<I', 7)},
        )
        assert wave_index.resolve('12345')['target'] == self.OLD_TARGET


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
