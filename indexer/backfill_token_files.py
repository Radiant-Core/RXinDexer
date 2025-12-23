#!/usr/bin/env python3
"""
Backfill token files from transaction_inputs scriptSig.
Extracts CBOR payloads containing embedded images and metadata.
"""
import os
import sys
import logging
import base64
import binascii
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.session import SessionLocal
from database.models import Glyph, TokenFile, BackfillStatus
import datetime

from indexer.script_utils import decode_and_extract_glyph

try:
    import cbor2
except ImportError:
    logger.error("cbor2 not installed. Run: pip install cbor2")
    sys.exit(1)

BATCH_SIZE = 5000
BACKFILL_TYPE = 'token_files'
GLYPH_MARKER = b'gly'
GLYPH_MARKER_HEX = '676c79'


def extract_cbor_from_scriptsig(script_sig_hex: str) -> dict:
    """
    Extract CBOR payload from a scriptSig hex string.
    The glyph payload is after the 'gly' (676c79) marker, preceded by OP_PUSH3 (03).
    Pattern: 03676c79 <push_opcode> <cbor_data>
    Returns decoded CBOR dict or None.
    """
    if not script_sig_hex:
        return None
    
    try:
        script_lower = script_sig_hex.lower()
        
        # Find the actual glyph marker: OP_PUSH3 (03) + "gly" (676c79)
        marker_pattern = '03676c79'
        marker_pos = script_lower.find(marker_pattern)
        if marker_pos == -1:
            return None
        
        # Convert to bytes for CBOR parsing
        script_bytes = binascii.unhexlify(script_sig_hex)
        marker_byte_pos = marker_pos // 2
        
        # The CBOR data follows after: 03 (1 byte) + gly (3 bytes) = 4 bytes
        after_marker = script_bytes[marker_byte_pos + 4:]
        
        if len(after_marker) < 2:
            return None
        
        # The next byte should be a push opcode for the CBOR data
        first_byte = after_marker[0]
        
        # Method 1: Standard push (1-75 bytes)
        if first_byte <= 0x4b and first_byte > 0:
            push_size = first_byte
            if len(after_marker) >= push_size + 1:
                try:
                    cbor_data = after_marker[1:1+push_size]
                    decoded = cbor2.loads(cbor_data)
                    if isinstance(decoded, dict):
                        return decoded
                except:
                    pass
        
        # Method 2: OP_PUSHDATA1 (4c)
        elif first_byte == 0x4c:
            if len(after_marker) >= 2:
                push_size = after_marker[1]
                if len(after_marker) >= push_size + 2:
                    try:
                        cbor_data = after_marker[2:2+push_size]
                        decoded = cbor2.loads(cbor_data)
                        if isinstance(decoded, dict):
                            return decoded
                    except:
                        pass
        
        # Method 3: OP_PUSHDATA2 (4d)
        elif first_byte == 0x4d:
            if len(after_marker) >= 3:
                push_size = int.from_bytes(after_marker[1:3], 'little')
                if len(after_marker) >= push_size + 3:
                    try:
                        cbor_data = after_marker[3:3+push_size]
                        decoded = cbor2.loads(cbor_data)
                        if isinstance(decoded, dict):
                            return decoded
                    except:
                        pass
        
        # Method 4: Try direct CBOR decode at various offsets
        for start_offset in range(min(5, len(after_marker))):
            try:
                cbor_data = after_marker[start_offset:]
                decoded = cbor2.loads(cbor_data)
                if isinstance(decoded, dict):
                    return decoded
            except:
                continue
        
        return None
        
    except Exception as e:
        return None


def extract_files_from_cbor(token_id: str, token_type: str, cbor_data: dict) -> list:
    """
    Extract embedded and remote files from decoded CBOR payload.
    Returns list of TokenFile objects.
    """
    files = []
    if not isinstance(cbor_data, dict):
        return files
    
    for key, value in cbor_data.items():
        if key in ('p', 'in', 'by', 'attrs'):  # Skip protocol fields
            continue
            
        if not isinstance(value, dict):
            continue
        
        # Check for embedded file (has 't' for type and 'b' for binary data)
        if 't' in value and 'b' in value:
            try:
                file_data = value['b']
                
                # Handle CBORTag objects (common in glyph payloads)
                if hasattr(file_data, 'value'):
                    file_data = file_data.value
                
                if isinstance(file_data, (bytes, bytearray)):
                    if len(file_data) == 0:
                        continue  # Skip empty files
                    encoded_data = base64.b64encode(file_data).decode('utf-8')
                elif isinstance(file_data, list):
                    if len(file_data) == 0:
                        continue  # Skip empty files
                    encoded_data = base64.b64encode(bytes(file_data)).decode('utf-8')
                else:
                    continue
                
                files.append(TokenFile(
                    token_id=token_id,
                    token_type=token_type,
                    file_key=key,
                    mime_type=value.get('t', 'application/octet-stream'),
                    file_data=encoded_data,
                    file_size=len(file_data) if isinstance(file_data, (bytes, bytearray, list)) else None,
                    created_at=datetime.datetime.utcnow()
                ))
            except Exception as e:
                logger.debug(f"Failed to extract embedded file {key}: {e}")
        
        # Check for remote file (has 'u' for URL)
        elif 'u' in value:
            try:
                file_hash = value.get('h', '')
                if isinstance(file_hash, (bytes, bytearray)):
                    file_hash = file_hash.hex()
                
                files.append(TokenFile(
                    token_id=token_id,
                    token_type=token_type,
                    file_key=key,
                    mime_type=value.get('t', ''),
                    remote_url=value.get('u', ''),
                    file_hash=str(file_hash) if file_hash else None,
                    created_at=datetime.datetime.utcnow()
                ))
            except Exception as e:
                logger.debug(f"Failed to extract remote file {key}: {e}")
    
    return files


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
    """Check if token files backfill has already completed."""
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    if not status or not status.is_complete:
        return False
    # If a previous run marked complete but extracted 0 files, treat as incomplete.
    # This allows fixes to re-run automatically without requiring manual DB edits.
    if (status.total_processed or 0) == 0:
        return False
    return True


def backfill_token_files(force=False):
    """
    Extract CBOR payloads from reveal transactions for known tokens.
    Uses a targeted approach: look up inputs that spend the commit outputs (refs).
    """
    db = SessionLocal()
    
    try:
        if not force and is_backfill_complete(db):
            logger.info("Token files backfill already completed. Use force=True to re-run.")
            return True
        
        status = get_or_create_backfill_status(db)
        
        # Get existing token_ids with files to avoid duplicates
        existing_files = set()
        for row in db.execute(text("SELECT DISTINCT token_id FROM token_files")).fetchall():
            existing_files.add(row[0])
        logger.info(f"Existing tokens with files: {len(existing_files)}")
        
        # Get all known glyphs (ref is the Photonic ref)
        # We need to find the reveal transaction that spends this commit output
        tokens_to_process = []
        for row in db.execute(text("""
            SELECT ref, 'glyph' as type FROM glyphs 
            WHERE ref NOT IN (SELECT DISTINCT token_id FROM token_files)
        """)).fetchall():
            tokens_to_process.append((row[0], row[1]))
        
        logger.info(f"Tokens to process for files: {len(tokens_to_process)}")
        
        if not tokens_to_process:
            logger.info("No tokens need file extraction.")
            status.is_complete = True
            status.completed_at = datetime.datetime.utcnow()
            db.commit()
            return True
        
        total_files = 0
        processed = 0
        
        def _reverse_txid_hex(txid_hex: str) -> str:
            if not isinstance(txid_hex, str) or len(txid_hex) != 64:
                return txid_hex
            return ''.join([txid_hex[i:i+2] for i in range(0, 64, 2)][::-1])

        def _txid_candidates_from_token_id(token_id: str) -> list:
            if not isinstance(token_id, str) or len(token_id) < 64:
                return []
            txid_hex = token_id[:64]
            return list({txid_hex, _reverse_txid_hex(txid_hex)})

        def _vout_candidates_from_token_id(token_id: str) -> list:
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

        for token_id, token_type in tokens_to_process:
            if token_id in existing_files:
                continue
            
            if len(token_id) < 72:
                continue

            txid_candidates = _txid_candidates_from_token_id(token_id)
            vout_candidates = _vout_candidates_from_token_id(token_id)

            if not txid_candidates or not vout_candidates:
                processed += 1
                continue

            query = text("""
                SELECT script_sig FROM transaction_inputs
                WHERE spent_txid = :txid AND spent_vout = :vout
                LIMIT 1
            """)

            result = None
            for txid in txid_candidates:
                for vout in vout_candidates:
                    result = db.execute(query, {'txid': txid, 'vout': vout}).fetchone()
                    if result and result[0]:
                        break
                if result and result[0]:
                    break

            if not result or not result[0]:
                processed += 1
                continue
            
            script_sig = result[0]
            
            # Decode glyph metadata (includes embedded_files/remote_files)
            meta = decode_and_extract_glyph(script_sig)
            if not meta:
                processed += 1
                continue
            
            # Extract files from decoded metadata
            files = []
            try:
                embedded = meta.get('embedded_files') if isinstance(meta, dict) else None
                remote = meta.get('remote_files') if isinstance(meta, dict) else None
                # Prefer the nested maps produced by extract_glyph_metadata
                if isinstance(embedded, dict):
                    files.extend(extract_files_from_cbor(token_id, token_type, embedded))
                if isinstance(remote, dict):
                    files.extend(extract_files_from_cbor(token_id, token_type, remote))
                # Fallback: attempt top-level (for older formats)
                if not files and isinstance(meta, dict):
                    files.extend(extract_files_from_cbor(token_id, token_type, meta))
            except Exception:
                files = []
            if files:
                try:
                    db.bulk_save_objects(files)
                    db.commit()
                    total_files += len(files)
                    existing_files.add(token_id)
                    logger.debug(f"Extracted {len(files)} files for token {token_id[:16]}...")
                except IntegrityError:
                    db.rollback()
            
            processed += 1
            
            if processed % 100 == 0:
                progress = (processed / len(tokens_to_process)) * 100
                logger.info(f"Progress: {progress:.1f}% | Processed: {processed}/{len(tokens_to_process)} | Files found: {total_files}")
                
                # Update status
                status.total_processed = processed
                status.updated_at = datetime.datetime.utcnow()
                db.commit()
        
        # Mark as complete
        status.is_complete = True
        status.completed_at = datetime.datetime.utcnow()
        status.total_processed = total_files
        db.commit()
        
        logger.info(f"Token files backfill complete! Total files extracted: {total_files}")
        return True
        
    except Exception as e:
        logger.error(f"Error during token files backfill: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        raise
    finally:
        db.close()


def run_if_needed():
    """Run token files backfill only if not already complete."""
    force = os.getenv("FORCE_TOKEN_FILES_BACKFILL")
    if force is not None and force.strip().lower() in ("1", "true", "yes", "y", "on"):
        logger.info("FORCE_TOKEN_FILES_BACKFILL is enabled; forcing token files backfill.")
        return backfill_token_files(force=True)

    db = SessionLocal()
    try:
        if is_backfill_complete(db):
            logger.info("Token files backfill already complete, skipping.")
            return True
    finally:
        db.close()

    return backfill_token_files()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Backfill token files from transaction_inputs')
    parser.add_argument('--force', action='store_true', help='Force re-run even if already complete')
    args = parser.parse_args()
    
    backfill_token_files(force=args.force)
