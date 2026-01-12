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
import json
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
                    # Fallback: if we know the reveal txid from glyphs.reveal_outpoint, use it
                    # directly to find the glyph-bearing input on that reveal transaction.
                    try:
                        if reveal_outpoint and ':' in str(reveal_outpoint):
                            r_txid = str(reveal_outpoint).split(':', 1)[0]
                            if r_txid:
                                row2 = db.execute(text("""
                                    SELECT ti.script_sig
                                    FROM transaction_inputs ti
                                    JOIN transactions t ON t.id = ti.transaction_id
                                    WHERE t.txid = :txid
                                      AND ti.script_sig IS NOT NULL
                                      AND ti.script_sig ILIKE '%676c79%'
                                    ORDER BY ti.id
                                    LIMIT 1
                                """), {'txid': r_txid}).fetchone()
                                if row2 and row2[0]:
                                    reveal_txid = r_txid
                                    script_sig = row2[0]
                    except Exception:
                        pass

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


def _is_backfill_complete(db, backfill_type: str) -> bool:
    db = None
    try:
        db = Session()
        row = db.execute(
            text("SELECT is_complete FROM backfill_status WHERE backfill_type = :t"),
            {'t': backfill_type},
        ).fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def backfill_ft_economics(batch_size: int = 200) -> int:
    from script_utils import decode_and_extract_glyph, parse_dmint_contract_script

    db = Session()
    updated = 0
    last_id = 0

    def _reverse_txid_hex(txid_hex: str) -> str:
        if not isinstance(txid_hex, str) or len(txid_hex) != 64:
            return txid_hex
        return ''.join([txid_hex[i:i+2] for i in range(0, 64, 2)][::-1])

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

    def _find_reveal_scriptsig(commit_txid: str, commit_vout: int):
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
            result = db.execute(text("""
                SELECT g.id, g.ref, g.reveal_outpoint
                FROM glyphs g
                WHERE g.id > :last_id
                  AND g.token_type = 'FT'
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

                if not isinstance(glyph_ref, str) or len(glyph_ref) < 64:
                    continue

                candidates = []
                candidate = glyph_ref[:64]
                candidates.append(candidate)
                candidates.append(_reverse_txid_hex(candidate))

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

                # Prefer the reveal txid we already know from glyphs.reveal_outpoint.
                # This is more reliable than trying to derive the commit outpoint from token_id.
                try:
                    if reveal_outpoint and ':' in str(reveal_outpoint):
                        r_txid = str(reveal_outpoint).split(':', 1)[0]
                        if r_txid:
                            row0 = db.execute(text("""
                                SELECT ti.script_sig
                                FROM transaction_inputs ti
                                JOIN transactions t ON t.id = ti.transaction_id
                                WHERE t.txid = :txid
                                  AND ti.script_sig IS NOT NULL
                                  AND ti.script_sig ILIKE '%676c79%'
                                ORDER BY ti.id
                                LIMIT 1
                            """), {'txid': r_txid}).fetchone()
                            if row0 and row0[0]:
                                reveal_txid = r_txid
                                script_sig = row0[0]
                except Exception:
                    pass

                if not script_sig:
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
                    # Fallback: use glyphs.reveal_outpoint txid directly and find the glyph-bearing input
                    # on that reveal transaction.
                    try:
                        if reveal_outpoint and ':' in str(reveal_outpoint):
                            r_txid = str(reveal_outpoint).split(':', 1)[0]
                            if r_txid:
                                row2 = db.execute(text("""
                                    SELECT ti.script_sig
                                    FROM transaction_inputs ti
                                    JOIN transactions t ON t.id = ti.transaction_id
                                    WHERE t.txid = :txid
                                      AND ti.script_sig IS NOT NULL
                                      AND ti.script_sig ILIKE '%676c79%'
                                    ORDER BY ti.id
                                    LIMIT 1
                                """), {'txid': r_txid}).fetchone()
                                if row2 and row2[0]:
                                    reveal_txid = r_txid
                                    script_sig = row2[0]
                    except Exception:
                        pass

                if not script_sig:
                    continue

                try:
                    meta = decode_and_extract_glyph(script_sig, txid=reveal_txid)
                except Exception:
                    meta = None

                if not isinstance(meta, dict):
                    continue

                max_supply = meta.get('max_supply')
                premine = meta.get('premine')
                difficulty = meta.get('difficulty')
                max_height = meta.get('max_height')
                reward = meta.get('reward')

                # For mineable tokens (FT + DMINT), these values are often encoded in the dMint contract script
                # (scriptPubKey), not in the reveal CBOR payload.
                protocols = []
                if isinstance(meta.get('protocols'), list):
                    protocols = meta.get('protocols')
                elif isinstance(meta.get('p'), list):
                    protocols = meta.get('p')

                protocol_ids = set()
                for p in (protocols or []):
                    try:
                        protocol_ids.add(int(p))
                    except Exception:
                        continue

                if 4 in protocol_ids and reveal_outpoint and ':' in str(reveal_outpoint):
                    try:
                        r_txid, r_vout = str(reveal_outpoint).split(':', 1)
                        r_vout = int(r_vout)
                        dmint = {}
                        matching_contracts = []
                        try:
                            contract_rows = db.execute(text("""
                                SELECT script_hex
                                FROM utxos
                                WHERE txid = :txid
                                  AND script_hex IS NOT NULL
                                  AND left(script_hex, 2) = '04'
                            """), {'txid': r_txid}).fetchall()
                            for (shex,) in contract_rows:
                                try:
                                    p = parse_dmint_contract_script(shex)
                                except Exception:
                                    continue
                                if isinstance(p, dict) and str(p.get('token_ref', '')).lower() == str(glyph_ref).lower():
                                    matching_contracts.append(p)
                                    if not dmint:
                                        dmint = p
                        except Exception:
                            matching_contracts = []

                        if not dmint:
                            utxo_row = db.execute(text("""
                                SELECT script_hex
                                FROM utxos
                                WHERE txid = :txid AND vout = :vout
                                LIMIT 1
                            """), {'txid': r_txid, 'vout': r_vout}).fetchone()
                            if utxo_row and utxo_row[0]:
                                dmint = parse_dmint_contract_script(utxo_row[0])
                                if isinstance(dmint, dict) and str(dmint.get('token_ref', '')).lower() == str(glyph_ref).lower():
                                    matching_contracts = [dmint]
                                else:
                                    dmint = {}

                        if isinstance(dmint, dict) and dmint.get('token_ref'):
                            # Only apply if this contract corresponds to this token_id
                            if str(dmint.get('token_ref')).lower() == str(glyph_ref).lower():
                                difficulty = dmint.get('difficulty') if difficulty is None else difficulty
                                max_height = dmint.get('max_height') if max_height is None else max_height
                                reward = dmint.get('reward') if reward is None else reward

                                # Count contracts in this reveal tx and infer premine from FT outputs
                                contract_count = int(len(matching_contracts)) if matching_contracts is not None else 0
                                premine_value = None

                                try:
                                    premine_row = db.execute(text("""
                                        SELECT value
                                        FROM utxos
                                        WHERE txid = :txid
                                          AND script_hex IS NOT NULL
                                          AND lower(script_hex) LIKE :needle
                                        ORDER BY value DESC
                                        LIMIT 1
                                    """), {'txid': r_txid, 'needle': f"%bdd0{str(glyph_ref).lower()}%"}).fetchone()
                                    if premine_row and premine_row[0] is not None:
                                        try:
                                            premine_value = int(premine_row[0])
                                        except Exception:
                                            premine_value = None
                                except Exception:
                                    premine_value = None

                                if premine is None and premine_value is not None:
                                    premine = premine_value

                                # Derive an approximate max supply for dmint tokens (used mainly for display).
                                # Photonic doesn't enforce a fixed max in script; cap is implied by max_height * reward * contracts.
                                try:
                                    if max_supply is None and reward is not None and max_height is not None and contract_count > 0:
                                        derived = int(contract_count) * int(max_height) * int(reward)
                                        if premine is not None:
                                            derived += int(premine)
                                        if -9223372036854775808 <= derived <= 9223372036854775807:
                                            max_supply = derived
                                except Exception:
                                    pass
                    except Exception:
                        pass

                try:
                    with db.begin_nested():
                        existing = db.execute(
                            text("SELECT 1 FROM glyph_tokens WHERE token_id = :token_id LIMIT 1"),
                            {'token_id': glyph_ref},
                        ).fetchone()

                        if existing:
                            db.execute(
                                text("""
                                    UPDATE glyph_tokens SET
                                        name = COALESCE(:name, name),
                                        ticker = COALESCE(:ticker, ticker),
                                        description = COALESCE(:description, description),
                                        protocols = COALESCE(CAST(:protocols AS json), protocols),
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
                                        updated_at = NOW(),
                                        supply_updated_at = COALESCE(supply_updated_at, NOW())
                                    WHERE token_id = :token_id
                                """),
                                {
                                    'token_id': glyph_ref,
                                    'name': meta.get('name'),
                                    'ticker': meta.get('ticker') or ((meta.get('name') or '')[:10] or None),
                                    'description': meta.get('description'),
                                    'protocols': json.dumps(protocols) if protocols else None,
                                    'protocol_type': meta.get('protocol_type'),
                                    'token_type_name': meta.get('token_type_name'),
                                    'immutable': meta.get('immutable'),
                                    'license': meta.get('license'),
                                    'attrs': json.dumps(meta.get('attrs')) if meta.get('attrs') else None,
                                    'location': meta.get('location'),
                                    'author': meta.get('author'),
                                    'container': meta.get('container'),
                                    'max_supply': max_supply,
                                    'premine': premine,
                                    'difficulty': difficulty,
                                    'max_height': max_height,
                                    'reward': reward,
                                    'icon_mime_type': meta.get('icon_mime_type'),
                                    'icon_url': meta.get('icon_url'),
                                    'icon_data': meta.get('icon_data'),
                                    'reveal_txid': reveal_txid,
                                },
                            )
                        else:
                            txid_for_row = reveal_txid or (str(reveal_outpoint).split(':')[0] if reveal_outpoint and ':' in str(reveal_outpoint) else None) or used_commit_txid
                            if not txid_for_row:
                                continue
                            db.execute(
                                text("""
                                    INSERT INTO glyph_tokens (
                                        token_id, txid, type,
                                        name, ticker, description,
                                        protocols, protocol_type, token_type_name, immutable, license, attrs,
                                        location, author, container,
                                        max_supply, premine, difficulty, max_height, reward,
                                        icon_mime_type, icon_url, icon_data, reveal_txid,
                                        created_at, updated_at, supply_updated_at
                                    ) VALUES (
                                        :token_id, :txid, :type,
                                        :name, :ticker, :description,
                                        CAST(:protocols AS json), :protocol_type, :token_type_name, :immutable, :license, CAST(:attrs AS json),
                                        :location, :author, :container,
                                        :max_supply, :premine, :difficulty, :max_height, :reward,
                                        :icon_mime_type, :icon_url, :icon_data, :reveal_txid,
                                        NOW(), NOW(), NOW()
                                    )
                                """),
                                {
                                    'token_id': glyph_ref,
                                    'txid': txid_for_row,
                                    'type': 'ft',
                                    'name': meta.get('name'),
                                    'ticker': meta.get('ticker') or ((meta.get('name') or '')[:10] or None),
                                    'description': meta.get('description'),
                                    'protocols': json.dumps(protocols) if protocols else None,
                                    'protocol_type': meta.get('protocol_type'),
                                    'token_type_name': meta.get('token_type_name'),
                                    'immutable': meta.get('immutable'),
                                    'license': meta.get('license'),
                                    'attrs': json.dumps(meta.get('attrs')) if meta.get('attrs') else None,
                                    'location': meta.get('location'),
                                    'author': meta.get('author'),
                                    'container': meta.get('container'),
                                    'max_supply': max_supply,
                                    'premine': premine,
                                    'difficulty': difficulty,
                                    'max_height': max_height,
                                    'reward': reward,
                                    'icon_mime_type': meta.get('icon_mime_type'),
                                    'icon_url': meta.get('icon_url'),
                                    'icon_data': meta.get('icon_data'),
                                    'reveal_txid': reveal_txid,
                                },
                            )

                    updated += 1
                except Exception as e:
                    logger.debug(f"FT economics backfill failed for {glyph_ref}: {e}")
                    continue

            db.commit()

        db.commit()
        logger.info(f"FT economics backfill complete. Updated {updated} tokens.")
        return updated
    except Exception as e:
        logger.error(f"Error in FT economics backfill: {e}")
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
        db.execute(text("TRUNCATE token_holders"))
        db.execute(text("""
            INSERT INTO token_holders (token_id, address, balance, first_acquired_at, last_updated_at)
            SELECT
                u.glyph_ref AS token_id,
                u.address AS address,
                SUM(u.value) AS balance,
                NOW() AS first_acquired_at,
                NOW() AS last_updated_at
            FROM utxos u
            WHERE u.spent = false
              AND u.contract_type = 'FT'
              AND u.glyph_ref IS NOT NULL
              AND u.address IS NOT NULL
              AND NULLIF(u.address, '') IS NOT NULL
            GROUP BY u.glyph_ref, u.address
        """))

        updated = db.execute(text("SELECT COUNT(*) FROM token_holders")).scalar() or 0
        logger.info(f"Inserted {updated} token holder rows.")
        db.execute(text("""
            UPDATE glyph_tokens
            SET
                holder_count = 0,
                circulating_supply = 0,
                supply_updated_at = NOW()
        """))
        db.execute(text("""
            UPDATE glyph_tokens gt
            SET
                holder_count = h.holder_count,
                circulating_supply = h.circulating_supply,
                supply_updated_at = NOW()
            FROM (
                SELECT token_id, COUNT(*) AS holder_count, SUM(balance) AS circulating_supply
                FROM token_holders
                GROUP BY token_id
            ) h
            WHERE gt.token_id = h.token_id
        """))
        logger.info("Calculating holder percentages...")
        db.execute(text("""
            UPDATE token_holders th
            SET percentage = (th.balance::float / h.circulating_supply) * 100,
                last_updated_at = NOW()
            FROM (
                SELECT token_id, SUM(balance) AS circulating_supply
                FROM token_holders
                GROUP BY token_id
            ) h
            WHERE th.token_id = h.token_id
              AND h.circulating_supply > 0
        """))

        db.commit()
        logger.info(f"Holder balance backfill complete. Inserted {updated} holder rows.")
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
        if _is_backfill_complete(db=None, backfill_type='token_metadata'):
            logger.info("Token metadata backfill already complete; skipping.")
        else:
            metadata_count = backfill_token_metadata()
            update_backfill_status('token_metadata', True, metadata_count)
    except Exception as e:
        logger.error(f"Metadata backfill failed: {e}")
        update_backfill_status('token_metadata', False, 0)

    # 2. NFT metadata backfill
    logger.info("\n[2/4] NFT Metadata Backfill")
    try:
        if _is_backfill_complete(db=None, backfill_type='nft_metadata'):
            logger.info("NFT metadata backfill already complete; skipping.")
        else:
            nft_meta_count = backfill_nft_metadata()
            update_backfill_status('nft_metadata', True, nft_meta_count)
    except Exception as e:
        logger.error(f"NFT metadata backfill failed: {e}")
        update_backfill_status('nft_metadata', False, 0)
    
    # 3. Holder balances
    logger.info("\n[3/4] Holder Balance Backfill")
    try:
        if _is_backfill_complete(db=None, backfill_type='token_holders'):
            logger.info("Holder backfill already complete; skipping.")
        else:
            holder_count = backfill_holder_balances()
            update_backfill_status('token_holders', True, holder_count)
    except Exception as e:
        logger.error(f"Holder backfill failed: {e}")
        update_backfill_status('token_holders', False, 0)
    
    # 4. Author resolution
    logger.info("\n[4/4] Author Resolution Backfill")
    try:
        if _is_backfill_complete(db=None, backfill_type='author_resolution'):
            logger.info("Author resolution already complete; skipping.")
        else:
            author_count = backfill_author_resolution()
            update_backfill_status('author_resolution', True, author_count)
    except Exception as e:
        logger.error(f"Author resolution failed: {e}")
        update_backfill_status('author_resolution', False, 0)

    # 5. FT economics
    logger.info("\n[5/5] FT Economics Backfill")
    try:
        if _is_backfill_complete(db=None, backfill_type='ft_economics'):
            logger.info("FT economics backfill already complete; skipping.")
        else:
            ft_count = backfill_ft_economics()
            update_backfill_status('ft_economics', True, ft_count)
    except Exception as e:
        logger.error(f"FT economics backfill failed: {e}")
        update_backfill_status('ft_economics', False, 0)
    
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
    parser.add_argument('--ft-economics', action='store_true', help='Run FT economics backfill only')
    parser.add_argument('--all', action='store_true', help='Run all backfills (default)')
    parser.add_argument('--batch-size', type=int, default=100, help='Batch size for processing')
    
    args = parser.parse_args()
    
    if args.metadata:
        backfill_token_metadata(args.batch_size)
    elif args.holders:
        backfill_holder_balances(args.batch_size)
    elif args.authors:
        backfill_author_resolution(args.batch_size)
    elif args.ft_economics:
        backfill_ft_economics(args.batch_size)
    else:
        run_full_backfill()
