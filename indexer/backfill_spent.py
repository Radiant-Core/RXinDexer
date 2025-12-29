import logging
import os
import time
import sys
import datetime
from sqlalchemy import text
from database.session import get_indexer_session
from database.models import BackfillStatus

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][BACKFILL] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

BACKFILL_TYPE = 'spent'

_LAST_UTXO_STATE_LOG_TS = 0.0

def get_or_create_backfill_status(db):
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
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    return bool(status and status.is_complete)

def backfill_spent_outputs(max_seconds=None, batch_size=None, sleep_seconds=None):
    """
    FAST backfill: Iterate through transaction_inputs and mark UTXOs as spent.
    This approach is much faster because:
    1. We iterate transaction_inputs (smaller, has index on spent_txid,spent_vout)
    2. We use direct index lookups instead of EXISTS subqueries
    3. We process in chunks by transaction_inputs.id (sequential, fast)
    """
    logger.info("Starting FAST chunked spent output backfill process...")
    
    if batch_size is None:
        batch_size = int(os.getenv("SPENT_BACKFILL_BATCH_SIZE", "10000"))
    if sleep_seconds is None:
        sleep_seconds = float(os.getenv("SPENT_BACKFILL_SLEEP_SECONDS", "0.1"))

    include_burns = os.getenv("SPENT_BACKFILL_INCLUDE_BURNS", "1").strip().lower() in {"1", "true", "yes", "y", "on"}
    statement_timeout_ms = None
    try:
        statement_timeout_ms = int(os.getenv("SPENT_BACKFILL_STATEMENT_TIMEOUT_MS", "60000") or 60000)
    except Exception:
        statement_timeout_ms = 60000

    burn_statement_timeout_ms = None
    try:
        burn_statement_timeout_ms = int(os.getenv("BURN_BACKFILL_STATEMENT_TIMEOUT_MS", "15000") or 15000)
    except Exception:
        burn_statement_timeout_ms = 15000

    total_updated = 0
    
    with get_indexer_session() as db:
        status = get_or_create_backfill_status(db)
        # IMPORTANT:
        # Do not treat "complete" as permanent. The indexer may continue ingesting
        # new blocks/transaction_inputs after a previous backfill run.
        # We are only truly complete when last_processed_id >= MAX(transaction_inputs.id).
        if status.is_complete:
            try:
                result = db.execute(text("SELECT MAX(id), MIN(id) FROM transaction_inputs"))
                row = result.fetchone()
                current_max_input_id = row[0] or 0

                last_done = int(status.last_processed_id or 0)
                if last_done >= current_max_input_id:
                    logger.info("Spent backfill already completed and caught up. Skipping.")
                    return True

                # New inputs were added after we previously marked complete.
                status.is_complete = False
                status.completed_at = None
                db.commit()
                logger.info(
                    f"Spent backfill was marked complete but new transaction_inputs exist "
                    f"(last_processed_id={last_done:,} < max_input_id={current_max_input_id:,}). Resuming."
                )
            except Exception as e:
                logger.warning(f"Could not validate completeness against transaction_inputs: {e}. Resuming backfill.")
                status.is_complete = False
                status.completed_at = None
                db.commit()

        if status.started_at is None:
            status.started_at = datetime.datetime.utcnow()
            db.commit()

        log_utxo_counts = os.getenv("LOG_UTXO_STATE_COUNTS", "0").strip().lower() in {"1", "true", "yes", "y", "on"}
        utxo_counts_interval_s = float(os.getenv("UTXO_STATE_COUNTS_LOG_INTERVAL_SECONDS", "3600"))
        global _LAST_UTXO_STATE_LOG_TS
        should_log_utxo_counts = log_utxo_counts and (time.time() - _LAST_UTXO_STATE_LOG_TS >= utxo_counts_interval_s)

        if should_log_utxo_counts:
            try:
                result = db.execute(text("""
                    SELECT 
                        COUNT(*) FILTER (WHERE spent = true) as already_spent,
                        COUNT(*) FILTER (WHERE spent = false) as unspent,
                        COUNT(*) as total
                    FROM utxos_initial
                """))
                row = result.fetchone()
                logger.info(f"UTXO state: {row.already_spent:,} already spent, {row.unspent:,} unspent, {row.total:,} total")
                _LAST_UTXO_STATE_LOG_TS = time.time()
            except Exception as e:
                logger.warning(f"Could not get UTXO counts: {e}")
        
        # Get max transaction_inputs id for progress tracking
        try:
            result = db.execute(text("SELECT MAX(id), MIN(id) FROM transaction_inputs"))
            row = result.fetchone()
            max_input_id = row[0] or 0
            min_input_id = row[1] or 0
            logger.info(f"Transaction inputs range: {min_input_id:,} to {max_input_id:,}")
            last_id = int(status.last_processed_id or 0)
            if last_id < (min_input_id - 1):
                last_id = min_input_id - 1
            # If last_processed_id overshot (e.g., due to batch increments), clamp it.
            if last_id > max_input_id:
                last_id = max_input_id
        except Exception as e:
            logger.warning(f"Could not get input range: {e}")
            max_input_id = 999999999
            min_input_id = 0
            last_id = int(status.last_processed_id or 0)

        if max_input_id <= 0:
            status.is_complete = True
            status.completed_at = datetime.datetime.utcnow()
            db.commit()
            logger.info("Backfill complete - no transaction inputs found.")
            return True

        budget_start_time = time.time()
        
        while True:
            if max_seconds is not None and (time.time() - budget_start_time) >= max_seconds:
                return False

            # FAST approach: iterate through transaction_inputs by ID range
            # and update UTXOs that match (spent_txid, spent_vout)
            stmt = text("""
                UPDATE utxos_initial u
                SET spent = true,
                    spent_in_txid = t.txid
                FROM transaction_inputs i
                JOIN transactions t ON t.id = i.transaction_id
                WHERE i.id > :last_id 
                  AND i.id <= :last_id + :batch_size
                  AND i.spent_txid IS NOT NULL
                  AND u.txid = i.spent_txid
                  AND u.vout = i.spent_vout
                  AND u.spent = false;
            """)
            
            try:
                batch_start_time = time.time()
                try:
                    if statement_timeout_ms and statement_timeout_ms > 0:
                        db.execute(text(f"SET LOCAL statement_timeout TO {int(statement_timeout_ms)}"))
                except Exception:
                    db.rollback()
                result = db.execute(stmt, {'last_id': last_id, 'batch_size': batch_size})
                updated_count = result.rowcount
                elapsed = time.time() - batch_start_time
                
                total_updated += updated_count
                last_id += batch_size
                progress_pct = min(100, (last_id - min_input_id) * 100 / (max_input_id - min_input_id + 1))

                status.last_processed_id = last_id
                status.total_processed = (status.total_processed or 0) + updated_count
                status.updated_at = datetime.datetime.utcnow()
                
                logger.info(f"Updated {updated_count} UTXOs in {elapsed:.1f}s. Total: {total_updated:,} | Progress: {progress_pct:.1f}%")

                if last_id >= max_input_id:
                    status.is_complete = True
                    status.completed_at = datetime.datetime.utcnow()
                    db.commit()

                    if include_burns:
                        try:
                            db.begin_nested()
                            try:
                                if burn_statement_timeout_ms and burn_statement_timeout_ms > 0:
                                    db.execute(text(f"SET LOCAL statement_timeout TO {int(burn_statement_timeout_ms)}"))
                            except Exception:
                                db.rollback()

                            db.execute(
                                text(
                                    """
                                    WITH tx_inputs AS (
                                        SELECT
                                            t.txid AS spending_txid,
                                            t.block_height AS block_height,
                                            u.glyph_ref AS token_id,
                                            SUM((u.value * 100000000)::bigint) AS amount
                                        FROM transaction_inputs i
                                        JOIN transactions t ON t.id = i.transaction_id
                                        JOIN utxos_initial u ON u.txid = i.spent_txid AND u.vout = i.spent_vout
                                        WHERE i.id > :start_id
                                          AND i.id <= :end_id
                                          AND i.spent_txid IS NOT NULL
                                          AND u.glyph_ref IS NOT NULL
                                          AND length(btrim(u.glyph_ref)) > 0
                                        GROUP BY t.txid, t.block_height, u.glyph_ref
                                    ),
                                    tx_outputs AS (
                                        SELECT o.txid AS spending_txid, o.glyph_ref AS token_id
                                        FROM utxos_initial o
                                        WHERE o.txid IN (SELECT DISTINCT spending_txid FROM tx_inputs)
                                          AND o.glyph_ref IS NOT NULL
                                          AND length(btrim(o.glyph_ref)) > 0
                                        GROUP BY o.txid, o.glyph_ref
                                    ),
                                    burns AS (
                                        SELECT
                                            i.token_id,
                                            i.spending_txid AS txid,
                                            i.block_height,
                                            i.amount
                                        FROM tx_inputs i
                                        LEFT JOIN tx_outputs o
                                          ON o.spending_txid = i.spending_txid
                                         AND o.token_id = i.token_id
                                        WHERE o.token_id IS NULL
                                          AND i.amount > 0
                                    ),
                                    ins AS (
                                        INSERT INTO token_burns (token_id, txid, amount, block_height)
                                        SELECT b.token_id, b.txid, b.amount, b.block_height
                                        FROM burns b
                                        LEFT JOIN token_burns existing
                                          ON existing.token_id = b.token_id
                                         AND existing.txid = b.txid
                                        WHERE existing.id IS NULL
                                        RETURNING token_id, amount
                                    )
                                    UPDATE glyph_tokens gt
                                    SET burned_supply = COALESCE(gt.burned_supply, 0) + ins.amount
                                    FROM ins
                                    WHERE gt.token_id = ins.token_id;
                                    """
                                ),
                                {
                                    'start_id': int(last_id - batch_size),
                                    'end_id': int(last_id),
                                },
                            )
                            db.commit()

                            try:
                                db.begin_nested()
                                try:
                                    if burn_statement_timeout_ms and burn_statement_timeout_ms > 0:
                                        db.execute(text(f"SET LOCAL statement_timeout TO {int(burn_statement_timeout_ms)}"))
                                except Exception:
                                    db.rollback()

                                db.execute(
                                    text(
                                        """
                                        WITH tx_inputs AS (
                                            SELECT
                                                t.txid AS spending_txid,
                                                u.glyph_ref AS token_id,
                                                SUM((u.value * 100000000)::bigint) AS amount
                                            FROM transaction_inputs i
                                            JOIN transactions t ON t.id = i.transaction_id
                                            JOIN utxos_initial u ON u.txid = i.spent_txid AND u.vout = i.spent_vout
                                            WHERE i.id > :start_id
                                              AND i.id <= :end_id
                                              AND i.spent_txid IS NOT NULL
                                              AND u.glyph_ref IS NOT NULL
                                              AND length(btrim(u.glyph_ref)) > 0
                                            GROUP BY t.txid, u.glyph_ref
                                        ),
                                        tx_outputs AS (
                                            SELECT o.txid AS spending_txid, o.glyph_ref AS token_id
                                            FROM utxos_initial o
                                            WHERE o.txid IN (SELECT DISTINCT spending_txid FROM tx_inputs)
                                              AND o.glyph_ref IS NOT NULL
                                              AND length(btrim(o.glyph_ref)) > 0
                                            GROUP BY o.txid, o.glyph_ref
                                        ),
                                        burns AS (
                                            SELECT i.token_id, SUM(i.amount) AS amount
                                            FROM tx_inputs i
                                            LEFT JOIN tx_outputs o
                                              ON o.spending_txid = i.spending_txid
                                             AND o.token_id = i.token_id
                                            WHERE o.token_id IS NULL
                                              AND i.amount > 0
                                            GROUP BY i.token_id
                                        )
                                        UPDATE glyphs g
                                        SET burned_supply = COALESCE(g.burned_supply, 0) + b.amount
                                        FROM burns b
                                        WHERE g.ref = b.token_id;
                                        """
                                    ),
                                    {
                                        'start_id': int(last_id - batch_size),
                                        'end_id': int(last_id),
                                    },
                                )
                                db.commit()
                            except Exception:
                                db.rollback()
                        except Exception:
                            db.rollback()

                    logger.info("Backfill complete - processed all transaction inputs.")
                    break

                db.commit()

                if include_burns:
                    try:
                        db.begin_nested()
                        try:
                            if burn_statement_timeout_ms and burn_statement_timeout_ms > 0:
                                db.execute(text(f"SET LOCAL statement_timeout TO {int(burn_statement_timeout_ms)}"))
                        except Exception:
                            db.rollback()

                        db.execute(
                            text(
                                """
                                WITH tx_inputs AS (
                                    SELECT
                                        t.txid AS spending_txid,
                                        t.block_height AS block_height,
                                        u.glyph_ref AS token_id,
                                        SUM((u.value * 100000000)::bigint) AS amount
                                    FROM transaction_inputs i
                                    JOIN transactions t ON t.id = i.transaction_id
                                    JOIN utxos_initial u ON u.txid = i.spent_txid AND u.vout = i.spent_vout
                                    WHERE i.id > :start_id
                                      AND i.id <= :end_id
                                      AND i.spent_txid IS NOT NULL
                                      AND u.glyph_ref IS NOT NULL
                                      AND length(btrim(u.glyph_ref)) > 0
                                    GROUP BY t.txid, t.block_height, u.glyph_ref
                                ),
                                tx_outputs AS (
                                    SELECT o.txid AS spending_txid, o.glyph_ref AS token_id
                                    FROM utxos_initial o
                                    WHERE o.txid IN (SELECT DISTINCT spending_txid FROM tx_inputs)
                                      AND o.glyph_ref IS NOT NULL
                                      AND length(btrim(o.glyph_ref)) > 0
                                    GROUP BY o.txid, o.glyph_ref
                                ),
                                burns AS (
                                    SELECT
                                        i.token_id,
                                        i.spending_txid AS txid,
                                        i.block_height,
                                        i.amount
                                    FROM tx_inputs i
                                    LEFT JOIN tx_outputs o
                                      ON o.spending_txid = i.spending_txid
                                     AND o.token_id = i.token_id
                                    WHERE o.token_id IS NULL
                                      AND i.amount > 0
                                ),
                                ins AS (
                                    INSERT INTO token_burns (token_id, txid, amount, block_height)
                                    SELECT b.token_id, b.txid, b.amount, b.block_height
                                    FROM burns b
                                    LEFT JOIN token_burns existing
                                      ON existing.token_id = b.token_id
                                     AND existing.txid = b.txid
                                    WHERE existing.id IS NULL
                                    RETURNING token_id, amount
                                )
                                UPDATE glyph_tokens gt
                                SET burned_supply = COALESCE(gt.burned_supply, 0) + ins.amount
                                FROM ins
                                WHERE gt.token_id = ins.token_id;
                                """
                            ),
                            {
                                'start_id': int(last_id - batch_size),
                                'end_id': int(last_id),
                            },
                        )
                        db.commit()

                        try:
                            db.begin_nested()
                            try:
                                if burn_statement_timeout_ms and burn_statement_timeout_ms > 0:
                                    db.execute(text(f"SET LOCAL statement_timeout TO {int(burn_statement_timeout_ms)}"))
                            except Exception:
                                db.rollback()

                            db.execute(
                                text(
                                    """
                                    WITH tx_inputs AS (
                                        SELECT
                                            t.txid AS spending_txid,
                                            u.glyph_ref AS token_id,
                                            SUM((u.value * 100000000)::bigint) AS amount
                                        FROM transaction_inputs i
                                        JOIN transactions t ON t.id = i.transaction_id
                                        JOIN utxos_initial u ON u.txid = i.spent_txid AND u.vout = i.spent_vout
                                        WHERE i.id > :start_id
                                          AND i.id <= :end_id
                                          AND i.spent_txid IS NOT NULL
                                          AND u.glyph_ref IS NOT NULL
                                          AND length(btrim(u.glyph_ref)) > 0
                                        GROUP BY t.txid, u.glyph_ref
                                    ),
                                    tx_outputs AS (
                                        SELECT o.txid AS spending_txid, o.glyph_ref AS token_id
                                        FROM utxos_initial o
                                        WHERE o.txid IN (SELECT DISTINCT spending_txid FROM tx_inputs)
                                          AND o.glyph_ref IS NOT NULL
                                          AND length(btrim(o.glyph_ref)) > 0
                                        GROUP BY o.txid, o.glyph_ref
                                    ),
                                    burns AS (
                                        SELECT i.token_id, SUM(i.amount) AS amount
                                        FROM tx_inputs i
                                        LEFT JOIN tx_outputs o
                                          ON o.spending_txid = i.spending_txid
                                         AND o.token_id = i.token_id
                                        WHERE o.token_id IS NULL
                                          AND i.amount > 0
                                        GROUP BY i.token_id
                                    )
                                    UPDATE glyphs g
                                    SET burned_supply = COALESCE(g.burned_supply, 0) + b.amount
                                    FROM burns b
                                    WHERE g.ref = b.token_id;
                                    """
                                ),
                                {
                                    'start_id': int(last_id - batch_size),
                                    'end_id': int(last_id),
                                },
                            )
                            db.commit()
                        except Exception:
                            db.rollback()
                    except Exception:
                        db.rollback()
                
                time.sleep(sleep_seconds)
            except Exception as e:
                logger.error(f"Error in backfill batch: {e}")
                db.rollback()
                time.sleep(5) # Wait before retrying
            
    logger.info(f"Backfill complete. Total UTXOs marked spent: {total_updated:,}")
    return True

if __name__ == "__main__":
    # Wait for DB to be ready
    time.sleep(5)
    backfill_spent_outputs()
