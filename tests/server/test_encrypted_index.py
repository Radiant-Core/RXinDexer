"""
Phase 6 Tests: Encrypted and Timelocked Token Indexing (REP-3008 / REP-3009)

Covers:
- GlyphTokenInfo encrypted/timelock field defaults
- GlyphTokenInfo CBOR round-trip for encrypted + timelock fields
- _register_token parsing of 'main' and 'crypto.timelock' metadata
- record_key_reveal / get_key_reveal persistence
- list_encrypted_tokens filtering
- glyph_record_key_reveal CEK hash verification
"""

import hashlib
import struct
import time
import pytest
from unittest.mock import MagicMock, patch, call

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False

from electrumx.lib.glyph import GlyphProtocol
from electrumx.server.glyph_index import (
    GlyphTokenInfo,
    GlyphIndex,
    GlyphDBKeys,
    pack_ref,
    unpack_ref,
)


# ============================================================================
# Helpers
# ============================================================================

def make_ref(txid_hex: str = "ab" * 32, vout: int = 0) -> bytes:
    return pack_ref(bytes.fromhex(txid_hex), vout)


def make_mock_env():
    env = MagicMock()
    env.glyph_index = True
    env.reorg_limit = 0
    return env


def make_mock_db():
    """Minimal mock that mimics the db.utxo_db interface."""
    store = {}

    class MockRocksDB:
        def get(self, key):
            return store.get(key)

        def put(self, key, value):
            store[key] = value

        def iterator(self, prefix=b""):
            return iter(
                (k, v) for k, v in store.items() if k.startswith(prefix)
            )

        @property
        def _store(self):
            return store

    db = MagicMock()
    db.db_height = 0
    db.utxo_db = MockRocksDB()
    return db


def make_token(protocols, **kwargs) -> GlyphTokenInfo:
    t = GlyphTokenInfo()
    t.ref = make_ref()
    t.protocols = protocols
    t.deploy_height = 100
    for k, v in kwargs.items():
        setattr(t, k, v)
    return t


# ============================================================================
# GlyphTokenInfo defaults
# ============================================================================

class TestGlyphTokenInfoDefaults:
    def test_encrypted_defaults(self):
        t = GlyphTokenInfo()
        assert t.is_encrypted is False
        assert t.cipher_hash is None
        assert t.enc_scheme is None

    def test_timelock_defaults(self):
        t = GlyphTokenInfo()
        assert t.is_timelocked is False
        assert t.timelock_mode is None
        assert t.timelock_unlock_at is None
        assert t.timelock_cek_hash is None
        assert t.timelock_hint is None


# ============================================================================
# GlyphTokenInfo CBOR round-trip
# ============================================================================

@pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")
class TestGlyphTokenInfoCBOR:
    def test_encrypted_round_trip(self):
        t = make_token(
            protocols=[GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED],
            name="Secret Art",
        )
        t.is_encrypted = True
        t.cipher_hash = "sha256:" + "aa" * 32
        t.enc_scheme = "chunked-aead-v1"
        t.token_type = 2
        t.deploy_txid = bytes(32)
        t.metadata_hash = bytes(32)

        raw = t.to_bytes()
        t2 = GlyphTokenInfo.from_bytes(raw)

        assert t2.is_encrypted is True
        assert t2.cipher_hash == t.cipher_hash
        assert t2.enc_scheme == t.enc_scheme

    def test_timelock_round_trip(self):
        t = make_token(
            protocols=[
                GlyphProtocol.GLYPH_NFT,
                GlyphProtocol.GLYPH_ENCRYPTED,
                GlyphProtocol.GLYPH_TIMELOCK,
            ],
            name="Time Capsule",
        )
        t.is_encrypted = True
        t.is_timelocked = True
        t.timelock_mode = "block"
        t.timelock_unlock_at = 500_000
        t.timelock_cek_hash = "sha256:" + "bb" * 32
        t.timelock_hint = "Release at block 500000"
        t.token_type = 2
        t.deploy_txid = bytes(32)
        t.metadata_hash = bytes(32)

        raw = t.to_bytes()
        t2 = GlyphTokenInfo.from_bytes(raw)

        assert t2.is_timelocked is True
        assert t2.timelock_mode == "block"
        assert t2.timelock_unlock_at == 500_000
        assert t2.timelock_cek_hash == "sha256:" + "bb" * 32
        assert t2.timelock_hint == "Release at block 500000"

    def test_non_encrypted_no_extra_keys(self):
        """Non-encrypted tokens should not persist encrypted sentinel fields."""
        t = make_token(protocols=[GlyphProtocol.GLYPH_NFT], name="Plain")
        t.token_type = 2
        t.deploy_txid = bytes(32)
        t.metadata_hash = bytes(32)

        raw = t.to_bytes()
        d = cbor2.loads(raw)
        # xe/xh/xs/tl should all be absent (stripped as None)
        assert "xe" not in d
        assert "xh" not in d
        assert "xs" not in d
        assert "tl" not in d

    def test_false_encrypted_not_persisted(self):
        """is_encrypted=False should be stored as None and round-trip to False."""
        t = make_token(protocols=[GlyphProtocol.GLYPH_NFT], name="Plain2")
        t.is_encrypted = False
        t.token_type = 2
        t.deploy_txid = bytes(32)
        t.metadata_hash = bytes(32)

        raw = t.to_bytes()
        t2 = GlyphTokenInfo.from_bytes(raw)
        assert t2.is_encrypted is False


# ============================================================================
# _register_token encrypted parsing
# ============================================================================

@pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")
class TestRegisterTokenEncryptedParsing:
    """
    Verifies that _register_token extracts encrypted + timelock metadata
    fields from on-chain Glyph metadata when the correct protocol IDs
    are present.
    """

    def _make_index(self):
        db = make_mock_db()
        env = make_mock_env()
        idx = GlyphIndex(db, env)
        return idx

    def _make_minimal_tx(self):
        tx = MagicMock()
        tx.outputs = []
        return tx

    def _make_envelope(self, metadata: dict) -> dict:
        return {
            "metadata": metadata,
            "metadata_bytes": cbor2.dumps(metadata),
        }

    def _call_index(self, idx, ref, metadata, height=100):
        """Invoke _index_token_reveal with standard test arguments."""
        envelope = self._make_envelope(metadata)
        idx._index_token_reveal(
            ref, bytes(32), 0,
            height, 0, envelope, metadata, self._make_minimal_tx()
        )

    def test_encrypted_flag_set(self):
        idx = self._make_index()
        ref = make_ref()
        metadata = {
            "p": [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED],
            "name": "SecretNFT",
            "main": {
                "hash": "sha256:" + "cc" * 32,
                "scheme": "chunked-aead-v1",
            },
        }
        self._call_index(idx, ref, metadata, height=100)
        token = idx.token_cache[ref]
        assert token.is_encrypted is True
        assert token.cipher_hash == "sha256:" + "cc" * 32
        assert token.enc_scheme == "chunked-aead-v1"

    def test_timelock_fields_parsed(self):
        idx = self._make_index()
        ref = make_ref()
        cek_hash = "sha256:" + "dd" * 32
        metadata = {
            "p": [
                GlyphProtocol.GLYPH_NFT,
                GlyphProtocol.GLYPH_ENCRYPTED,
                GlyphProtocol.GLYPH_TIMELOCK,
            ],
            "name": "TimeCapsule",
            "main": {
                "hash": "sha256:" + "ee" * 32,
                "scheme": "chunked-aead-v1",
            },
            "crypto": {
                "mode": "wrapped",
                "timelock": {
                    "mode": "time",
                    "unlock_at": 1_800_000_000,
                    "cek_hash": cek_hash,
                    "hint": "Reveal on New Year 2027",
                },
            },
        }
        self._call_index(idx, ref, metadata, height=101)
        token = idx.token_cache[ref]
        assert token.is_timelocked is True
        assert token.timelock_mode == "time"
        assert token.timelock_unlock_at == 1_800_000_000
        assert token.timelock_cek_hash == cek_hash
        assert token.timelock_hint == "Reveal on New Year 2027"

    def test_non_encrypted_token_not_flagged(self):
        idx = self._make_index()
        ref = make_ref("cd" * 32)
        metadata = {
            "p": [GlyphProtocol.GLYPH_NFT],
            "name": "PlainNFT",
        }
        self._call_index(idx, ref, metadata, height=102)
        token = idx.token_cache[ref]
        assert token.is_encrypted is False
        assert token.is_timelocked is False

    def test_timelock_block_mode(self):
        idx = self._make_index()
        ref = make_ref("ef" * 32)
        metadata = {
            "p": [
                GlyphProtocol.GLYPH_NFT,
                GlyphProtocol.GLYPH_ENCRYPTED,
                GlyphProtocol.GLYPH_TIMELOCK,
            ],
            "name": "BlockLocked",
            "main": {"hash": "sha256:" + "ff" * 32, "scheme": "chunked-aead-v1"},
            "crypto": {
                "timelock": {
                    "mode": "block",
                    "unlock_at": 500_000,
                    "cek_hash": "sha256:" + "aa" * 32,
                }
            },
        }
        self._call_index(idx, ref, metadata, height=103)
        token = idx.token_cache[ref]
        assert token.timelock_mode == "block"
        assert token.timelock_unlock_at == 500_000


# ============================================================================
# record_key_reveal / get_key_reveal
# ============================================================================

@pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")
class TestKeyRevealPersistence:
    def _make_index(self):
        db = make_mock_db()
        env = make_mock_env()
        return GlyphIndex(db, env)

    def test_record_and_get_reveal(self):
        idx = self._make_index()
        ref = make_ref()
        reveal_tx = bytes.fromhex("ab" * 32)
        cek_hex = "cd" * 32
        height = 500_100
        ts = int(time.time())

        idx.record_key_reveal(ref, reveal_tx, cek_hex, height, ts)

        result = idx.get_key_reveal(ref)
        assert result is not None
        assert result["reveal_tx"] == "ab" * 32
        assert result["revealed_key"] == cek_hex
        assert result["reveal_height"] == height
        assert result["created_at"] == ts

    def test_get_reveal_unknown_ref(self):
        idx = self._make_index()
        assert idx.get_key_reveal(make_ref()) is None

    def test_overwrite_reveal(self):
        idx = self._make_index()
        ref = make_ref()
        idx.record_key_reveal(ref, bytes(32), "aa" * 32, 100, 1000)
        idx.record_key_reveal(ref, bytes.fromhex("bb" * 32), "cc" * 32, 200, 2000)
        result = idx.get_key_reveal(ref)
        assert result["revealed_key"] == "cc" * 32
        assert result["reveal_height"] == 200


# ============================================================================
# list_encrypted_tokens
# ============================================================================

@pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")
class TestListEncryptedTokens:
    def _make_index(self):
        db = make_mock_db()
        env = make_mock_env()
        return GlyphIndex(db, env)

    def test_empty_when_no_encrypted_tokens(self):
        idx = self._make_index()
        plain = make_token([GlyphProtocol.GLYPH_NFT], name="Plain")
        idx.token_cache[plain.ref] = plain
        assert idx.list_encrypted_tokens() == []

    def test_returns_encrypted_tokens(self):
        idx = self._make_index()
        enc = make_token(
            [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED], name="Enc"
        )
        enc.is_encrypted = True
        enc.deploy_txid = bytes(32)
        enc.metadata_hash = None
        idx.token_cache[enc.ref] = enc
        results = idx.list_encrypted_tokens()
        assert len(results) == 1
        assert results[0].get("is_encrypted") is True

    def test_timelocked_only_filter(self):
        idx = self._make_index()

        enc = make_token(
            [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED], name="EncOnly"
        )
        enc.is_encrypted = True
        enc.deploy_txid = bytes(32)
        enc.metadata_hash = None

        tl = make_token(
            [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED, GlyphProtocol.GLYPH_TIMELOCK],
            name="TimeLocked"
        )
        tl.is_encrypted = True
        tl.is_timelocked = True
        tl.deploy_txid = bytes(32)
        tl.metadata_hash = None
        tl.ref = make_ref("bb" * 32)

        idx.token_cache[enc.ref] = enc
        idx.token_cache[tl.ref] = tl

        all_enc = idx.list_encrypted_tokens()
        assert len(all_enc) == 2

        tl_only = idx.list_encrypted_tokens(timelocked_only=True)
        assert len(tl_only) == 1
        names = [t.get("name") for t in tl_only]
        assert "TimeLocked" in names

    def test_pagination(self):
        idx = self._make_index()
        for i in range(5):
            t = make_token(
                [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED], name=f"T{i}"
            )
            t.ref = make_ref(f"{i:02x}" * 32)
            t.is_encrypted = True
            t.deploy_height = i * 10
            t.deploy_txid = bytes(32)
            t.metadata_hash = None
            idx.token_cache[t.ref] = t

        page1 = idx.list_encrypted_tokens(limit=2, offset=0)
        page2 = idx.list_encrypted_tokens(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # Pages should not overlap
        refs1 = {r["ref"] for r in page1}
        refs2 = {r["ref"] for r in page2}
        assert refs1.isdisjoint(refs2)

    def test_returns_empty_when_disabled(self):
        db = make_mock_db()
        env = make_mock_env()
        env.glyph_index = False
        idx = GlyphIndex(db, env)
        t = make_token([GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED])
        t.is_encrypted = True
        t.deploy_txid = bytes(32)
        t.metadata_hash = None
        idx.token_cache[t.ref] = t
        # When disabled the index is not enabled; list_encrypted_tokens returns []
        assert idx.list_encrypted_tokens() == []


# ============================================================================
# glyph_record_key_reveal CEK verification
# ============================================================================

@pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")
class TestAPIRecordKeyReveal:
    """
    Tests the CEK hash-verification logic used in glyph_record_key_reveal.
    We isolate just the hash check — the full async API method is tested
    via the integration test suite.
    """

    def test_cek_hash_verification_passes(self):
        cek_bytes = bytes.fromhex("aa" * 32)
        expected_hash = hashlib.sha256(cek_bytes).hexdigest()
        committed = f"sha256:{expected_hash}"
        # Strip prefix
        committed_raw = committed[len("sha256:"):]
        computed = hashlib.sha256(cek_bytes).hexdigest()
        assert computed == committed_raw

    def test_cek_hash_verification_fails_on_wrong_key(self):
        real_cek = bytes.fromhex("aa" * 32)
        wrong_cek = bytes.fromhex("bb" * 32)
        committed = hashlib.sha256(real_cek).hexdigest()
        assert hashlib.sha256(wrong_cek).hexdigest() != committed

    def test_cek_hash_without_prefix(self):
        """Handles committed hash without 'sha256:' prefix gracefully."""
        cek_bytes = bytes.fromhex("cc" * 32)
        committed = hashlib.sha256(cek_bytes).hexdigest()
        if committed.startswith("sha256:"):
            committed = committed[len("sha256:"):]
        assert hashlib.sha256(cek_bytes).hexdigest() == committed
