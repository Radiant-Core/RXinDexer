"""
Fast bulk insert using PostgreSQL COPY command.
This is 5-10x faster than SQLAlchemy bulk_save_objects for large datasets.
"""
import io
import csv
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam


def copy_transactions(db: Session, transactions: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Bulk insert transactions using COPY and return txid -> id mapping.
    
    Args:
        db: SQLAlchemy session
        transactions: List of dicts with keys: txid, block_id, block_height, version, locktime, created_at
    
    Returns:
        Dict mapping txid to database id
    """
    if not transactions:
        return {}
    
    # Get raw connection for COPY
    connection = db.connection().connection
    cursor = connection.cursor()
    
    # Create CSV buffer
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
    
    for tx in transactions:
        writer.writerow([
            tx['txid'],
            tx.get('version', 1),
            tx.get('locktime', 0),
            tx.get('block_id') or '\\N',  # NULL handling
            tx['block_height'],
            tx['created_at'].isoformat() if tx.get('created_at') else 'now()'
        ])
    
    buffer.seek(0)
    
    # Use COPY to insert
    cursor.copy_expert(
        """COPY transactions (txid, version, locktime, block_id, block_height, created_at) 
           FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')""",
        buffer
    )
    
    # Fetch the IDs for the inserted transactions
    txids = [tx['txid'] for tx in transactions]
    
    # Get unique block heights for efficient querying
    block_heights = list(set(tx['block_height'] for tx in transactions))
    
    # Query back the IDs - handle multiple block heights
    if len(block_heights) == 1:
        # Single block - use partition-efficient query
        stmt = text(
            """SELECT txid, id FROM transactions 
                    WHERE block_height = :height AND txid IN :txids"""
        ).bindparams(bindparam("txids", expanding=True))
        result = db.execute(stmt, {'height': block_heights[0], 'txids': txids})
    else:
        # Multiple blocks - use range query
        min_height = min(block_heights)
        max_height = max(block_heights)
        stmt = text(
            """SELECT txid, id FROM transactions 
                    WHERE block_height BETWEEN :min_height AND :max_height 
                    AND txid IN :txids"""
        ).bindparams(bindparam("txids", expanding=True))
        result = db.execute(stmt, {'min_height': min_height, 'max_height': max_height, 'txids': txids})

    mapping = {row.txid: row.id for row in result}
    if len(mapping) != len(txids):
        missing = len(set(txids) - set(mapping.keys()))
        raise RuntimeError(f"Failed to resolve {missing} transaction ids after COPY")
    return mapping


def copy_utxos(db: Session, utxos: List[Dict[str, Any]]) -> int:
    """
    Bulk insert UTXOs using COPY via temp table + INSERT ON CONFLICT.
    
    Args:
        db: SQLAlchemy session
        utxos: List of dicts with keys: txid, vout, address, value, transaction_id, 
               transaction_block_height, script_type, script_hex, is_glyph_reveal,
               glyph_ref, contract_type
    
    Returns:
        Number of rows inserted
    """
    if not utxos:
        return 0
    
    # Get raw connection for COPY
    connection = db.connection().connection
    cursor = connection.cursor()
    
    # Create temp table for staging (avoids unique constraint errors during COPY)
    cursor.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _utxos_staging (
            txid VARCHAR(64),
            vout INTEGER,
            address VARCHAR(128),
            value NUMERIC(20, 8),
            spent BOOLEAN,
            spent_in_txid VARCHAR(64),
            transaction_id INTEGER,
            transaction_block_height INTEGER,
            script_type VARCHAR(32),
            script_hex TEXT,
            is_glyph_reveal BOOLEAN,
            glyph_ref VARCHAR(72),
            contract_type VARCHAR(20)
        ) ON COMMIT DROP
    """)
    cursor.execute("TRUNCATE _utxos_staging")
    
    # Create CSV buffer
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
    
    for utxo in utxos:
        # Handle None values as \N for PostgreSQL NULL
        address = utxo.get('address') or '\\N'
        transaction_id = utxo.get('transaction_id')
        transaction_id_str = str(transaction_id) if transaction_id is not None else '\\N'
        script_type = utxo.get('script_type') or '\\N'
        script_hex = utxo.get('script_hex') or '\\N'
        is_glyph_reveal = 't' if utxo.get('is_glyph_reveal') else 'f'
        glyph_ref = utxo.get('glyph_ref') or '\\N'
        contract_type = utxo.get('contract_type') or '\\N'
        
        writer.writerow([
            utxo['txid'],
            utxo['vout'],
            address,
            str(utxo['value']),
            'f',  # spent = false
            '\\N',  # spent_in_txid = NULL
            transaction_id_str,
            utxo['transaction_block_height'],
            script_type,
            script_hex,
            is_glyph_reveal,
            glyph_ref,
            contract_type
        ])
    
    buffer.seek(0)
    
    # COPY into staging table
    cursor.copy_expert(
        """COPY _utxos_staging (txid, vout, address, value, spent, spent_in_txid, 
                       transaction_id, transaction_block_height, script_type, script_hex,
                       is_glyph_reveal, glyph_ref, contract_type) 
           FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')""",
        buffer
    )
    
    # Insert from staging with ON CONFLICT DO NOTHING (prevents duplicates)
    cursor.execute("""
        INSERT INTO utxos (txid, vout, address, value, spent, spent_in_txid,
                          transaction_id, transaction_block_height, script_type, script_hex,
                          is_glyph_reveal, glyph_ref, contract_type)
        SELECT txid, vout, address, value, spent, spent_in_txid,
               transaction_id, transaction_block_height, script_type, script_hex,
               is_glyph_reveal, glyph_ref, contract_type
        FROM _utxos_staging
        ON CONFLICT (txid, vout, transaction_block_height) DO NOTHING
    """)
    
    inserted = cursor.rowcount
    return inserted


def copy_transaction_inputs(db: Session, inputs: List[Dict[str, Any]]) -> int:
    """
    Bulk insert transaction inputs using COPY via temp table + INSERT ON CONFLICT.
    
    Args:
        db: SQLAlchemy session
        inputs: List of dicts with keys: transaction_id, input_index, spent_txid, 
                spent_vout, script_sig, coinbase, sequence
    
    Returns:
        Number of rows inserted
    """
    if not inputs:
        return 0
    
    # Get raw connection for COPY
    connection = db.connection().connection
    cursor = connection.cursor()
    
    # Create temp table for staging (avoids unique constraint errors during COPY)
    cursor.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _inputs_staging (
            transaction_id INTEGER,
            input_index INTEGER,
            spent_txid VARCHAR(64),
            spent_vout INTEGER,
            script_sig TEXT,
            coinbase TEXT,
            sequence BIGINT
        ) ON COMMIT DROP
    """)
    cursor.execute("TRUNCATE _inputs_staging")
    
    # Create CSV buffer
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
    
    for inp in inputs:
        transaction_id = inp.get('transaction_id')
        if transaction_id is None:
            continue  # Skip inputs without transaction_id
            
        spent_txid = inp.get('spent_txid') or '\\N'
        spent_vout = inp.get('spent_vout')
        spent_vout_str = str(spent_vout) if spent_vout is not None else '\\N'
        script_sig = inp.get('script_sig') or '\\N'
        coinbase = inp.get('coinbase') or '\\N'
        sequence = inp.get('sequence', 4294967295)  # Default max sequence
        
        writer.writerow([
            transaction_id,
            inp['input_index'],
            spent_txid,
            spent_vout_str,
            script_sig,
            coinbase,
            sequence
        ])
    
    buffer.seek(0)
    
    # COPY into staging table
    cursor.copy_expert(
        """COPY _inputs_staging (transaction_id, input_index, spent_txid, 
                                    spent_vout, script_sig, coinbase, sequence) 
           FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')""",
        buffer
    )
    
    # Insert from staging with ON CONFLICT DO NOTHING (prevents duplicates)
    cursor.execute("""
        INSERT INTO transaction_inputs (transaction_id, input_index, spent_txid,
                                        spent_vout, script_sig, coinbase, sequence)
        SELECT transaction_id, input_index, spent_txid, spent_vout, script_sig, coinbase, sequence
        FROM _inputs_staging
        ON CONFLICT (transaction_id, input_index) DO NOTHING
    """)
    
    inserted = cursor.rowcount
    return inserted
