#!/usr/bin/env python3
"""
Verify WAVE genesis transaction and provide reindexing guidance.

Usage:
    python verify_wave_genesis.py

This script checks:
1. Environment configuration for WAVE_GENESIS_REF
2. The structure of the genesis transaction (if accessible)
3. Provides reindexing instructions
"""

import os
import sys

# The first WAVE name mint transaction
genesis_txid = "115e62d96f44402c448bf76d4ca403188733b902ab0b7703d9f36333178afda4"
genesis_vout = 0
genesis_ref = f"{genesis_txid}_{genesis_vout}"

def check_env_config():
    """Check environment configuration."""
    print("=" * 60)
    print("WAVE GENESIS REFERENCE VERIFICATION")
    print("=" * 60)
    print()
    
    # Check docker env file
    env_file = "docker/full-stack/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            content = f.read()
            if f"WAVE_GENESIS_REF={genesis_ref}" in content:
                print(f"✓ docker/full-stack/.env correctly configured:")
                print(f"  WAVE_GENESIS_REF={genesis_ref}")
            elif "WAVE_GENESIS_REF=" in content and genesis_ref not in content:
                print(f"✗ docker/full-stack/.env has different genesis ref")
                for line in content.split('\n'):
                    if 'WAVE_GENESIS_REF' in line and not line.startswith('#'):
                        print(f"  Current: {line}")
                print(f"  Expected: WAVE_GENESIS_REF={genesis_ref}")
            else:
                print(f"✗ WAVE_GENESIS_REF not found in {env_file}")
    else:
        print(f"? {env_file} not found")
    
    print()
    
    # Check current environment
    current_ref = os.environ.get('WAVE_GENESIS_REF')
    if current_ref == genesis_ref:
        print(f"✓ Environment variable WAVE_GENESIS_REF is set correctly")
    elif current_ref:
        print(f"✗ Environment variable WAVE_GENESIS_REF has different value:")
        print(f"  Current: {current_ref}")
        print(f"  Expected: {genesis_ref}")
    else:
        print(f"✗ Environment variable WAVE_GENESIS_REF is not set")
        print(f"  Run: export WAVE_GENESIS_REF={genesis_ref}")
    
    print()

def print_transaction_details():
    """Print details about the genesis transaction."""
    print("=" * 60)
    print("GENESIS TRANSACTION DETAILS")
    print("=" * 60)
    print()
    print(f"Transaction ID: {genesis_txid}")
    print(f"Output Index:   {genesis_vout}")
    print(f"Genesis Ref:    {genesis_ref}")
    print(f"Block Height:   425,046")
    print()
    print("This transaction should be:")
    print("  - A Glyph v2 WAVE name mint (protocols: [2, 5, 11])")
    print("  - Have 38 outputs (1 claim + 37 branches)")
    print("  - Contain metadata with attrs.name and attrs.domain='rxd'")
    print()

def print_reindex_instructions():
    """Print reindexing instructions."""
    print("=" * 60)
    print("REINDEXING INSTRUCTIONS")
    print("=" * 60)
    print()
    
    print("Option 1: Trigger reorg via RPC (Recommended)")
    print("-" * 40)
    print("# If RXinDexer is running and caught up:")
    print(f"electrumx_rpc reorg 1000  # Go back ~1000 blocks from tip")
    print()
    print("# Or use Python directly:")
    print("python -c \"import asyncio; from electrumx_rpc import main; asyncio.run(main())\" reorg 1000")
    print()
    
    print("Option 2: Clear WAVE-specific data and restart")
    print("-" * 40)
    print("# Stop RXinDexer")
    print("docker-compose down  # or kill the process")
    print()
    print("# Clear WAVE index keys from database (optional, advanced)")
    print("# This requires direct RocksDB/LevelDB manipulation")
    print()
    print("# Restart with fresh WAVE indexing:")
    print(f"export WAVE_GENESIS_REF={genesis_ref}")
    print("docker-compose up -d")
    print()
    
    print("Option 3: Full resync from block 425,046")
    print("-" * 40)
    print("# Stop RXinDexer")
    print("docker-compose down")
    print()
    print("# Clear the database (⚠️ DESTRUCTIVE - all data lost)")
    print("rm -rf $DB_DIRECTORY/*  # or your data directory")
    print()
    print("# Restart - will sync from genesis")
    print(f"export WAVE_GENESIS_REF={genesis_ref}")
    print("docker-compose up -d")
    print()

def print_verification_steps():
    """Print post-reindex verification steps."""
    print("=" * 60)
    print("POST-REINDEX VERIFICATION")
    print("=" * 60)
    print()
    print("1. Check logs for WAVE initialization:")
    print("   docker-compose logs -f electrumx | grep -i wave")
    print()
    print("2. Expected log messages:")
    print(f"   'WAVE genesis ref: {genesis_ref}'")
    print("   'WAVE name indexing enabled'")
    print("   'Indexed WAVE name \"...\" target=... at height 425046'")
    print()
    print("3. Test API resolution:")
    print("   # Get a list of WAVE names to test")
    print("   curl http://localhost:8000/wave/resolve/alice.rxd")
    print("   curl http://localhost:8000/wave/resolve/yourname.rxd")
    print()
    print("4. Check WAVE stats:")
    print("   curl http://localhost:8000/wave/stats")
    print()
    print("5. ElectrumX RPC method:")
    print("   electrumx_rpc wave.stats")
    print()

def print_photonic_wallet_check():
    """Print Photonic Wallet specific checks."""
    print("=" * 60)
    print("PHOTONIC WALLET VERIFICATION")
    print("=" * 60)
    print()
    print("After RXinDexer is reindexed, verify in Photonic Wallet:")
    print()
    print("1. Wallet queries local DB first (db.glyph table)")
    print("   - Sync must complete for WAVE names to appear")
    print()
    print("2. Fallback RPC call:")
    print("   wave.resolve(name) -> returns {target, zone, owner}")
    print()
    print("3. Check browser console for:")
    print("   - 'resolveWaveName' call results")
    print("   - Any errors from electrumWorker")
    print()
    print("4. Verify WAVE name format in metadata:")
    print("   - attrs.name = 'alice'")
    print("   - attrs.domain = 'rxd'")
    print("   - p = [2, 5, 11] (NFT + Mutable + WAVE)")
    print()

def main():
    """Main function."""
    check_env_config()
    print_transaction_details()
    print_reindex_instructions()
    print_verification_steps()
    print_photonic_wallet_check()
    
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()
    print(f"Genesis Ref: {genesis_ref}")
    print("Block: 425,046")
    print()
    print("Next steps:")
    print("1. Ensure WAVE_GENESIS_REF is exported in your environment")
    print("2. Restart or trigger reorg on RXinDexer")
    print("3. Verify logs show genesis ref")
    print("4. Test API resolution")
    print("5. Verify in Photonic Wallet after sync completes")
    print()

if __name__ == '__main__':
    main()
