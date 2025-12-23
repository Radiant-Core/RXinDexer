#!/usr/bin/env python3
"""
Backfill token metadata (name, description, type) from CBOR payloads.
Updates glyph_tokens and nfts tables with extracted metadata.
"""
import os
import sys
import logging
import binascii
import json
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.session import SessionLocal
from database.models import Glyph, BackfillStatus
import datetime

try:
    import cbor2
except ImportError:
    logger.error("cbor2 not installed. Run: pip install cbor2")
    sys.exit(1)

BATCH_SIZE = 100
BACKFILL_TYPE = 'token_metadata'


def extract_cbor_from_scriptsig(script_sig_hex: str) -> dict:
    """Extract CBOR payload from scriptSig."""
    if not script_sig_hex:
        return None
    
    try:
        script_lower = script_sig_hex.lower()
        marker_pattern = '03676c79'
        marker_pos = script_lower.find(marker_pattern)
        if marker_pos == -1:
            return None
        
        script_bytes = binascii.unhexlify(script_sig_hex)
        marker_byte_pos = marker_pos // 2
        after_marker = script_bytes[marker_byte_pos + 4:]
        
        if len(after_marker) < 2:
            return None
        
        first_byte = after_marker[0]
        
        if first_byte <= 0x4b and first_byte > 0:
            push_size = first_byte
            if len(after_marker) >= push_size + 1:
                try:
                    return cbor2.loads(after_marker[1:1+push_size])
                except:
                    pass
        elif first_byte == 0x4c:
            if len(after_marker) >= 2:
                push_size = after_marker[1]
                if len(after_marker) >= push_size + 2:
                    try:
                        return cbor2.loads(after_marker[2:2+push_size])
                    except:
                        pass
        elif first_byte == 0x4d:
            if len(after_marker) >= 3:
                push_size = int.from_bytes(after_marker[1:3], 'little')
                if len(after_marker) >= push_size + 3:
                    try:
                        return cbor2.loads(after_marker[3:3+push_size])
                    except:
                        pass
        
        return None
    except:
        return None


def get_or_create_backfill_status(db):
    """Get or create backfill status record."""
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    if not status:
        status = BackfillStatus(
            backfill_type=BACKFILL_TYPE,
            is_complete=False,
            last_processed_id=0,
            total_processed=0,
            started_at=datetime.datetime.utcnow()
        )
        db.add(status)
        db.commit()
    return status


def is_backfill_complete(db):
    """Check if metadata backfill has already completed."""
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    return status and status.is_complete


def backfill_token_metadata(force=False):
    """
    Update token metadata from CBOR payloads in reveal transactions.
    Scans all glyph scripts and matches to tokens by txid.
    """
    db = SessionLocal()
    
    try:
        if not force and is_backfill_complete(db):
            logger.info("Token metadata backfill already completed. Use force=True to re-run.")
            return True
        
        status = get_or_create_backfill_status(db)
        
        # Get all glyphs needing metadata (name is 'Unknown' or ticker is NULL)
        # For unified Glyph model, we use ref as the identifier
        glyphs_needing_update = {}
        result = db.execute(text("""
            SELECT ref, reveal_outpoint FROM glyphs 
            WHERE name = 'Unknown' OR ticker IS NULL
        """))
        for row in result.fetchall():
            ref, reveal_outpoint = row
            if reveal_outpoint:
                # Extract txid from reveal_outpoint (format: txid:vout)
                txid = reveal_outpoint.split(':')[0] if ':' in reveal_outpoint else reveal_outpoint
                glyphs_needing_update[txid] = ref
            elif ref and len(ref) >= 64:
                # Use ref's txid portion as fallback
                txid = ref[:64]
                glyphs_needing_update[txid] = ref
        
        logger.info(f"Glyphs needing metadata: {len(glyphs_needing_update)}")
        
        if not glyphs_needing_update:
            status.is_complete = True
            status.completed_at = datetime.datetime.utcnow()
            db.commit()
            return True
        
        updated_count = 0
        processed = 0
        
        # Scan glyph scripts in batches - join with transactions to get txid
        last_id = 0
        while True:
            result = db.execute(text("""
                SELECT ti.id, t.txid, ti.script_sig 
                FROM transaction_inputs ti
                JOIN transactions t ON t.id = ti.transaction_id
                WHERE ti.id > :last_id AND ti.script_sig LIKE '%03676c79%'
                ORDER BY ti.id LIMIT 500
            """), {'last_id': last_id})
            rows = result.fetchall()
            
            if not rows:
                break
            
            for row in rows:
                input_id, txid, script_sig = row
                last_id = input_id
                
                # Check if this txid matches a glyph we need to update
                if txid not in glyphs_needing_update:
                    continue
                
                glyph_ref = glyphs_needing_update[txid]
                
                cbor_data = extract_cbor_from_scriptsig(script_sig)
                if not cbor_data or not isinstance(cbor_data, dict):
                    continue
                
                # Extract metadata fields
                name = cbor_data.get('name', '')
                ticker = cbor_data.get('ticker', '') or (name[:10] if name else '')
                description = cbor_data.get('desc', '') or cbor_data.get('description', '')
                token_type = cbor_data.get('type', '')
                
                # Extract additional metadata
                author = cbor_data.get('by', '') or cbor_data.get('author', '')
                container = cbor_data.get('in', '') or cbor_data.get('container', '')
                attrs = cbor_data.get('attrs', {})
                
                # Update unified glyphs table
                db.execute(text("""
                    UPDATE glyphs 
                    SET name = :name, 
                        ticker = :ticker, 
                        description = :description,
                        type = :token_type,
                        author = :author,
                        container = :container,
                        attrs = :attrs
                    WHERE ref = :ref
                """), {
                    'name': name or 'Unnamed',
                    'ticker': ticker[:50] if ticker else None,
                    'description': description or '',
                    'token_type': token_type or 'unknown',
                    'author': author or '',
                    'container': container or '',
                    'attrs': json.dumps(attrs) if attrs else '{}',
                    'ref': glyph_ref
                })
                
                updated_count += 1
                # Remove from dict so we don't process again
                del glyphs_needing_update[txid]
            
            processed += len(rows)
            db.commit()
            logger.info(f"Scanned {processed} glyph scripts | Updated: {updated_count}")
        
        db.commit()
        
        status.is_complete = True
        status.total_processed = processed
        status.completed_at = datetime.datetime.utcnow()
        db.commit()
        
        logger.info(f"Token metadata backfill complete! Updated: {updated_count}")
        return True
        
    except Exception as e:
        logger.error(f"Error during token metadata backfill: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        raise
    finally:
        db.close()


def run_if_needed():
    """Run metadata backfill only if not already complete."""
    db = SessionLocal()
    try:
        if is_backfill_complete(db):
            logger.info("Token metadata backfill already complete, skipping.")
            return True
    finally:
        db.close()
    
    return backfill_token_metadata()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Backfill token metadata from CBOR')
    parser.add_argument('--force', action='store_true', help='Force re-run')
    args = parser.parse_args()
    
    backfill_token_metadata(force=args.force)
