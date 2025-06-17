#!/bin/bash
# Fix database connectivity in the container
echo "Fixing database connectivity in indexer container..."

# Ensure DATABASE_URL is correct
export DATABASE_URL="postgresql://postgres:postgres@db:5432/rxindexer"

# Diagnose by attempting direct insert to database
python3 -c "
import os
import sys
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker, Session

print('Testing direct database access...')
db_url = os.environ.get('DATABASE_URL')
print(f'Using database URL: {db_url}')

engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Test direct connection and write
try:
    with engine.begin() as conn:
        # Check connection
        result = conn.execute(text('SELECT 1')).scalar()
        print(f'Database connection test: {result == 1}')
        
        # Check tables
        tables = conn.execute(text('''
            SELECT tablename FROM pg_catalog.pg_tables 
            WHERE schemaname = 'public'
        ''')).fetchall()
        print(f'Available tables: {[t[0] for t in tables]}')
        
        # Count blocks
        block_count = conn.execute(text('SELECT COUNT(*) FROM blocks')).scalar()
        print(f'Current block count: {block_count}')
        
        # Test insert a block
        test_hash = 'test_direct_insert'
        conn.execute(text('''
        INSERT INTO blocks (
            hash, height, prev_hash, merkle_root, timestamp, nonce,
            bits, version, size, weight, tx_count, created_at, updated_at
        ) VALUES (
            :hash, 999998, 'test_direct', 'test_direct', 
            extract(epoch from now()), 12345, '1d00ffff', 1, 1000, 4000, 1,
            NOW(), NOW()
        )
        ON CONFLICT (hash) DO NOTHING
        '''), {'hash': test_hash})
        
        # Verify insert
        verify = conn.execute(text('SELECT COUNT(*) FROM blocks WHERE hash = :hash'), 
                             {'hash': test_hash}).scalar()
        print(f'Verify insert success: {verify == 1}')
        
        print('Database direct insert test completed successfully')
        
except Exception as e:
    print(f'Database error: {str(e)}')
    sys.exit(1)
"

echo "Database connectivity test completed"
