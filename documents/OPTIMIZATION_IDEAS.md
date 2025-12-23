# Optimization Ideas for RXinDexer

Reference document for potential future optimizations. Not implemented — saved for if re-sync or performance tuning is needed.

---

## Spent Check Optimization

**Current Issue:** As UTXO table grows (100M+ rows), spent check queries slow down from 1-2s to 27s+ per batch.

### Idea 1: Composite Index on (txid, vout, spent)
```sql
CREATE INDEX ix_utxos_txid_vout_spent ON utxos_initial (txid, vout) WHERE spent = false;
```
- Partial index only on unspent UTXOs
- Dramatically reduces scan size as most UTXOs become spent

### Idea 2: Batch by Block Height Range
Instead of querying all unspent UTXOs, limit search to UTXOs created before the current block:
```python
candidates = db.query(UTXO.txid, UTXO.vout, UTXO.transaction_block_height).filter(
    UTXO.txid.in_(chunk_txids),
    UTXO.spent == False,
    UTXO.transaction_block_height < current_block_height  # Can't spend future UTXOs
).all()
```

### Idea 3: In-Memory Spent Tracking During Bulk Sync
- Maintain a Python set of `(txid, vout)` for UTXOs created in current sync session
- Check set first before hitting DB
- Only query DB for UTXOs from previous sync sessions

### Idea 4: Deferred Spent Updates with Temp Table
```sql
-- During sync, just log spent events to temp table
INSERT INTO spent_log (txid, vout, spent_in_txid) VALUES ...

-- After sync batch, bulk update
UPDATE utxos_initial u
SET spent = true, spent_in_txid = s.spent_in_txid
FROM spent_log s
WHERE u.txid = s.txid AND u.vout = s.vout;

TRUNCATE spent_log;
```

### Idea 5: Skip Spent Check During Catchup, Backfill After
(Already implemented as fallback option)
- Disable inline spent marking when lag > 1000
- Run backfill process after reaching tip
- Trade-off: Faster sync but spent status delayed

---

## General Sync Optimizations

### Reduce Partition Manager Logging
The partition manager logs on every batch. Could reduce to only log when actually creating partitions.

### Parallel Transaction Parsing
Currently sequential. Could use ThreadPoolExecutor for parsing multiple blocks' transactions in parallel (careful with DB session handling).

### Connection Pooling Tuning
```python
# In database/session.py
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)
```

---

## PostgreSQL Tuning (Already Partially Applied)

Current settings in docker-compose for SSD:
- `shared_buffers=4GB`
- `max_wal_size=4GB`
- `synchronous_commit=off`
- `max_parallel_workers=8`

### Additional Ideas:
```
effective_cache_size = 12GB  # If system has 16GB+ RAM
random_page_cost = 1.1       # SSD optimization (default 4.0)
effective_io_concurrency = 200  # SSD can handle parallel I/O
```

---

## When to Apply

- **Re-sync needed**: Apply Ideas 1-3 before starting
- **Live performance issues**: Apply PostgreSQL tuning
- **Frequent re-indexing**: Consider Idea 5 (hybrid approach)

Last updated: Dec 2025
