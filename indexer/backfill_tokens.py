#!/usr/bin/env python3
"""
Backfill tokens from existing UTXOs using Photonic Wallet patterns.
This scans UTXOs with nonstandard scripts and detects FT/NFT tokens.
Supports resumable progress tracking via backfill_status table.
"""
import os
import sys
import logging
import base64
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.session import SessionLocal
from database.models import Glyph, GlyphAction, Container, TokenFile, BackfillStatus
from database.models import ACTION_TYPE_MINT, GLYPH_TYPE_NFT, GLYPH_TYPE_FT, GLYPH_TYPE_USER, GLYPH_TYPE_CONTAINER, GLYPH_TYPE_DAT
from indexer.script_utils import detect_token_from_script, ref_to_token_id
import datetime

BATCH_SIZE = 10000
BACKFILL_TYPE = 'tokens'


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
    """Check if token backfill has already completed."""
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    return status and status.is_complete


def extract_files_from_metadata(token_id, token_type, metadata):
    """Extract embedded and remote files from token metadata."""
    files = []
    if not isinstance(metadata, dict):
        return files
    
    for key, value in metadata.items():
        if not isinstance(value, dict):
            continue
        
        # Check for embedded file (has 't' for type and 'b' for binary data)
        if 't' in value and 'b' in value:
            try:
                file_data = value['b']
                if isinstance(file_data, (bytes, bytearray)):
                    file_data = base64.b64encode(file_data).decode('utf-8')
                elif isinstance(file_data, list):
                    file_data = base64.b64encode(bytes(file_data)).decode('utf-8')
                
                files.append(TokenFile(
                    token_id=token_id,
                    token_type=token_type,
                    file_key=key,
                    mime_type=value.get('t', 'application/octet-stream'),
                    file_data=file_data,
                    file_size=len(value['b']) if value.get('b') else None,
                    created_at=datetime.datetime.utcnow()
                ))
            except Exception as e:
                logger.warning(f"Failed to extract embedded file {key}: {e}")
        
        # Check for remote file (has 'u' for URL)
        elif 'u' in value:
            files.append(TokenFile(
                token_id=token_id,
                token_type=token_type,
                file_key=key,
                mime_type=value.get('t', ''),
                remote_url=value.get('u', ''),
                file_hash=value.get('h', ''),
                created_at=datetime.datetime.utcnow()
            ))
    
    return files


def update_container(db, container_id, owner=None):
    """Create or update a container record."""
    if not container_id:
        return
    
    try:
        container = db.query(Container).filter(Container.container_id == container_id).first()
        if container:
            container.token_count = (container.token_count or 0) + 1
            container.updated_at = datetime.datetime.utcnow()
        else:
            container = Container(
                container_id=container_id,
                owner=owner,
                token_count=1,
                created_at=datetime.datetime.utcnow()
            )
            db.add(container)
    except IntegrityError:
        db.rollback()
        # Container was created by another process, just update count
        container = db.query(Container).filter(Container.container_id == container_id).first()
        if container:
            container.token_count = (container.token_count or 0) + 1


def backfill_tokens(force=False):
    """
    Scan existing UTXOs and detect tokens using Photonic Wallet patterns.
    
    Args:
        force: If True, run even if backfill was previously completed
    """
    db = SessionLocal()
    
    try:
        # Check if already complete
        if not force and is_backfill_complete(db):
            logger.info("Token backfill already completed. Use force=True to re-run.")
            return True
        
        # Get or create status
        status = get_or_create_backfill_status(db)
        last_id = status.last_processed_id or 0
        
        # Get max ID for progress calculation
        result = db.execute(text("SELECT MAX(id), COUNT(*) FROM utxos_initial WHERE script_hex IS NOT NULL AND LENGTH(script_hex) > 50"))
        row = result.fetchone()
        max_id = row[0] or 0
        total_with_scripts = row[1] or 0
        
        logger.info(f"Token backfill starting from ID {last_id}, max ID: {max_id}, total UTXOs with scripts: {total_with_scripts:,}")
        
        if last_id > 0:
            logger.info(f"Resuming from previous run. Already processed: {status.total_processed:,}")
        
        # Get existing refs to avoid duplicates (using new unified glyphs table)
        existing_refs = set(row[0] for row in db.execute(text("SELECT ref FROM glyphs")).fetchall())
        logger.info(f"Existing glyphs: {len(existing_refs)}")
        
        total_glyphs = 0
        total_actions = 0
        total_files = 0
        batch_count = 0
        
        while True:
            # Fetch batch of UTXOs with scripts using ID-based pagination (faster than OFFSET)
            query = text("""
                SELECT id, txid, vout, address, script_hex, transaction_block_height, value
                FROM utxos_initial 
                WHERE id > :last_id AND script_hex IS NOT NULL AND LENGTH(script_hex) > 50
                ORDER BY id
                LIMIT :limit
            """)
            
            rows = db.execute(query, {'last_id': last_id, 'limit': BATCH_SIZE}).fetchall()
            
            if not rows:
                break
            
            glyph_objs = []
            action_objs = []
            file_objs = []
            containers_to_update = []
            
            for row in rows:
                utxo_id, txid, vout, address, script_hex, block_height, value = row
                last_id = utxo_id  # Track progress
                
                token_info = detect_token_from_script(script_hex)
                if not token_info:
                    continue
                
                token_type = token_info.get('type', 'unknown')
                token_ref = token_info.get('ref', '')
                token_id = ref_to_token_id(token_ref) if token_ref else txid
                
                if token_id in existing_refs:
                    continue
                
                # Determine glyph type for unified model
                if token_type in ('nft', 'mutable_nft'):
                    glyph_token_type = GLYPH_TYPE_NFT
                    protocols = [2]
                    is_immutable = (token_type != 'mutable_nft')
                elif token_type == 'ft':
                    glyph_token_type = GLYPH_TYPE_FT
                    protocols = [1]
                    is_immutable = True
                elif token_type == 'delegate':
                    glyph_token_type = GLYPH_TYPE_FT  # Delegates are FT-like
                    protocols = [3]
                    is_immutable = True
                else:
                    continue  # Unknown type
                
                # Create unified Glyph object
                glyph = Glyph(
                    ref=token_id,
                    token_type=glyph_token_type,
                    p=protocols,
                    name='Unknown',  # Will be populated when reveal is processed
                    ticker=None,
                    type='unknown',
                    description='',
                    immutable=is_immutable,
                    attrs={},
                    author='',
                    container='',
                    is_container=False,
                    spent=False,
                    fresh=True,
                    melted=False,
                    sealed=False,
                    swap_pending=False,
                    value=int((value or 0) * 100000000),  # Convert to satoshis
                    location=None,
                    reveal_outpoint=None,
                    height=block_height,
                    timestamp=None,
                    embed_type=None,
                    embed_data=None,
                    remote_type=None,
                    remote_url=None,
                    created_at=datetime.datetime.utcnow()
                )
                glyph_objs.append(glyph)
                existing_refs.add(token_id)
                
                # Create GlyphAction for mint
                action = GlyphAction(
                    ref=token_id,
                    type=ACTION_TYPE_MINT,
                    txid=txid,
                    height=block_height,
                    timestamp=datetime.datetime.utcnow(),
                    action_metadata={'owner': address, 'value': value or 0}
                )
                action_objs.append(action)
                
                # Extract files
                metadata = {'ref': token_ref, 'type': token_type}
                file_objs.extend(extract_files_from_metadata(token_id, 'glyph', metadata))
            
            # Bulk insert unified Glyphs
            if glyph_objs:
                db.bulk_save_objects(glyph_objs)
                total_glyphs += len(glyph_objs)
            if action_objs:
                db.bulk_save_objects(action_objs)
                total_actions += len(action_objs)
            if file_objs:
                db.bulk_save_objects(file_objs)
                total_files += len(file_objs)
            
            # Update containers
            for container_id, owner in containers_to_update:
                update_container(db, container_id, owner)
            
            # Update progress
            status.last_processed_id = last_id
            status.total_processed = (status.total_processed or 0) + len(rows)
            status.updated_at = datetime.datetime.utcnow()
            
            db.commit()
            
            batch_count += 1
            progress = min(100, (last_id / max_id) * 100) if max_id > 0 else 100
            
            if batch_count % 10 == 0:  # Log every 10 batches
                logger.info(f"Progress: {progress:.1f}% | ID: {last_id:,}/{max_id:,} | Found: {total_glyphs} glyphs, {total_actions} actions, {total_files} files")
        
        # Mark as complete
        status.is_complete = True
        status.completed_at = datetime.datetime.utcnow()
        db.commit()
        
        logger.info(f"Token backfill complete! Total found: {total_glyphs} glyphs, {total_actions} actions, {total_files} files")
        return True
        
    except Exception as e:
        logger.error(f"Error during token backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def run_if_needed():
    """Run token backfill only if not already complete."""
    db = SessionLocal()
    try:
        if is_backfill_complete(db):
            logger.info("Token backfill already complete, skipping.")
            return True
    finally:
        db.close()
    
    return backfill_tokens()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Backfill tokens from existing UTXOs')
    parser.add_argument('--force', action='store_true', help='Force re-run even if already complete')
    args = parser.parse_args()
    
    backfill_tokens(force=args.force)
