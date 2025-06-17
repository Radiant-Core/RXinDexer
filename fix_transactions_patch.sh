#!/bin/bash
# Add missing _insert_transactions method to block_parser.py

# First, check if file exists
if [ ! -f "/app/src/parser/block_parser.py" ]; then
    echo "Error: block_parser.py not found"
    exit 1
fi

# Use grep to find where the _insert_block method ends
line_num=$(grep -n "^    def _insert_block" /app/src/parser/block_parser.py | cut -d: -f1)
if [ -z "$line_num" ]; then
    echo "Error: Could not find _insert_block method"
    exit 1
fi

# Find where the next method starts after _insert_block
end_line=$(tail -n +$line_num /app/src/parser/block_parser.py | grep -n "^    def " | sed -n 2p | cut -d: -f1)
if [ -z "$end_line" ]; then
    echo "Error: Could not find the end of _insert_block method"
    exit 1
fi

# Calculate the insertion point
insert_line=$((line_num + end_line - 1))

# Create a temporary file with the new method
cat > /tmp/method.py << 'EOL'

def _insert_transactions(self, txs, height, block_hash):
    """
    Insert transactions into the database.
    Uses the current session transaction.
    
    Args:
        txs: Transaction data from Radiant Node
        height: Block height
        block_hash: Block hash
    """
    try:
        for tx_index, tx in enumerate(txs):
            # Get transaction hash
            tx_hash = tx.get("hash") or tx.get("txid")
            if not tx_hash:
                logger.warning(f"Transaction at index {tx_index} in block {height} has no hash")
                continue
                
            # Get transaction size and weight
            size = tx.get("size", 0)
            weight = tx.get("weight", 0)
            
            # Get lock time
            lock_time = tx.get("locktime", 0)
            
            # Get input and output counts
            vin = tx.get("vin", [])
            vout = tx.get("vout", [])
            input_count = len(vin)
            output_count = len(vout)
            
            # Insert the transaction
            logger.debug(f"Inserting transaction {tx_hash[:10]}...")
            
            self.db.execute(
                text("""
                INSERT INTO transactions (
                    hash, block_hash, block_height, index_in_block, timestamp,
                    size, weight, lock_time, input_count, output_count,
                    created_at, updated_at
                ) VALUES (
                    :hash, :block_hash, :height, :index, :timestamp,
                    :size, :weight, :lock_time, :input_count, :output_count,
                    NOW(), NOW()
                )
                ON CONFLICT (hash) DO UPDATE
                SET block_hash = EXCLUDED.block_hash,
                    block_height = EXCLUDED.block_height,
                    index_in_block = EXCLUDED.index_in_block,
                    timestamp = EXCLUDED.timestamp,
                    size = EXCLUDED.size,
                    weight = EXCLUDED.weight,
                    lock_time = EXCLUDED.lock_time,
                    input_count = EXCLUDED.input_count,
                    output_count = EXCLUDED.output_count,
                    updated_at = NOW()
                """),
                {
                    "hash": tx_hash,
                    "block_hash": block_hash,
                    "height": height,
                    "index": tx_index,
                    "timestamp": tx.get("time", 0),
                    "size": size,
                    "weight": weight,
                    "lock_time": lock_time,
                    "input_count": input_count,
                    "output_count": output_count
                }
            )
            
        # Force a flush to detect any errors early
        self.db.flush()
        logger.info(f"Inserted {len(txs)} transactions for block {height}")
        
    except Exception as e:
        logger.error(f"Error inserting transactions for block {height}: {str(e)}")
        raise

EOL

# Use sed to insert the new method at the calculated line
sed -i "${insert_line}r /tmp/method.py" /app/src/parser/block_parser.py

# Verify the insertion
if grep -q "_insert_transactions" /app/src/parser/block_parser.py; then
    echo "Successfully added _insert_transactions method"
    # Clean up
    rm /tmp/method.py
    exit 0
else
    echo "Failed to add _insert_transactions method"
    exit 1
fi
