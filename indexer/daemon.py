import sys
print("=== RXINDEXER DAEMON START ==="); sys.stdout.flush()
import os
import logging
import time
import threading
from database.session import SessionLocal
from indexer.sync import sync_blocks
from indexer.parser import parse_transactions

from indexer.monitor import get_sync_lag, start_monitoring_thread, periodic_status_check
from database.maintenance import restore_heavy_indices

logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# Start automated monitoring thread (every 5 minutes)
start_monitoring_thread(interval=300)
# Start periodic status checks every 30 minutes
periodic_status_check(interval=1800)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _start_backfill_worker_once():
    if getattr(_start_backfill_worker_once, "_started", False):
        return
    _start_backfill_worker_once._started = True

    enabled = _env_bool("ENABLE_AUTOMATED_BACKFILLS", True)
    if not enabled:
        print("[daemon] Automated backfills disabled (ENABLE_AUTOMATED_BACKFILLS=0)")
        return

    max_lag = _env_int("BACKFILL_MAX_SYNC_LAG", 10)
    spent_budget_s = _env_float("SPENT_BACKFILL_TIME_BUDGET_SECONDS", 30.0)
    loop_sleep_s = _env_float("BACKFILL_LOOP_SLEEP_SECONDS", 60.0)
    spent_sleep_s = _env_float("SPENT_BACKFILL_SLEEP_SECONDS", 0.1)
    spent_batch_size = _env_int("SPENT_BACKFILL_BATCH_SIZE", 10000)

    spent_budget_near_sync_s = _env_float("SPENT_BACKFILL_TIME_BUDGET_SECONDS_NEAR_SYNC", max(60.0, spent_budget_s))
    loop_sleep_near_sync_s = _env_float("BACKFILL_LOOP_SLEEP_SECONDS_NEAR_SYNC", min(30.0, loop_sleep_s))
    spent_sleep_near_sync_s = _env_float("SPENT_BACKFILL_SLEEP_SECONDS_NEAR_SYNC", min(0.05, spent_sleep_s))
    spent_batch_size_near_sync = _env_int("SPENT_BACKFILL_BATCH_SIZE_NEAR_SYNC", max(20000, spent_batch_size))

    spent_budget_caught_up_s = _env_float("SPENT_BACKFILL_TIME_BUDGET_SECONDS_CAUGHT_UP", max(120.0, spent_budget_s))
    loop_sleep_caught_up_s = _env_float("BACKFILL_LOOP_SLEEP_SECONDS_CAUGHT_UP", min(10.0, loop_sleep_s))
    spent_sleep_caught_up_s = _env_float("SPENT_BACKFILL_SLEEP_SECONDS_CAUGHT_UP", 0.0)
    spent_batch_size_caught_up = _env_int("SPENT_BACKFILL_BATCH_SIZE_CAUGHT_UP", max(50000, spent_batch_size))

    def worker():
        print(
            f"[daemon] Backfill worker started (max_lag={max_lag} | "
            f"base: budget={spent_budget_s}s, loop_sleep={loop_sleep_s}s, batch={spent_batch_size}, per_batch_sleep={spent_sleep_s}s | "
            f"near_sync: budget={spent_budget_near_sync_s}s, loop_sleep={loop_sleep_near_sync_s}s, batch={spent_batch_size_near_sync}, per_batch_sleep={spent_sleep_near_sync_s}s | "
            f"caught_up: budget={spent_budget_caught_up_s}s, loop_sleep={loop_sleep_caught_up_s}s, batch={spent_batch_size_caught_up}, per_batch_sleep={spent_sleep_caught_up_s}s)"
        )
        sys.stdout.flush()

        spent_done = False
        tokens_done = False
        token_files_done = False
        token_data_done = False

        while True:
            try:
                lag = 1000000
                try:
                    lag_info = get_sync_lag()
                    if isinstance(lag_info, dict) and 'lag' in lag_info:
                        lag = lag_info['lag']
                except Exception:
                    lag = 1000000

                if lag <= max_lag:
                    if not spent_done:
                        from indexer.backfill_spent import backfill_spent_outputs
                        effective_budget_s = spent_budget_s
                        effective_spent_sleep_s = spent_sleep_s
                        effective_batch_size = spent_batch_size

                        if lag == 0:
                            effective_budget_s = spent_budget_caught_up_s
                            effective_spent_sleep_s = spent_sleep_caught_up_s
                            effective_batch_size = spent_batch_size_caught_up
                        elif lag <= 2:
                            effective_budget_s = spent_budget_near_sync_s
                            effective_spent_sleep_s = spent_sleep_near_sync_s
                            effective_batch_size = spent_batch_size_near_sync

                        completed = backfill_spent_outputs(
                            max_seconds=effective_budget_s,
                            batch_size=effective_batch_size,
                            sleep_seconds=effective_spent_sleep_s,
                        )
                        if completed:
                            spent_done = True
                            print("[daemon] Spent backfill completed (or already complete).")
                            sys.stdout.flush()

                    elif not tokens_done:
                        from indexer.backfill_tokens import run_if_needed as run_token_backfill
                        run_token_backfill()
                        tokens_done = True
                        print("[daemon] Token backfill completed (or already complete).")
                        sys.stdout.flush()

                    elif not token_files_done:
                        from indexer.backfill_token_files import run_if_needed as run_token_files_backfill
                        run_token_files_backfill()
                        token_files_done = True
                        print("[daemon] Token files backfill completed (or already complete).")
                        sys.stdout.flush()

                    elif not token_data_done:
                        from indexer.backfill_token_data import run_full_backfill
                        run_full_backfill()
                        token_data_done = True
                        print("[daemon] Token data backfill completed.")
                        sys.stdout.flush()
                    else:
                        return

            except Exception as e:
                print(f"[daemon][ERROR] Backfill worker error: {e}")
                sys.stdout.flush()

            try:
                sleep_s = loop_sleep_s
                if lag == 0:
                    sleep_s = loop_sleep_caught_up_s
                elif lag <= 2:
                    sleep_s = loop_sleep_near_sync_s
                time.sleep(sleep_s)
            except Exception:
                time.sleep(loop_sleep_s)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

def run_daemon():
    indices_checked = False
    
    while True:
        db = SessionLocal()
        try:
            sync_blocks(db, parse_tx_callback=parse_transactions)
        except Exception as e:
            print(f"Sync error: {e}")
        finally:
            db.close()
        
        # Adaptive sleep based on sync lag
        lag = 1000000 # Default to high lag to prevent premature index restoration
        try:
            lag_info = get_sync_lag()
            if isinstance(lag_info, dict) and 'lag' in lag_info:
                lag = lag_info['lag']
        except Exception as e:
            print(f"Error getting sync lag: {e}")
        
        # AUTOMATION: Restore heavy indices if we are caught up
        if lag < 5 and not indices_checked:
            print("[daemon] Catch-up complete (lag < 5). Attempting to restore heavy indices...")
            if restore_heavy_indices():
                print("[daemon] Heavy indices restored successfully.")

                _start_backfill_worker_once()
                indices_checked = True
        
        # Optimized sleep logic for catch-up performance
        if lag > 50000:  # Critical lag - no sleep, continuous sync
            sleep_time = 0
        elif lag > 10000:  # High lag - minimal sleep
            sleep_time = 0.5
        elif lag > 1000:  # Moderate lag - short sleep
            sleep_time = 1
        elif lag > 100:  # Low lag - normal sleep
            sleep_time = 5
        elif lag > 10:  # Very low lag - longer sleep
            sleep_time = 10
        else:  # Fully synced - standard sleep
            sleep_time = _env_int("SYNC_POLL_INTERVAL_CAUGHT_UP", 30)
        
        if sleep_time > 0:
            print(f"[daemon] Sync lag: {lag}. Sleeping {sleep_time}s before next sync."); sys.stdout.flush()
            time.sleep(sleep_time)
        else:
            print(f"[daemon] Critical sync lag: {lag}. Continuous sync mode - no sleep."); sys.stdout.flush()

if __name__ == "__main__":
    run_daemon()
