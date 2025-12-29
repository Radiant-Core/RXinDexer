# Transaction and token parsing logic

import os
import base64
import json
from database.models import (
    UTXO, Transaction, TokenFile, Container,
    # New unified Glyph model (primary)
    Glyph, GlyphAction,
    # Legacy models (kept for backward compatibility during migration)
    GlyphToken, NFT,
    # Constants
    GLYPH_TYPE_NFT, GLYPH_TYPE_FT, GLYPH_TYPE_DAT, GLYPH_TYPE_CONTAINER, GLYPH_TYPE_USER,
    ACTION_TYPE_MINT, ACTION_TYPE_TRANSFER, ACTION_TYPE_MELT,
    CONTRACT_TYPE_NFT, CONTRACT_TYPE_FT, CONTRACT_TYPE_RXD,
)
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import datetime
import sys

import cbor2  # Make sure cbor2 is in requirements.txt
import binascii

# Import token tracking for live holder/supply updates
try:
    from indexer.token_tracking import update_token_holders, record_token_burn
    TOKEN_TRACKING_ENABLED = True
except ImportError:
    TOKEN_TRACKING_ENABLED = False

# Skip spent checks during bulk sync for faster initial indexing
# SKIP_SPENT_CHECK=1: Always skip (manual mode)
# SKIP_SPENT_CHECK=auto: Skip when sync lag > threshold (recommended)
# SKIP_SPENT_CHECK=0: Never skip
SKIP_SPENT_CHECK_MODE = os.getenv("SKIP_SPENT_CHECK", "auto")
SKIP_SPENT_CHECK_THRESHOLD = int(os.getenv("SKIP_SPENT_CHECK_THRESHOLD", "1000"))  # Skip if lag > this

# Use COPY for bulk inserts (much faster than INSERT)
# Set USE_COPY=1 to enable, USE_COPY=0 to disable
USE_COPY_MODE = os.getenv("USE_COPY", "1") == "1"

def should_skip_spent_check(sync_lag: int = None) -> bool:
    """Determine if spent check should be skipped based on mode and sync lag."""
    if SKIP_SPENT_CHECK_MODE == "1":
        return True
    elif SKIP_SPENT_CHECK_MODE == "0":
        return False
    elif SKIP_SPENT_CHECK_MODE == "auto":
        # Auto mode: skip if sync lag exceeds threshold
        if sync_lag is not None:
            return sync_lag > SKIP_SPENT_CHECK_THRESHOLD
        # If we don't know the lag, check from DB
        try:
            from indexer.sync import rpc_call, get_last_synced_height
            from database.session import get_session
            with get_session() as db:
                node_height = rpc_call("getblockcount")
                db_height = get_last_synced_height(db)
                lag = node_height - db_height
                return lag > SKIP_SPENT_CHECK_THRESHOLD
        except:
            return True  # Default to skip if we can't determine
    return False

# Track current sync lag for spent check decisions
_current_sync_lag = None

def set_current_sync_lag(lag: int):
    """Set the current sync lag for spent check decisions."""
    global _current_sync_lag
    _current_sync_lag = lag


def extract_files_from_metadata(token_id, token_type, metadata):
    """Extract embedded and remote files from token metadata for storage."""
    files = []
    if not isinstance(metadata, dict):
        return files

    def _extract_file_obj(file_key: str, value: dict):
        if not isinstance(value, dict):
            return

        # Embedded file (Photonic Wallet: { t: string, b: Uint8Array })
        if 't' in value and 'b' in value:
            try:
                file_data = value['b']
                if isinstance(file_data, (bytes, bytearray)):
                    encoded = base64.b64encode(file_data).decode('utf-8')
                    size = len(file_data)
                elif isinstance(file_data, list):
                    raw = bytes(file_data)
                    encoded = base64.b64encode(raw).decode('utf-8')
                    size = len(raw)
                else:
                    return

                files.append(TokenFile(
                    token_id=token_id,
                    token_type=token_type,
                    file_key=file_key,
                    mime_type=value.get('t', 'application/octet-stream'),
                    file_data=encoded,
                    file_size=size,
                    created_at=datetime.datetime.utcnow()
                ))
            except Exception:
                return

        # Remote file (Photonic Wallet: { u: string, t?: string, h?: bytes, hs?: bytes })
        elif 'u' in value:
            try:
                files.append(TokenFile(
                    token_id=token_id,
                    token_type=token_type,
                    file_key=file_key,
                    mime_type=value.get('t', ''),
                    remote_url=value.get('u', ''),
                    file_hash=str(value.get('h', '')) if value.get('h') else None,
                    created_at=datetime.datetime.utcnow()
                ))
            except Exception:
                return
    
    # 1) Direct file objects at top-level
    for key, value in metadata.items():
        _extract_file_obj(key, value)

    # 2) Nested file maps from extract_glyph_metadata
    embedded = metadata.get('embedded_files')
    if isinstance(embedded, dict):
        for key, value in embedded.items():
            _extract_file_obj(key, value)

    remote = metadata.get('remote_files')
    if isinstance(remote, dict):
        for key, value in remote.items():
            _extract_file_obj(key, value)
    
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


def parse_transactions(txs, db: Session, block_id=None, block_time=None, block_height=None):
    """
    Parses transactions for UTXOs and tokens.
    Uses bulk inserts for Transactions and UTXOs for higher throughput.
    """
    from database.models import GlyphToken, NFT, UserProfile, Transaction, Glyph, GlyphAction
    import binascii
    from indexer.script_utils import extract_refs_from_script, extract_gly_cbor_from_script, construct_ref
    from database.models import TransactionInput # Import new model
    from sqlalchemy import text, func

    try:
        transaction_objs = []
        utxo_objs = []
        glyph_objs = []  # New unified Glyph objects
        glyph_action_objs = []  # GlyphAction tracking objects
        nft_objs = []  # Legacy NFT objects (for backward compat)
        legacy_glyph_objs = []  # Legacy GlyphToken objects (for backward compat)
        input_objs_with_txid = [] # List of (parent_txid, input_obj)

        output_refs_by_txid = {}
        tx_height_by_txid = {}

        # Map token_id (commit outpoint ref) -> extracted glyph metadata from reveal tx input scriptSig
        reveal_metadata_by_token_id = {}
        token_obj_by_token_id = {}
        existing_token_row_by_token_id = {}
        token_files_present_by_token_id = {}
        resolved_token_id_by_commit_outpoint = {}

        def maybe_persist_token_files(token_id: str, token_type: str, metadata: dict):
            """Persist token files in a separate transaction to avoid corrupting main session."""
            present = token_files_present_by_token_id.get(token_id)
            if present is None:
                try:
                    present = db.query(TokenFile.id).filter(TokenFile.token_id == token_id).limit(1).first() is not None
                except Exception:
                    db.rollback()  # Rollback on query failure
                    present = False
                token_files_present_by_token_id[token_id] = present

            if present:
                return

            file_objs = extract_files_from_metadata(token_id, token_type, metadata)
            if file_objs:
                try:
                    # Use a savepoint so failures don't corrupt the main transaction
                    db.begin_nested()
                    db.bulk_save_objects(file_objs)
                    db.commit()  # Commit the savepoint
                    token_files_present_by_token_id[token_id] = True
                except Exception as e:
                    db.rollback()  # Rollback only the savepoint
                    # Silently continue - token files are non-critical

        for tx in txs:
            txid = tx['txid']
            total_output = sum(vout.get('value', 0) for vout in tx.get('vout', []))
            # Use per-transaction metadata if available (batched mode), else use function params
            tx_block_id = tx.get('_block_id', block_id)
            tx_block_time = tx.get('_block_time', block_time)
            tx_block_height = tx.get('_block_height', block_height)
            try:
                if tx_block_height is not None:
                    tx_height_by_txid[txid] = int(tx_block_height)
            except Exception:
                pass
            tx_time = tx_block_time if tx_block_time else datetime.datetime.utcnow()
            txn = Transaction(
                txid=txid,
                block_id=tx_block_id,
                block_height=tx_block_height,
                version=tx.get('version', 1),
                locktime=tx.get('locktime', 0),
                created_at=tx_time
            )
            transaction_objs.append(txn)

            # Process Inputs (vin)
            for i, vin in enumerate(tx.get('vin', [])):
                script_sig = vin.get('scriptSig', {}).get('hex', None)
                coinbase = vin.get('coinbase', None)

                # If this is a reveal input, decode metadata from scriptSig so we can attach
                # human-readable name/ticker to the token identified by the spent outpoint.
                if script_sig and isinstance(script_sig, str):
                    try:
                        # Photonic Wallet convention: OP_PUSH3 (03) + "gly" (676c79)
                        if '03676c79' in script_sig.lower() or '676c79' in script_sig.lower():
                            from indexer.script_utils import decode_and_extract_glyph, construct_ref

                            commit_txid = vin.get('txid')
                            commit_vout = vin.get('vout')
                            if commit_txid and commit_vout is not None:
                                commit_key = f"{commit_txid}:{int(commit_vout)}"
                                
                                # Use the new construct_ref function that matches reference implementation
                                # Canonical ref format: txid bytes reversed (LE) + vout as 4-byte LE
                                canonical_ref = construct_ref(commit_txid, int(commit_vout))
                                
                                # Also keep legacy formats for backward compatibility with existing DB records
                                commit_txid_be = commit_txid
                                commit_txid_le = bytes.fromhex(commit_txid)[::-1].hex() if len(commit_txid) == 64 else None
                                vout_le_hex = int(commit_vout).to_bytes(4, 'little').hex()
                                vout_be_hex = f"{int(commit_vout):08x}"

                                # Canonical ref first, then legacy formats
                                token_id_candidates = [canonical_ref]
                                if commit_txid_le and f"{commit_txid_le}{vout_le_hex}" != canonical_ref:
                                    token_id_candidates.append(f"{commit_txid_le}{vout_le_hex}")
                                if commit_txid_le:
                                    token_id_candidates.append(f"{commit_txid_le}{vout_be_hex}")
                                token_id_candidates.append(f"{commit_txid_be}{vout_le_hex}")
                                token_id_candidates.append(f"{commit_txid_be}{vout_be_hex}")

                                meta = decode_and_extract_glyph(script_sig, txid=txid)
                                if meta:
                                    meta = dict(meta)
                                    meta['reveal_txid'] = txid

                                    # Persist token_files immediately for the resolved token_id, so fresh installs get icons
                                    # without waiting for a post-sync backfill.
                                    try:
                                        resolved = resolved_token_id_by_commit_outpoint.get(commit_key)
                                        resolved_type = None
                                        if resolved is None:
                                            resolved = None
                                            # Prefer an exact token_id present in our DB (glyph or nft)
                                            try:
                                                hit = db.query(GlyphToken.token_id).filter(GlyphToken.token_id.in_(token_id_candidates)).limit(1).first()
                                                if hit:
                                                    resolved = hit[0]
                                                    resolved_type = 'glyph'
                                            except Exception:
                                                pass
                                            if resolved is None:
                                                try:
                                                    hit = db.query(NFT.token_id).filter(NFT.token_id.in_(token_id_candidates)).limit(1).first()
                                                    if hit:
                                                        resolved = hit[0]
                                                        resolved_type = 'nft'
                                                except Exception:
                                                    pass

                                            # Fallback to canonical Photonic ref (txid_le + vout_le)
                                            if resolved is None and commit_txid_le:
                                                resolved = f"{commit_txid_le}{vout_le_hex}"
                                            resolved_token_id_by_commit_outpoint[commit_key] = resolved

                                        if resolved:
                                            if resolved_type is None:
                                                try:
                                                    protocols = meta.get('protocols')
                                                    if isinstance(protocols, list) and 2 in protocols:
                                                        resolved_type = 'nft'
                                                    else:
                                                        resolved_type = 'glyph'
                                                except Exception:
                                                    resolved_type = 'glyph'
                                            maybe_persist_token_files(resolved, resolved_type, meta)
                                    except Exception:
                                        pass

                                    for token_id_cand in token_id_candidates:
                                        reveal_metadata_by_token_id[token_id_cand] = meta
                                        try:
                                            # Persist reveal metadata for tokens that were created in earlier blocks
                                            db.execute(text("""
                                                UPDATE glyph_tokens SET
                                                    name = COALESCE(:name, name),
                                                    ticker = COALESCE(:ticker, ticker),
                                                    description = COALESCE(:description, description),
                                                    protocol_type = COALESCE(:protocol_type, protocol_type),
                                                    token_type_name = COALESCE(:token_type_name, token_type_name),
                                                    immutable = COALESCE(:immutable, immutable),
                                                    license = COALESCE(:license, license),
                                                    attrs = COALESCE(CAST(:attrs AS json), attrs),
                                                    location = COALESCE(:location, location),
                                                    author = COALESCE(:author, author),
                                                    container = COALESCE(:container, container),
                                                    max_supply = COALESCE(:max_supply, max_supply),
                                                    premine = COALESCE(:premine, premine),
                                                    difficulty = COALESCE(:difficulty, difficulty),
                                                    max_height = COALESCE(:max_height, max_height),
                                                    reward = COALESCE(:reward, reward),
                                                    icon_mime_type = COALESCE(:icon_mime_type, icon_mime_type),
                                                    icon_url = COALESCE(:icon_url, icon_url),
                                                    icon_data = COALESCE(:icon_data, icon_data),
                                                    reveal_txid = COALESCE(:reveal_txid, reveal_txid),
                                                    updated_at = NOW()
                                                WHERE token_id = :token_id
                                                  AND (name IS NULL OR name = '')
                                            """), {
                                                'token_id': token_id_cand,
                                                'name': meta.get('name'),
                                                'ticker': meta.get('ticker') or ((meta.get('name') or '')[:10] or None),
                                                'description': meta.get('description'),
                                                'protocol_type': meta.get('protocol_type'),
                                                'token_type_name': meta.get('token_type_name'),
                                                'immutable': meta.get('immutable'),
                                                'license': meta.get('license'),
                                                'attrs': json.dumps(meta.get('attrs')) if meta.get('attrs') else None,
                                                'location': meta.get('location'),
                                                'author': meta.get('author'),
                                                'container': meta.get('container'),
                                                'max_supply': meta.get('max_supply'),
                                                'premine': meta.get('premine'),
                                                'difficulty': meta.get('difficulty'),
                                                'max_height': meta.get('max_height'),
                                                'reward': meta.get('reward'),
                                                'icon_mime_type': meta.get('icon_mime_type'),
                                                'icon_url': meta.get('icon_url'),
                                                'icon_data': meta.get('icon_data'),
                                                'reveal_txid': meta.get('reveal_txid'),
                                            })
                                        except Exception:
                                            pass

                                        try:
                                            db.execute(text("""
                                                UPDATE glyphs SET
                                                    name = COALESCE(NULLIF(:name, ''), name),
                                                    ticker = COALESCE(NULLIF(:ticker, ''), ticker),
                                                    description = COALESCE(NULLIF(:description, ''), description),
                                                    author = COALESCE(NULLIF(:author, ''), author),
                                                    container = COALESCE(NULLIF(:container, ''), container),
                                                    embed_type = COALESCE(:embed_type, embed_type),
                                                    embed_data = COALESCE(:embed_data, embed_data),
                                                    remote_url = COALESCE(:remote_url, remote_url),
                                                    updated_at = NOW()
                                                WHERE ref = :token_id
                                                  AND (name = '' OR name = 'Unnamed' OR name = 'Unnamed Token')
                                            """), {
                                                'token_id': token_id_cand,
                                                'name': meta.get('name') or '',
                                                'ticker': meta.get('ticker') or ((meta.get('name') or '')[:10] or None),
                                                'description': meta.get('description') or '',
                                                'author': meta.get('author') or '',
                                                'container': meta.get('container') or '',
                                                'embed_type': meta.get('icon_mime_type'),
                                                'embed_data': meta.get('icon_data'),
                                                'remote_url': meta.get('icon_url'),
                                            })
                                        except Exception:
                                            pass

                                        try:
                                            # Persist reveal metadata for NFTs that were created earlier (e.g. via backfill)
                                            # Update both JSON metadata and dedicated columns
                                            db.execute(text("""
                                                UPDATE nfts SET
                                                    nft_metadata = COALESCE(nft_metadata::jsonb, '{}'::jsonb) || (CAST(:meta_json AS jsonb)),
                                                    name = COALESCE(:name, name),
                                                    ticker = COALESCE(:ticker, ticker),
                                                    description = COALESCE(:description, description),
                                                    token_type_name = COALESCE(:token_type_name, token_type_name),
                                                    author = COALESCE(:author, author),
                                                    container = COALESCE(:container, container),
                                                    protocols = COALESCE(CAST(:protocols AS json), protocols),
                                                    protocol_type = COALESCE(:protocol_type, protocol_type),
                                                    immutable = COALESCE(:immutable, immutable),
                                                    attrs = COALESCE(CAST(:attrs AS json), attrs),
                                                    location = COALESCE(:location, location),
                                                    icon_mime_type = COALESCE(:icon_mime_type, icon_mime_type),
                                                    icon_url = COALESCE(:icon_url, icon_url),
                                                    icon_data = COALESCE(:icon_data, icon_data),
                                                    reveal_txid = COALESCE(:reveal_txid, reveal_txid),
                                                    updated_at = NOW()
                                                WHERE token_id = :token_id
                                            """), {
                                                'token_id': token_id_cand,
                                                'meta_json': json.dumps(meta),
                                                'name': meta.get('name'),
                                                'ticker': meta.get('ticker'),
                                                'description': meta.get('description'),
                                                'token_type_name': meta.get('token_type_name'),
                                                'author': meta.get('author'),
                                                'container': meta.get('container'),
                                                'protocols': json.dumps(meta.get('protocols')) if meta.get('protocols') else None,
                                                'protocol_type': meta.get('protocol_type'),
                                                'immutable': meta.get('immutable'),
                                                'attrs': json.dumps(meta.get('attrs')) if meta.get('attrs') else None,
                                                'location': meta.get('location'),
                                                'icon_mime_type': meta.get('icon_mime_type'),
                                                'icon_url': meta.get('icon_url'),
                                                'icon_data': meta.get('icon_data'),
                                                'reveal_txid': meta.get('reveal_txid'),
                                            })
                                        except Exception:
                                            pass

                                        existing = token_obj_by_token_id.get(token_id_cand)
                                        if existing is not None:
                                            existing.name = meta.get('name')
                                            existing.ticker = meta.get('ticker') or (meta.get('name') or '')[:10] or None
                                            existing.description = meta.get('description')
                                            existing.protocols = meta.get('protocols')
                                            existing.protocol_type = meta.get('protocol_type')
                                            existing.token_type_name = meta.get('token_type_name')
                                            existing.immutable = meta.get('immutable')
                                            existing.author = meta.get('author')
                                            existing.container = meta.get('container')
                                            existing.max_supply = meta.get('max_supply')
                                            existing.premine = meta.get('premine')
                                            existing.difficulty = meta.get('difficulty')
                                            existing.max_height = meta.get('max_height')
                                            existing.reward = meta.get('reward')
                                            existing.icon_mime_type = meta.get('icon_mime_type')
                                            existing.icon_url = meta.get('icon_url')
                                            existing.icon_data = meta.get('icon_data')
                                            existing.reveal_txid = meta.get('reveal_txid')
                                            try:
                                                if isinstance(existing.token_metadata, dict):
                                                    existing.token_metadata.update(meta)
                                            except Exception:
                                                pass
                    except Exception:
                        pass
                
                inp = TransactionInput(
                    transaction_id=None, # Will set after tx insert
                    input_index=i,
                    spent_txid=vin.get('txid', None),
                    spent_vout=vin.get('vout', None),
                    script_sig=script_sig,
                    sequence=vin.get('sequence', 0),
                    coinbase=coinbase
                )
                input_objs_with_txid.append((txid, inp))

            for vout in tx.get('vout', []):
                address = None
                if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                    address = vout['scriptPubKey']['addresses'][0]
                
                script_type = vout.get('scriptPubKey', {}).get('type', 'unknown')
                script_hex = vout.get('scriptPubKey', {}).get('hex', '')
                
                # Store all outputs as UTXOs, including those without addresses (nonstandard/nulldata)
                # Use a special address format for nonstandard scripts without an address
                # This ensures we capture and can query nonstandard scripts
                if not address and (script_type == 'nonstandard' or script_type == 'nulldata'):
                    # Use a special format to indicate nonstandard script without address
                    address = f"NONSTANDARD:{txid}:{vout['n']}"
                
                # Store all outputs, including those with synthetic addresses
                utxo = UTXO(
                    txid=txid,
                    vout=vout['n'],
                    address=address,  # May be None, standard address, or synthetic nonstandard address
                    value=vout.get('value', 0),  # Fixed: using 'value' instead of 'amount' to match DB schema
                    spent=False,
                    transaction_id=None,  # Will update after flush
                    transaction_block_height=tx_block_height,  # Required for partitioning - use per-tx height
                    script_type=script_type,  # Store script_type for better querying
                    script_hex=script_hex if (script_type == 'nonstandard' or script_type == 'nulldata') else None  # Store hex for nonstandard scripts
                )
                utxo_objs.append(utxo)
                
                # Get the recipient address for this output if available
                recipient_address = vout.get('scriptPubKey', {}).get('addresses', [None])[0] 
                
                if script_hex:
                    try:
                        script_bytes = binascii.unhexlify(script_hex)
                    except Exception as e:
                        print(f"[parse_transactions][ERROR] Error decoding script hex for txid={txid}, vout={vout['n']}: {e}"); sys.stdout.flush()
                        continue  # Skip this vout if script decoding fails
                    
                    # =========================================================
                    # TOKEN DETECTION using Photonic Wallet patterns
                    # This detects tokens by their scriptPubKey structure
                    # =========================================================
                    from indexer.script_utils import detect_token_from_script, ref_to_token_id
                    
                    token_info = detect_token_from_script(script_hex)
                    if token_info:
                        token_type = token_info.get('type', 'unknown')
                        token_ref = token_info.get('ref', '')
                        token_address = token_info.get('address', '')
                        
                        # Convert ref to token_id
                        token_id = ref_to_token_id(token_ref) if token_ref else txid
                        token_owner = recipient_address or address

                        try:
                            if token_id:
                                refs = output_refs_by_txid.get(txid)
                                if refs is None:
                                    refs = set()
                                    output_refs_by_txid[txid] = refs
                                refs.add(token_id)
                        except Exception:
                            pass

                        # Best-effort: derive the genesis txid from the ref-based token_id.
                        # ref format is 36 bytes: txid(32) + vout(4) (often little-endian).
                        # We try both as-is and byte-reversed to match our transactions table.
                        genesis_txid = txid
                        if isinstance(token_id, str) and len(token_id) >= 64:
                            candidate = token_id[:64]
                            genesis_txid = candidate
                            try:
                                exists = db.query(Transaction).filter(Transaction.txid == candidate).first()
                                if not exists:
                                    rev = ''.join([candidate[i:i+2] for i in range(0, 64, 2)][::-1])
                                    if db.query(Transaction).filter(Transaction.txid == rev).first():
                                        genesis_txid = rev
                            except Exception:
                                # If lookup fails, fall back to candidate
                                genesis_txid = candidate
                        
                        # Create unified Glyph record for detected token
                        if token_type in ('nft', 'mutable_nft'):
                            # NFT token - create unified Glyph
                            extracted = reveal_metadata_by_token_id.get(token_id)

                            if extracted is None and token_id not in existing_token_row_by_token_id:
                                try:
                                    # Check new Glyph table first, then legacy NFT
                                    existing_token_row_by_token_id[token_id] = db.query(Glyph).filter(
                                        Glyph.ref == token_id,
                                    ).first()
                                    if not existing_token_row_by_token_id[token_id]:
                                        existing_token_row_by_token_id[token_id] = db.query(NFT).filter(
                                            NFT.token_id == token_id,
                                        ).order_by(NFT.id.desc()).first()
                                except Exception:
                                    existing_token_row_by_token_id[token_id] = None

                            existing_record = existing_token_row_by_token_id.get(token_id)
                            metadata = {'ref': token_ref, 'type': token_type}
                            if extracted:
                                try:
                                    metadata.update(extracted)
                                except Exception:
                                    pass
                            elif existing_record:
                                # Try to get metadata from existing record
                                try:
                                    if hasattr(existing_record, 'attrs') and existing_record.attrs:
                                        metadata['attrs'] = existing_record.attrs
                                    if hasattr(existing_record, 'nft_metadata') and isinstance(existing_record.nft_metadata, dict):
                                        metadata.update(existing_record.nft_metadata)
                                    metadata['ref'] = token_ref or metadata.get('ref')
                                    metadata['type'] = token_type
                                except Exception:
                                    pass
                            
                            # Determine immutability using Photonic Wallet logic
                            # Mutable only if both NFT (2) and MUT (5) protocols present
                            protocols_list = extracted.get('protocols', [2]) if extracted else [2]
                            is_immutable = not (2 in protocols_list and 5 in protocols_list)
                            
                            # Determine glyph type from payload.type
                            payload_type = extracted.get('token_type_name') if extracted else None
                            if payload_type == 'user':
                                glyph_token_type = GLYPH_TYPE_USER
                            elif payload_type == 'container':
                                glyph_token_type = GLYPH_TYPE_CONTAINER
                            else:
                                glyph_token_type = GLYPH_TYPE_NFT
                            
                            # Create unified Glyph object
                            glyph = Glyph(
                                ref=token_id,
                                token_type=glyph_token_type,
                                p=protocols_list,
                                name=(extracted.get('name') if extracted else '') or '',
                                ticker=(extracted.get('ticker') if extracted else None),
                                type=payload_type or 'object',
                                description=(extracted.get('description') if extracted else '') or '',
                                immutable=is_immutable,
                                attrs=(extracted.get('attrs') if extracted else {}),
                                author=(extracted.get('author') if extracted else '') or '',
                                container=(extracted.get('container') if extracted else '') or '',
                                is_container=(payload_type == 'container'),
                                spent=False,
                                fresh=True,
                                melted=False,
                                sealed=False,
                                swap_pending=False,
                                value=int(vout.get('value', 0) * 100000000),  # Convert to satoshis
                                location=(extracted.get('location') if extracted else None),
                                reveal_outpoint=f"{extracted.get('reveal_txid')}:{vout['n']}" if extracted and extracted.get('reveal_txid') else None,
                                height=tx_block_height,
                                timestamp=int(tx_block_time.timestamp()) if tx_block_time else None,
                                embed_type=(extracted.get('icon_mime_type') if extracted else None),
                                embed_data=(extracted.get('icon_data') if extracted else None),
                                remote_type=None,
                                remote_url=(extracted.get('icon_url') if extracted else None),
                                created_at=datetime.datetime.utcnow()
                            )
                            glyph_objs.append(glyph)
                            token_obj_by_token_id[token_id] = glyph
                            
                            # Create GlyphAction for mint
                            action = GlyphAction(
                                ref=token_id,
                                type=ACTION_TYPE_MINT,
                                txid=txid,
                                height=tx_block_height,
                                timestamp=tx_block_time or datetime.datetime.utcnow(),
                                action_metadata={'owner': token_owner, 'value': vout.get('value', 0)}
                            )
                            glyph_action_objs.append(action)
                            
                            # Update UTXO with glyph info
                            utxo.is_glyph_reveal = True
                            utxo.glyph_ref = token_id
                            utxo.contract_type = CONTRACT_TYPE_NFT
                            
                            # Extract and store files from metadata
                            maybe_persist_token_files(token_id, 'glyph', metadata)
                                
                        elif token_type in ('ft', 'delegate'):
                            # Fungible token or delegate - create unified Glyph
                            extracted = reveal_metadata_by_token_id.get(token_id)

                            if extracted is None and token_id not in existing_token_row_by_token_id:
                                try:
                                    # Check new Glyph table first, then legacy GlyphToken
                                    existing_token_row_by_token_id[token_id] = db.query(Glyph).filter(
                                        Glyph.ref == token_id,
                                    ).first()
                                    if not existing_token_row_by_token_id[token_id]:
                                        existing_token_row_by_token_id[token_id] = db.query(GlyphToken).filter(
                                            GlyphToken.token_id == token_id,
                                            GlyphToken.name.isnot(None),
                                            GlyphToken.name != ''
                                        ).order_by(GlyphToken.updated_at.desc().nullslast(), GlyphToken.id.desc()).first()
                                except Exception:
                                    existing_token_row_by_token_id[token_id] = None

                            existing_record = existing_token_row_by_token_id.get(token_id)
                            if extracted is None and existing_record is not None:
                                # Extract metadata from existing record
                                extracted = {
                                    'name': getattr(existing_record, 'name', None),
                                    'ticker': getattr(existing_record, 'ticker', None),
                                    'description': getattr(existing_record, 'description', None),
                                    'protocols': getattr(existing_record, 'protocols', getattr(existing_record, 'p', None)),
                                    'protocol_type': getattr(existing_record, 'protocol_type', None),
                                    'token_type_name': getattr(existing_record, 'token_type_name', getattr(existing_record, 'type', None)),
                                    'immutable': getattr(existing_record, 'immutable', None),
                                    'attrs': getattr(existing_record, 'attrs', None),
                                    'location': getattr(existing_record, 'location', None),
                                    'author': getattr(existing_record, 'author', None),
                                    'container': getattr(existing_record, 'container', None),
                                    'icon_mime_type': getattr(existing_record, 'icon_mime_type', getattr(existing_record, 'embed_type', None)),
                                    'icon_url': getattr(existing_record, 'icon_url', getattr(existing_record, 'remote_url', None)),
                                    'icon_data': getattr(existing_record, 'icon_data', getattr(existing_record, 'embed_data', None)),
                                }

                            metadata = {'ref': token_ref}
                            if extracted:
                                try:
                                    metadata.update(extracted)
                                except Exception:
                                    pass

                            # Determine protocols
                            protocols_list = extracted.get('protocols', [1]) if extracted else ([1] if token_type == 'ft' else [3])
                            is_immutable = not (2 in protocols_list and 5 in protocols_list)
                            
                            # Create unified Glyph object for FT
                            glyph = Glyph(
                                ref=token_id,
                                token_type=GLYPH_TYPE_FT,
                                p=protocols_list,
                                name=(extracted.get('name') if extracted else '') or '',
                                ticker=(extracted.get('ticker') if extracted else None) or ((extracted.get('name') or '')[:10] if extracted else None),
                                type=extracted.get('token_type_name', 'fungible') if extracted else 'fungible',
                                description=(extracted.get('description') if extracted else '') or '',
                                immutable=is_immutable,
                                attrs=(extracted.get('attrs') if extracted else {}),
                                author=(extracted.get('author') if extracted else '') or '',
                                container=(extracted.get('container') if extracted else '') or '',
                                is_container=False,
                                spent=False,
                                fresh=True,
                                melted=False,
                                sealed=False,
                                swap_pending=False,
                                value=int(vout.get('value', 0) * 100000000),  # Convert to satoshis
                                location=(extracted.get('location') if extracted else None),
                                reveal_outpoint=f"{extracted.get('reveal_txid')}:{vout['n']}" if extracted and extracted.get('reveal_txid') else None,
                                height=tx_block_height,
                                timestamp=int(tx_block_time.timestamp()) if tx_block_time else None,
                                embed_type=(extracted.get('icon_mime_type') if extracted else None),
                                embed_data=(extracted.get('icon_data') if extracted else None),
                                remote_type=None,
                                remote_url=(extracted.get('icon_url') if extracted else None),
                                created_at=datetime.datetime.utcnow()
                            )
                            glyph_objs.append(glyph)
                            token_obj_by_token_id[token_id] = glyph
                            
                            # Create GlyphAction for mint
                            action = GlyphAction(
                                ref=token_id,
                                type=ACTION_TYPE_MINT,
                                txid=txid,
                                height=tx_block_height,
                                timestamp=tx_block_time or datetime.datetime.utcnow(),
                                action_metadata={'owner': token_owner, 'value': vout.get('value', 0)}
                            )
                            glyph_action_objs.append(action)
                            
                            # Update UTXO with glyph info
                            utxo.is_glyph_reveal = True
                            utxo.glyph_ref = token_id
                            utxo.contract_type = CONTRACT_TYPE_FT
                            
                            # Update container if present
                            if glyph.container:
                                update_container(db, glyph.container, token_owner)
                            
                            # Extract and store files from metadata
                            maybe_persist_token_files(token_id, 'glyph', metadata)
                        
                        # Skip the old glyph marker detection for this output
                        continue
                        
                    # Extract refs for token_id generation (fallback path for scripts with glyph marker)
                    refs = extract_refs_from_script(script_bytes)
                    
                    # Use the enhanced glyph detection with all parameters
                    from indexer.script_utils import decode_glyph
                    
                    # Pass txid and address to the enhanced glyph detection function
                    glyph_data = decode_glyph(script_bytes, txid=txid, address=recipient_address)
                    
                    if glyph_data:
                        try:
                            # Use the structured glyph data
                            payload = glyph_data['payload']
                            files = glyph_data['files']
                            raw_data = glyph_data['raw']
                            is_mineable = glyph_data.get('is_mineable', False)
                            
                            # Extract key metadata
                            protocols = payload.get('p', [])
                            token_type = payload.get('type', None)
                            token_name = payload.get('name', 'unnamed')
                            token_id = binascii.hexlify(refs[0]).decode() if refs else txid
                            token_owner = recipient_address or address
                            
                            # Determine glyph type for unified model
                            glyph_token_type = 'NFT'  # Default
                            if 1 in protocols:
                                glyph_token_type = 'FT'
                            elif token_type == 'user':
                                glyph_token_type = 'USER'
                            elif token_type == 'container':
                                glyph_token_type = 'CONTAINER'
                            elif is_mineable:
                                glyph_token_type = 'DAT'
                            
                            # Create unified Glyph object (PRIMARY)
                            glyph = Glyph(
                                ref=token_id,
                                token_type=glyph_token_type,
                                p=protocols,
                                name=payload.get('name', 'unnamed'),
                                ticker=(payload.get('ticker') or (payload.get('name') or '')[:10] or None),
                                type=token_type or 'unknown',
                                description=payload.get('desc', ''),
                                immutable=not (2 in protocols and 5 in protocols),
                                attrs=payload.get('attrs', {}),
                                author=payload.get('by', ''),
                                container=payload.get('in', ''),
                                is_container=(token_type == 'container'),
                                spent=False,
                                fresh=True,
                                value=int(vout.get('value', 0) * 100000000),
                                height=tx_block_height,
                                timestamp=int(tx_block_time.timestamp()) if tx_block_time else None,
                                embed_type=files.get('embedded', {}).get('t') if files.get('embedded') else None,
                                embed_data=files.get('embedded', {}).get('b') if files.get('embedded') else None,
                                remote_type=files.get('remote', {}).get('t') if files.get('remote') else None,
                                remote_url=files.get('remote', {}).get('src') if files.get('remote') else None,
                                created_at=datetime.datetime.utcnow()
                            )
                            glyph_objs.append(glyph)
                            
                            # Create GlyphAction for mint
                            action = GlyphAction(
                                ref=token_id,
                                type=ACTION_TYPE_MINT,
                                txid=txid,
                                height=tx_block_height,
                                timestamp=tx_block_time or datetime.datetime.utcnow(),
                                action_metadata={'owner': token_owner, 'value': vout.get('value', 0)}
                            )
                            glyph_action_objs.append(action)
                            
                        except Exception as e:
                            print(f"[parse_transactions][ERROR] Glyph processing failed for txid={txid}, vout={vout['n']}: {e}"); sys.stdout.flush()
        # Use COPY for bulk inserts if enabled (much faster)
        if USE_COPY_MODE:
            from indexer.bulk_copy import copy_transactions, copy_utxos, copy_transaction_inputs
            import time as _copy_time
            
            t_copy_start = _copy_time.time()
            
            # Convert transaction objects to dicts for COPY
            tx_dicts = [{
                'txid': t.txid,
                'block_id': t.block_id,
                'block_height': t.block_height,
                'version': t.version,
                'locktime': t.locktime,
                'created_at': t.created_at
            } for t in transaction_objs]
            
            # COPY transactions and get ID mapping
            txid_to_id = copy_transactions(db, tx_dicts)
            
            # Convert UTXO objects to dicts for COPY
            utxo_dicts = [{
                'txid': u.txid,
                'vout': u.vout,
                'address': u.address,
                'value': u.value,
                'transaction_id': txid_to_id.get(u.txid),
                'transaction_block_height': u.transaction_block_height,
                'script_type': u.script_type,
                'script_hex': u.script_hex,
                'is_glyph_reveal': getattr(u, 'is_glyph_reveal', False),
                'glyph_ref': getattr(u, 'glyph_ref', None),
                'contract_type': getattr(u, 'contract_type', None),
            } for u in utxo_objs]
            
            # COPY UTXOs
            copy_utxos(db, utxo_dicts)
            
            # Convert input objects to dicts for COPY
            input_dicts = [{
                'transaction_id': txid_to_id.get(parent_txid),
                'input_index': inp.input_index,
                'spent_txid': inp.spent_txid,
                'spent_vout': inp.spent_vout,
                'script_sig': inp.script_sig,
                'coinbase': inp.coinbase,
                'sequence': inp.sequence
            } for parent_txid, inp in input_objs_with_txid]
            
            # COPY inputs
            copy_transaction_inputs(db, input_dicts)
            
            db.commit()
            
            copy_duration = _copy_time.time() - t_copy_start
            if copy_duration > 5.0:
                print(f"[parse_transactions][COPY] Inserted {len(tx_dicts)} txs, {len(utxo_dicts)} UTXOs, {len(input_dicts)} inputs in {copy_duration:.2f}s"); sys.stdout.flush()
        else:
            # Original bulk_save_objects path
            # Bulk insert transactions and flush to get IDs
            db.bulk_save_objects(transaction_objs)
            db.commit()
            # Batch fetch transaction IDs after bulk insert
            txids = [t.txid for t in transaction_objs]
            tx_rows = db.query(Transaction).filter(Transaction.txid.in_(txids)).all()
            txid_to_id = {row.txid: row.id for row in tx_rows}
            for utxo in utxo_objs:
                utxo.transaction_id = txid_to_id.get(utxo.txid)
            
            # Link Inputs to Transaction IDs
            input_objs = []
            for parent_txid, inp in input_objs_with_txid:
                inp.transaction_id = txid_to_id.get(parent_txid)
                if inp.transaction_id: # Should always be true
                    input_objs.append(inp)
            
            # Bulk insert inputs
            if input_objs:
                db.bulk_save_objects(input_objs)
                db.commit()

            # Deduplication: Check for existing UTXOs to prevent duplicate key violations
            utxo_keys = [(utxo.txid, utxo.transaction_block_height) for utxo in utxo_objs]
            if utxo_objs:
                import time as _p_time
                t_dedup = _p_time.time()
                # Optimization: Since we are processing per-block, all UTXOs share the same block_height
                # We can use a much faster IN clause instead of OR(AND(...))
                current_block_height = block_height
                txids_in_batch = [utxo.txid for utxo in utxo_objs]
                
                existing_utxos = db.query(UTXO.txid).filter(
                    UTXO.transaction_block_height == current_block_height,
                    UTXO.txid.in_(txids_in_batch)
                ).all()
                
                existing_txids = set(row.txid for row in existing_utxos)
                new_utxos = [utxo for utxo in utxo_objs if utxo.txid not in existing_txids]
                dedup_duration = _p_time.time() - t_dedup
                if dedup_duration > 1.0:
                    print(f"[parse_transactions][WARNING] Slow dedup: checked {len(utxo_objs)} UTXOs in {dedup_duration:.2f}s"); sys.stdout.flush()
                
                if new_utxos:
                    db.bulk_save_objects(new_utxos)
                    db.commit()
            else:
                # No UTXOs to process
                pass

        # Bulk insert unified Glyphs (new primary table)
        # Deduplicate by ref - keep only the first occurrence of each ref
        if glyph_objs:
            seen_refs = set()
            unique_glyph_objs = []
            for g in glyph_objs:
                if g.ref not in seen_refs:
                    seen_refs.add(g.ref)
                    unique_glyph_objs.append(g)
            
            if unique_glyph_objs:
                # Use ON CONFLICT DO UPDATE to handle duplicates - update state fields for transfers
                from sqlalchemy.dialects.postgresql import insert
                for g in unique_glyph_objs:
                    stmt = insert(Glyph).values(
                        ref=g.ref,
                        token_type=g.token_type or 'NFT',
                        p=g.p,
                        name=g.name or '',
                        ticker=g.ticker,
                        type=g.type or 'object',
                        description=g.description or '',
                        immutable=g.immutable,
                        attrs=g.attrs,
                        author=g.author,
                        container=g.container,
                        is_container=g.is_container,
                        spent=g.spent,
                        fresh=g.fresh,
                        melted=g.melted,
                        sealed=g.sealed,
                        swap_pending=g.swap_pending,
                        value=g.value,
                        location=g.location,
                        reveal_outpoint=g.reveal_outpoint,
                        height=g.height,
                        timestamp=g.timestamp,
                        embed_type=g.embed_type,
                        embed_data=g.embed_data,
                        remote_type=g.remote_type,
                        remote_url=g.remote_url,
                        created_at=g.created_at
                    ).on_conflict_do_update(
                        index_elements=['ref'],
                        set_={
                            'spent': g.spent,
                            'fresh': False,  # No longer fresh after transfer
                            'melted': g.melted,
                            'sealed': g.sealed,
                            'swap_pending': g.swap_pending,
                            'value': g.value,
                            'height': g.height,
                            'timestamp': g.timestamp,
                            'updated_at': func.now()
                        }
                    )
                    db.execute(stmt)
                db.commit()
            
            # Live holder tracking - update balances for new token outputs
            if TOKEN_TRACKING_ENABLED:
                # Build quick lookup of owner/value from actions created in this batch.
                owner_by_ref = {}
                for action in glyph_action_objs:
                    try:
                        if action and action.action_metadata and action.action_metadata.get('owner'):
                            owner_by_ref[action.ref] = action.action_metadata.get('owner')
                    except Exception:
                        continue

                for g in glyph_objs:
                    ref = getattr(g, 'ref', None)
                    if not ref:
                        continue

                    owner = owner_by_ref.get(ref)
                    if not owner:
                        continue

                    try:
                        update_token_holders(db, ref, [{
                            'address': owner,
                            'amount': g.value or 1,
                            'is_receive': True
                        }])
                    except Exception:
                        pass
        
        # Bulk insert GlyphActions
        if glyph_action_objs:
            db.bulk_save_objects(glyph_action_objs)
            db.commit()
                            
        # NOTE: Legacy nft_objs and legacy_glyph_objs lists are no longer populated.
        # All token creation now uses the unified Glyph model above.
        # Legacy tables (nfts, glyph_tokens) remain in schema for API backward compatibility
        # but will not receive new data from the parser.

        # Update User Profiles and Containers
        user_updates = {}  # Map address -> set(containers)

        # Process unified Glyphs for container info
        for g in glyph_objs:
            # Get container from the Glyph model
            container_ref = getattr(g, 'container', None)
            if container_ref:
                # Find owner from corresponding action
                owner = None
                for action in glyph_action_objs:
                    if action.ref == g.ref and action.action_metadata:
                        owner = action.action_metadata.get('owner')
                        break
                if owner:
                    if owner not in user_updates:
                        user_updates[owner] = set()
                    user_updates[owner].add(container_ref)

        # Legacy: Process NFTs for container info
        for n in nft_objs:
            if not n.owner:
                continue
            if n.collection:
                if n.owner not in user_updates:
                    user_updates[n.owner] = set()
                user_updates[n.owner].add(n.collection)

        # Also ensure we create profiles for ANY user involved in glyphs/NFTs, even without containers
        all_token_owners = set()
        # Get owners from GlyphAction action_metadata for unified Glyphs
        for action in glyph_action_objs:
            if action.action_metadata and action.action_metadata.get('owner'):
                all_token_owners.add(action.action_metadata['owner'])
        # Legacy: get owners from NFT objects
        for n in nft_objs:
            if n.owner: all_token_owners.add(n.owner)
            
        for owner in all_token_owners:
            if owner not in user_updates:
                user_updates[owner] = set()

        # Perform Bulk Update/Insert of User Profiles
        if user_updates:
            import json
            addresses = list(user_updates.keys())
            
            # Fetch existing profiles
            existing_profiles = db.query(UserProfile).filter(UserProfile.address.in_(addresses)).all()
            existing_map = {p.address: p for p in existing_profiles}
            
            profiles_to_save = []
            
            for address, new_containers in user_updates.items():
                profile = existing_map.get(address)
                
                if profile:
                    # Update existing profile
                    current_containers = []
                    if profile.containers:
                        if isinstance(profile.containers, str):
                            try:
                                current_containers = json.loads(profile.containers)
                            except:
                                current_containers = []
                        elif isinstance(profile.containers, list):
                            current_containers = profile.containers
                    
                    # Merge and deduplicate
                    updated_set = set(current_containers) | new_containers
                    profile.containers = list(updated_set)
                    # Add to session if not already (it should be attached)
                    profiles_to_save.append(profile)
                else:
                    # Create new profile
                    new_profile = UserProfile(
                        address=address,
                        containers=list(new_containers),
                        created_at=datetime.datetime.utcnow()
                    )
                    profiles_to_save.append(new_profile)
            
            # Bulk save
            if profiles_to_save:
                # For updates, SQLAlchemy session tracking handles it. 
                # For new objects, we need to add them.
                # Using bulk_save_objects for mixed insert/update is tricky with ORM,
                # but add_all works well for this volume.
                db.add_all(profiles_to_save)
                db.commit()

    except Exception as e:
        print(f"[parse_transactions][EXCEPTION][BULK] Exception occurred: {e}"); sys.stdout.flush()
        db.rollback()
    finally:
        pass

    # Batch mark spent UTXOs
    # Can be skipped during bulk sync for faster initial indexing
    if should_skip_spent_check(_current_sync_lag):
        # Skip spent check during bulk sync - will be backfilled when caught up
        db.commit()
        return
    
    from sqlalchemy import tuple_
    spent_keys_map = {}  # Maps (spent_txid, spent_vout) -> spending_txid
    for tx in txs:
        spending_txid = tx['txid']
        for vin in tx.get('vin', []):
            spent_txid = vin.get('txid')
            spent_vout = vin.get('vout')
            if spent_txid and spent_vout is not None:
                spent_keys_map[(spent_txid, spent_vout)] = spending_txid
    
    if spent_keys_map:
        import time as _p_time
        from sqlalchemy import bindparam, text
        t_spent = _p_time.time()
        
        spent_keys = list(spent_keys_map.keys())
        total_updates = 0
        
        # Chunk size for processing spent keys
        # ORBSTACK MODE: Increased chunk size for better SSD performance
        CHUNK_SIZE = 2000

        input_token_amounts_by_spending_txid = {}
        burn_statement_timeout_ms = None
        try:
            burn_statement_timeout_ms = int(os.getenv('BURN_DETECT_STATEMENT_TIMEOUT_MS', '1500') or 1500)
        except Exception:
            burn_statement_timeout_ms = 1500
        for i in range(0, len(spent_keys), CHUNK_SIZE):
            chunk_keys = spent_keys[i:i + CHUNK_SIZE]
            
            # Optimization 1: Query by txid list first (efficient 1D index scan)
            # instead of tuple IN clause which confuses planner on partitioned tables
            chunk_txids = list(set(k[0] for k in chunk_keys))
            
            candidates = None
            try:
                if burn_statement_timeout_ms and burn_statement_timeout_ms > 0:
                    db.execute(text(f"SET LOCAL statement_timeout TO {int(burn_statement_timeout_ms)}"))
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass

            try:
                candidates = db.query(
                    UTXO.txid,
                    UTXO.vout,
                    UTXO.transaction_block_height,
                    UTXO.glyph_ref,
                    UTXO.value,
                ).filter(
                    UTXO.txid.in_(chunk_txids),
                    UTXO.spent == False
                ).all()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                candidates = db.query(
                    UTXO.txid,
                    UTXO.vout,
                    UTXO.transaction_block_height,
                ).filter(
                    UTXO.txid.in_(chunk_txids),
                    UTXO.spent == False
                ).all()
            finally:
                try:
                    db.execute(text("SET LOCAL statement_timeout TO 0"))
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            
            updates_list = []
            for row in candidates:
                spending_txid = spent_keys_map.get((row.txid, row.vout))
                if spending_txid:
                    # We must update using the Composite PK (txid, vout, block_height)
                    # because 'id' has no index and causes full table scans
                    updates_list.append({
                        'b_txid': row.txid, 
                        'b_vout': row.vout, 
                        'b_height': row.transaction_block_height,
                        'b_spent_in': spending_txid
                    })

                    try:
                        ref = getattr(row, 'glyph_ref', None)
                        if isinstance(ref, str) and ref:
                            amt = 0
                            try:
                                amt = int(float(getattr(row, 'value', 0) or 0) * 100000000)
                            except Exception:
                                amt = 0
                            if amt:
                                ref_map = input_token_amounts_by_spending_txid.get(spending_txid)
                                if ref_map is None:
                                    ref_map = {}
                                    input_token_amounts_by_spending_txid[spending_txid] = ref_map
                                ref_map[ref] = int(ref_map.get(ref, 0) or 0) + int(amt)
                    except Exception:
                        pass
            
            if updates_list:
                 # Use Core update with bindparam for bulk execution using the index
                 stmt = (
                     UTXO.__table__.update()
                     .where(UTXO.txid == bindparam('b_txid'))
                     .where(UTXO.vout == bindparam('b_vout'))
                     .where(UTXO.transaction_block_height == bindparam('b_height'))
                     .values(spent=True, spent_in_txid=bindparam('b_spent_in'))
                 )
                 db.execute(stmt, updates_list)
                 total_updates += len(updates_list)

        if TOKEN_TRACKING_ENABLED and input_token_amounts_by_spending_txid:
            try:
                from indexer.script_utils import detect_token_burn
            except Exception:
                detect_token_burn = None

            if detect_token_burn is not None:
                for spending_txid, ref_amounts in input_token_amounts_by_spending_txid.items():
                    try:
                        out_refs = output_refs_by_txid.get(spending_txid) or set()
                        burned_refs = detect_token_burn(list(ref_amounts.keys()), list(out_refs))
                        if not burned_refs:
                            continue
                        b_height = tx_height_by_txid.get(spending_txid)
                        for burned_ref in burned_refs:
                            amt = int(ref_amounts.get(burned_ref) or 0)
                            if amt <= 0:
                                continue
                            try:
                                record_token_burn(
                                    db,
                                    burned_ref,
                                    spending_txid,
                                    amt,
                                    burner_address=None,
                                    block_height=int(b_height) if b_height is not None else None,
                                )
                            except Exception:
                                pass
                    except Exception:
                        continue
        
        db.commit()
        spent_duration = _p_time.time() - t_spent
        if spent_duration > 1.0:
             print(f"[parse_transactions][PROFILE] Spent check: processed {len(spent_keys)} inputs, updated {total_updates} rows in {spent_duration:.2f}s"); sys.stdout.flush()
             
    db.commit()
