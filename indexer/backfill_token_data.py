#!/usr/bin/env python3
"""
Backfill Token Data

Populates the new token indexer tables with data from existing tokens:
- Extracts comprehensive metadata from CBOR payloads
- Calculates holder balances from UTXOs
- Resolves author and container references
- Updates supply tracking

Run after the token_indexer_enhancement migration.
"""

import os
import sys
import logging
from datetime import datetime

# Add indexer directory to path for imports
indexer_dir = os.path.dirname(os.path.abspath(__file__))
if indexer_dir not in sys.path:
    sys.path.insert(0, indexer_dir)

# Add parent directory to path for database imports
parent_dir = os.path.dirname(indexer_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Use the project's database session
try:
    from database.session import SessionLocal as Session
    logger.info("Using project database session")
except ImportError:
    # Fallback to direct connection
    DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://rxindexer:rxindexer@rxindexer-db:5432/rxindexer')
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    logger.info(f"Using direct database connection: {DATABASE_URL}")


def backfill_token_metadata(batch_size: int = 100) -> int:
    """
    Backfill token metadata from CBOR payloads in transaction_inputs.
    
    Extracts name, ticker, description, author, container, icon data, etc.
    """
    logger.info("Starting token metadata backfill...")
    
    from script_utils import decode_and_extract_glyph
    
    db = Session()
    updated = 0
    last_id = 0

    def _reverse_txid_hex(txid_hex: str) -> str:
        if not isinstance(txid_hex, str) or len(txid_hex) != 64:
            return txid_hex
        return ''.join([txid_hex[i:i+2] for i in range(0, 64, 2)][::-1])

    def _vout_candidates_from_token_id(token_id: str) -> list:
        """Extract vout candidates from token_id (ref format: txid(32) + vout(4))."""
        if not isinstance(token_id, str) or len(token_id) < 72:
            return []
        vout_hex = token_id[64:72]
        try:
            vout_bytes = bytes.fromhex(vout_hex)
            return list({
                int.from_bytes(vout_bytes, 'little'),
                int.from_bytes(vout_bytes, 'big'),
            })
        except Exception:
            return []

    def _find_reveal_scriptsig(commit_txid: str, commit_vout: int):
        """Find reveal input (scriptSig containing gly marker) that spends the commit outpoint."""
        if not commit_txid or commit_vout is None:
            return (None, None)
        row = db.execute(text("""
            SELECT t.txid, ti.script_sig
            FROM transaction_inputs ti
            JOIN transactions t ON t.id = ti.transaction_id
            WHERE ti.spent_txid = :spent_txid
              AND ti.spent_vout = :spent_vout
              AND ti.script_sig IS NOT NULL
              AND ti.script_sig ILIKE '%676c79%'
            ORDER BY ti.id
            LIMIT 1
        """), {'spent_txid': commit_txid, 'spent_vout': commit_vout}).fetchone()
        if not row:
            return (None, None)
        return (row[0], row[1])
    
    try:
        while True:
            # Use unified glyphs table instead of legacy glyph_tokens
            result = db.execute(text("""
                SELECT g.id, g.ref, g.reveal_outpoint
                FROM glyphs g
                WHERE g.id > :last_id
                  AND (g.name IS NULL OR g.name = '' OR g.name = 'Unknown')
                ORDER BY g.id
                LIMIT :limit
            """), {'limit': batch_size, 'last_id': last_id})

            rows = result.fetchall()
            if not rows:
                break
            
            for row in rows:
                last_id = int(row.id)
                glyph_ref = row.ref
                reveal_outpoint = row.reveal_outpoint

                candidates = []
                # Extract txid from ref (first 64 chars)
                if isinstance(glyph_ref, str) and len(glyph_ref) >= 64:
                    candidate = glyph_ref[:64]
                    candidates.append(candidate)
                    candidates.append(_reverse_txid_hex(candidate))
                # Extract txid from reveal_outpoint if available
                if reveal_outpoint and ':' in str(reveal_outpoint):
                    reveal_txid_part = str(reveal_outpoint).split(':')[0]
                    if reveal_txid_part not in candidates:
                        candidates.append(reveal_txid_part)
                        candidates.append(_reverse_txid_hex(reveal_txid_part))

                vout_candidates = _vout_candidates_from_token_id(glyph_ref)
                if not vout_candidates:
                    continue

                script_sig = None
                reveal_txid = None
                used_commit_txid = None
                used_commit_vout = None
                for txid_hex in candidates:
                    if not txid_hex:
                        continue
                    for vout in vout_candidates:
                        r_txid, r_sig = _find_reveal_scriptsig(txid_hex, vout)
                        if r_sig:
                            reveal_txid = r_txid
                            script_sig = r_sig
                            used_commit_txid = txid_hex
                            used_commit_vout = vout
                            break
                    if script_sig:
                        break

                if not script_sig:
                    continue
                
                try:
                    # Decode CBOR and extract metadata
                    metadata = decode_and_extract_glyph(script_sig, txid=reveal_txid)
                    
                    if metadata:
                        # Update unified glyphs table with extracted metadata
                        db.execute(text("""
                            UPDATE glyphs SET
                                reveal_outpoint = COALESCE(:reveal_outpoint, reveal_outpoint),
                                name = COALESCE(:name, name),
                                ticker = COALESCE(:ticker, ticker),
                                description = COALESCE(:description, description),
                                type = COALESCE(:token_type, type),
                                immutable = COALESCE(:immutable, immutable),
                                author = COALESCE(:author, author),
                                container = COALESCE(:container, container),
                                embed_type = COALESCE(:icon_mime_type, embed_type),
                                embed_data = COALESCE(:icon_data, embed_data),
                                remote_url = COALESCE(:icon_url, remote_url),
                                updated_at = NOW()
                            WHERE ref = :ref
                        """), {
                            'ref': glyph_ref,
                            'reveal_outpoint': f"{reveal_txid}:{used_commit_vout}" if reveal_txid and used_commit_vout is not None else None,
                            'name': metadata.get('name'),
                            'ticker': metadata.get('ticker'),
                            'description': metadata.get('description'),
                            'token_type': metadata.get('token_type_name'),
                            'immutable': metadata.get('immutable'),
                            'author': metadata.get('author'),
                            'container': metadata.get('container'),
                            'icon_mime_type': metadata.get('icon_mime_type'),
                            'icon_url': metadata.get('icon_url'),
                            'icon_data': metadata.get('icon_data'),
                        })
                        updated += 1
                        
                except Exception as e:
                    logger.debug(f"Failed to extract metadata for {glyph_ref}: {e}")
            
            db.commit()
            
            if updated > 0 and updated % 500 == 0:
                logger.info(f"Updated metadata for {updated} glyphs...")
        
        db.commit()
        logger.info(f"Token metadata backfill complete. Updated {updated} tokens.")
        return updated
        
    except Exception as e:
        logger.error(f"Error in metadata backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def backfill_nft_metadata(batch_size: int = 100) -> int:
    """DEPRECATED: NFT metadata is now handled by backfill_token_metadata using unified glyphs table.
    
    This function is kept for backward compatibility but does nothing.
    All NFT data is now stored in the unified 'glyphs' table.
    """
    logger.info("NFT metadata backfill skipped - using unified glyphs table instead.")
    logger.info("Run backfill_token_metadata() to update glyph metadata.")
    return 0


def _legacy_backfill_nft_metadata(batch_size: int = 100) -> int:
    """Legacy function - no longer used. Kept for reference only."""
    pass  # No-op - legacy nfts table is no longer populated


def backfill_missing_names_from_existing() -> int:
    """DEPRECATED: Now uses unified glyphs table. This is a no-op for backward compatibility."""
    logger.info("Missing-name propagation skipped - unified glyphs table has unique refs.")
    return 0


def backfill_holder_balances(batch_size: int = 100) -> int:
    """
    Calculate holder balances from UTXOs for all tokens.
    
    OPTIMIZED: Uses owner field from glyph_tokens. For FT tokens without 
    explicit supply, defaults to 1. Real supply tracking happens during 
    live indexing.
    """
    logger.info("Starting holder balance backfill (optimized)...")
    
    db = Session()
    updated = 0
    
    try:
        # Get all tokens with their owner and supply info
        result = db.execute(text("""
            SELECT token_id, type, owner, max_supply, current_supply
            FROM glyph_tokens
            WHERE owner IS NOT NULL
            ORDER BY token_id
        """))
        tokens = result.fetchall()
        
        logger.info(f"Processing {len(tokens)} tokens for holder tracking...")
        
        for token in tokens:
            token_id = token.token_id
            token_type = token.type
            owner = token.owner
            
            # Use existing supply or default to 1 for NFTs/unknown
            supply = token.max_supply or token.current_supply or 1
            
            if not owner:
                continue
            
            # Insert holder record - owner is initial holder
            db.execute(text("""
                INSERT INTO token_holders (token_id, address, balance, first_acquired_at, last_updated_at)
                VALUES (:token_id, :address, :balance, NOW(), NOW())
                ON CONFLICT (token_id, address) DO UPDATE SET
                    balance = GREATEST(token_holders.balance, :balance),
                    last_updated_at = NOW()
            """), {'token_id': token_id, 'address': owner, 'balance': supply})
            
            # Update token stats
            db.execute(text("""
                UPDATE glyph_tokens SET
                    holder_count = COALESCE(holder_count, 0) + 1,
                    circulating_supply = COALESCE(circulating_supply, 0) + :supply,
                    supply_updated_at = NOW()
                WHERE token_id = :token_id
                AND (holder_count IS NULL OR holder_count = 0)
            """), {'token_id': token_id, 'supply': supply})
            
            updated += 1
            
            if updated % 500 == 0:
                db.commit()
                logger.info(f"Processed {updated} tokens for holder tracking...")
        
        # Calculate percentages
        logger.info("Calculating holder percentages...")
        db.execute(text("""
            UPDATE token_holders th
            SET percentage = (th.balance::float / gt.circulating_supply) * 100
            FROM glyph_tokens gt
            WHERE th.token_id = gt.token_id
            AND gt.circulating_supply > 0
        """))
        
        db.commit()
        logger.info(f"Holder balance backfill complete. Processed {updated} tokens.")
        return updated
        
    except Exception as e:
        logger.error(f"Error in holder backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def backfill_author_resolution(batch_size: int = 100) -> int:
    """
    Resolve author references to get author names and images.
    """
    logger.info("Starting author resolution backfill...")
    
    from author_resolver import batch_resolve_authors, batch_resolve_containers
    
    db = Session()
    
    try:
        # Resolve authors
        authors_updated = batch_resolve_authors(db, batch_size)
        
        # Resolve containers
        containers_updated = batch_resolve_containers(db, batch_size)
        
        db.commit()
        logger.info(f"Author resolution complete. Authors: {authors_updated}, Containers: {containers_updated}")
        return authors_updated + containers_updated
        
    except Exception as e:
        logger.error(f"Error in author resolution: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def update_backfill_status(backfill_type: str, is_complete: bool, total_processed: int):
    """Update backfill status tracking."""
    db = Session()
    try:
        db.execute(text("""
            INSERT INTO backfill_status (backfill_type, is_complete, total_processed, completed_at, updated_at)
            VALUES (:type, :complete, :total, 
                    CASE WHEN :complete THEN NOW() ELSE NULL END, NOW())
            ON CONFLICT (backfill_type) DO UPDATE SET
                is_complete = :complete,
                total_processed = :total,
                completed_at = CASE WHEN :complete THEN NOW() ELSE backfill_status.completed_at END,
                updated_at = NOW()
        """), {'type': backfill_type, 'complete': is_complete, 'total': total_processed})
        db.commit()
    finally:
        db.close()


def run_full_backfill():
    """Run all backfill operations in sequence."""
    logger.info("=" * 60)
    logger.info("Starting full token data backfill")
    logger.info("=" * 60)
    
    start_time = datetime.now()
    
    # 1. Metadata backfill
    logger.info("\n[1/4] Token Metadata Backfill")
    try:
        metadata_count = backfill_token_metadata()
        update_backfill_status('token_metadata', True, metadata_count)
    except Exception as e:
        logger.error(f"Metadata backfill failed: {e}")
        update_backfill_status('token_metadata', False, 0)

    # 2. NFT metadata backfill
    logger.info("\n[2/4] NFT Metadata Backfill")
    try:
        nft_meta_count = backfill_nft_metadata()
        update_backfill_status('nft_metadata', True, nft_meta_count)
    except Exception as e:
        logger.error(f"NFT metadata backfill failed: {e}")
        update_backfill_status('nft_metadata', False, 0)
    
    # 3. Holder balances
    logger.info("\n[3/4] Holder Balance Backfill")
    try:
        holder_count = backfill_holder_balances()
        update_backfill_status('token_holders', True, holder_count)
    except Exception as e:
        logger.error(f"Holder backfill failed: {e}")
        update_backfill_status('token_holders', False, 0)
    
    # 4. Author resolution
    logger.info("\n[4/4] Author Resolution Backfill")
    try:
        author_count = backfill_author_resolution()
        update_backfill_status('author_resolution', True, author_count)
    except Exception as e:
        logger.error(f"Author resolution failed: {e}")
        update_backfill_status('author_resolution', False, 0)
    
    elapsed = datetime.now() - start_time
    logger.info("=" * 60)
    logger.info(f"Full backfill complete in {elapsed}")
    logger.info("=" * 60)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill token data for enhanced indexer')
    parser.add_argument('--metadata', action='store_true', help='Run metadata backfill only')
    parser.add_argument('--holders', action='store_true', help='Run holder backfill only')
    parser.add_argument('--authors', action='store_true', help='Run author resolution only')
    parser.add_argument('--all', action='store_true', help='Run all backfills (default)')
    parser.add_argument('--batch-size', type=int, default=100, help='Batch size for processing')
    
    args = parser.parse_args()
    
    if args.metadata:
        backfill_token_metadata(args.batch_size)
    elif args.holders:
        backfill_holder_balances(args.batch_size)
    elif args.authors:
        backfill_author_resolution(args.batch_size)
    else:
        run_full_backfill()
