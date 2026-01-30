#!/usr/bin/env python3
"""
RXinDexer Local Test Suite

Quick validation tests that can run without Docker.
Tests module imports, class instantiation, and core logic.

Usage: python3 tests/test_local.py
"""

import sys
import os
import traceback
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test counters
passed = 0
failed = 0
tests_run = []


def test(name):
    """Decorator for test functions."""
    def decorator(func):
        def wrapper():
            global passed, failed
            try:
                func()
                print(f"  ✅ {name}")
                passed += 1
                tests_run.append((name, True, None))
            except AssertionError as e:
                print(f"  ❌ {name}")
                print(f"     └─ {e}")
                failed += 1
                tests_run.append((name, False, str(e)))
            except Exception as e:
                print(f"  ❌ {name}")
                print(f"     └─ {type(e).__name__}: {e}")
                failed += 1
                tests_run.append((name, False, f"{type(e).__name__}: {e}"))
        return wrapper
    return decorator


# ============================================================
# Module Import Tests
# ============================================================

print("\n📦 Module Import Tests\n")

@test("Import electrumx.lib.glyph")
def test_import_glyph():
    from electrumx.lib.glyph import (
        parse_glyph_envelope, contains_glyph_magic, GlyphProtocol,
        get_protocol_name, is_dmint_reveal, is_wave_claim
    )

@test("Import electrumx.server.glyph_index")
def test_import_glyph_index():
    from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo

@test("Import electrumx.server.wave_index")
def test_import_wave_index():
    from electrumx.server.wave_index import WaveIndex, WaveNameInfo

@test("Import electrumx.server.swap_index")
def test_import_swap_index():
    from electrumx.server.swap_index import SwapIndex, SwapOrderInfo

@test("Import electrumx.server.mempool_glyph")
def test_import_mempool_glyph():
    from electrumx.server.mempool_glyph import MempoolGlyphIndex

@test("Import electrumx.server.glyph_subscriptions")
def test_import_subscriptions():
    from electrumx.server.glyph_subscriptions import GlyphSubscriptionManager

@test("Import electrumx.server.dmint_contracts")
def test_import_dmint():
    from electrumx.server.dmint_contracts import DMintContractsManager

@test("Import electrumx.server.glyph_api")
def test_import_glyph_api():
    from electrumx.server.glyph_api import GlyphAPIMixin, GLYPH_METHODS

# Run import tests
test_import_glyph()
test_import_glyph_index()
test_import_wave_index()
test_import_swap_index()
test_import_mempool_glyph()
test_import_subscriptions()
test_import_dmint()
test_import_glyph_api()


# ============================================================
# API Registration Tests
# ============================================================

print("\n🔌 API Registration Tests\n")

@test("GLYPH_METHODS has 38 methods")
def test_glyph_methods_count():
    from electrumx.server.glyph_api import GLYPH_METHODS
    assert len(GLYPH_METHODS) == 38, f"Expected 38, got {len(GLYPH_METHODS)}"

@test("All handlers exist in GlyphAPIMixin")
def test_handlers_exist():
    from electrumx.server.glyph_api import GlyphAPIMixin, GLYPH_METHODS
    missing = []
    for method, handler in GLYPH_METHODS.items():
        if not hasattr(GlyphAPIMixin, handler):
            missing.append(f"{method} -> {handler}")
    assert not missing, f"Missing handlers: {missing}"

@test("Glyph methods registered (20)")
def test_glyph_methods():
    from electrumx.server.glyph_api import GLYPH_METHODS
    glyph_methods = [m for m in GLYPH_METHODS if m.startswith('glyph.')]
    assert len(glyph_methods) == 20, f"Expected 20, got {len(glyph_methods)}"

@test("WAVE methods registered (6)")
def test_wave_methods():
    from electrumx.server.glyph_api import GLYPH_METHODS
    wave_methods = [m for m in GLYPH_METHODS if m.startswith('wave.')]
    assert len(wave_methods) == 6, f"Expected 6, got {len(wave_methods)}"

@test("Swap methods registered (6)")
def test_swap_methods():
    from electrumx.server.glyph_api import GLYPH_METHODS
    swap_methods = [m for m in GLYPH_METHODS if m.startswith('swap.')]
    assert len(swap_methods) == 6, f"Expected 6, got {len(swap_methods)}"

@test("dMint methods registered (5)")
def test_dmint_methods():
    from electrumx.server.glyph_api import GLYPH_METHODS
    dmint_methods = [m for m in GLYPH_METHODS if m.startswith('dmint.')]
    assert len(dmint_methods) == 5, f"Expected 5, got {len(dmint_methods)}"

# Run API tests
test_glyph_methods_count()
test_handlers_exist()
test_glyph_methods()
test_wave_methods()
test_swap_methods()
test_dmint_methods()


# ============================================================
# Data Structure Tests
# ============================================================

print("\n📊 Data Structure Tests\n")

@test("GlyphTokenInfo serialization roundtrip")
def test_glyph_token_serialization():
    from electrumx.server.glyph_index import GlyphTokenInfo
    token = GlyphTokenInfo()
    token.ref = b'\x00' * 36
    token.name = 'TestToken'
    token.ticker = 'TEST'
    token.decimals = 8
    token.protocols = [1, 2]
    token.token_type = 1
    token.deploy_height = 100000
    
    data = token.to_bytes()
    token2 = GlyphTokenInfo.from_bytes(data)
    
    assert token2.name == token.name
    assert token2.ticker == token.ticker
    assert token2.decimals == token.decimals

@test("WaveNameInfo serialization roundtrip")
def test_wave_name_serialization():
    from electrumx.server.wave_index import WaveNameInfo
    wave = WaveNameInfo()
    wave.name = 'testname'
    wave.ref = b'\x00' * 36
    wave.owner_scripthash = b'\x00' * 32
    wave.registration_height = 100000
    wave.zone = {'address': 'rxd1test...'}
    
    data = wave.to_bytes()
    wave2 = WaveNameInfo.from_bytes(data)
    
    assert wave2.name == wave.name
    assert wave2.registration_height == wave.registration_height

@test("SwapOrderInfo serialization roundtrip")
def test_swap_order_serialization():
    from electrumx.server.swap_index import SwapOrderInfo
    order = SwapOrderInfo()
    order.order_id = b'\x00' * 36
    order.base_ref = b'\x01' * 36
    order.quote_ref = b'\x02' * 36
    order.maker_scripthash = b'\x00' * 32
    order.side = 0
    order.price = 1000000
    order.amount = 500000
    order.height = 100000
    
    data = order.to_bytes()
    order2 = SwapOrderInfo.from_bytes(data)
    
    assert order2.price == order.price
    assert order2.amount == order.amount

# Run data structure tests
test_glyph_token_serialization()
test_wave_name_serialization()
test_swap_order_serialization()


# ============================================================
# WAVE Validation Tests
# ============================================================

print("\n🌊 WAVE Validation Tests\n")

@test("Valid WAVE names accepted")
def test_valid_wave_names():
    from electrumx.server.wave_index import validate_wave_name
    valid_names = ['test', 'hello', 'radiant', 'abc123', 'my-name', 'xn--nxasmq5b']
    for name in valid_names:
        valid, err = validate_wave_name(name)
        assert valid, f"'{name}' should be valid: {err}"

@test("Invalid WAVE names rejected")
def test_invalid_wave_names():
    from electrumx.server.wave_index import validate_wave_name
    invalid = ['', '-test', 'test-', 'test--name', 'test_name', 'TEST!']
    for name in invalid:
        valid, _ = validate_wave_name(name)
        assert not valid, f"'{name}' should be invalid"

@test("WAVE character mapping")
def test_wave_char_mapping():
    from electrumx.server.wave_index import char_to_index, index_to_char
    assert char_to_index('a') == 0
    assert char_to_index('z') == 25
    assert char_to_index('0') == 26
    assert char_to_index('-') == 36
    assert index_to_char(0) == 'a'
    assert index_to_char(26) == '0'

@test("WAVE name normalization")
def test_wave_normalization():
    from electrumx.server.wave_index import normalize_name, name_to_hash
    assert normalize_name('TEST') == 'test'
    assert normalize_name('  Hello  ') == 'hello'
    assert name_to_hash('test') == name_to_hash('TEST')

# Run WAVE tests
test_valid_wave_names()
test_invalid_wave_names()
test_wave_char_mapping()
test_wave_normalization()


# ============================================================
# Glyph Protocol Tests
# ============================================================

print("\n🔮 Glyph Protocol Tests\n")

@test("Glyph magic detection")
def test_glyph_magic():
    from electrumx.lib.glyph import contains_glyph_magic, GLYPH_MAGIC
    script_with = b'\x6a' + GLYPH_MAGIC + b'\x00' * 20
    script_without = b'\x6a\x00' * 20
    assert contains_glyph_magic(script_with)
    assert not contains_glyph_magic(script_without)

@test("Protocol name lookup")
def test_protocol_names():
    from electrumx.lib.glyph import get_protocol_name, GlyphProtocol
    assert 'FT' in get_protocol_name(GlyphProtocol.GLYPH_FT).upper() or 'FUNGIBLE' in get_protocol_name(GlyphProtocol.GLYPH_FT).upper()
    assert 'NFT' in get_protocol_name(GlyphProtocol.GLYPH_NFT).upper() or 'NON' in get_protocol_name(GlyphProtocol.GLYPH_NFT).upper()

@test("Token type detection")
def test_token_types():
    from electrumx.lib.glyph import get_token_type_id, GlyphProtocol
    from electrumx.server.glyph_index import GlyphTokenType
    assert get_token_type_id([GlyphProtocol.GLYPH_FT]) == GlyphTokenType.FT
    assert get_token_type_id([GlyphProtocol.GLYPH_NFT]) == GlyphTokenType.NFT
    assert get_token_type_id([GlyphProtocol.GLYPH_WAVE]) == GlyphTokenType.WAVE

@test("dMint/WAVE envelope detection")
def test_envelope_detection():
    from electrumx.lib.glyph import is_dmint_reveal, is_wave_claim, GlyphProtocol
    dmint_reveal = {'protocols': [GlyphProtocol.GLYPH_DMINT], 'type': 'reveal'}
    wave_claim = {'protocols': [GlyphProtocol.GLYPH_WAVE]}
    ft_envelope = {'protocols': [GlyphProtocol.GLYPH_FT]}
    
    assert is_dmint_reveal(dmint_reveal)
    assert not is_dmint_reveal(ft_envelope)
    assert is_wave_claim(wave_claim)

# Run Glyph tests
test_glyph_magic()
test_protocol_names()
test_token_types()
test_envelope_detection()


# ============================================================
# Manager Instantiation Tests
# ============================================================

print("\n⚙️ Manager Instantiation Tests\n")

@test("GlyphSubscriptionManager initialization")
def test_subscription_manager():
    from electrumx.server.glyph_subscriptions import GlyphSubscriptionManager
    class MockEnv:
        glyph_subscriptions = True
    mgr = GlyphSubscriptionManager(MockEnv())
    assert mgr is not None

@test("DMintContractsManager initialization")
def test_dmint_manager():
    from electrumx.server.dmint_contracts import DMintContractsManager
    mgr = DMintContractsManager('/tmp')
    assert mgr is not None

@test("MempoolGlyphIndex initialization")
def test_mempool_index():
    from electrumx.server.mempool_glyph import MempoolGlyphIndex
    class MockEnv:
        mempool_glyph_index = True
        mempool_swap_index = True
    idx = MempoolGlyphIndex(MockEnv())
    assert idx is not None

# Run manager tests
test_subscription_manager()
test_dmint_manager()
test_mempool_index()


# ============================================================
# Summary
# ============================================================

print("\n" + "=" * 50)
print("TEST SUMMARY")
print("=" * 50)
print(f"Total:   {passed + failed}")
print(f"Passed:  {passed}")
print(f"Failed:  {failed}")
print(f"Success: {100 * passed / (passed + failed):.1f}%")
print("=" * 50)

if failed > 0:
    print("\n❌ Failed tests:")
    for name, success, error in tests_run:
        if not success:
            print(f"  • {name}: {error}")
    sys.exit(1)
else:
    print("\n✅ All tests passed!")
    sys.exit(0)
