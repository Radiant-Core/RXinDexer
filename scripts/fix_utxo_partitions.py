#!/usr/bin/env python3
"""
Standalone script to add script_type and script_hex columns to UTXO partitions.
This script runs independently of Alembic migrations to avoid transaction issues.
"""
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Database connection parameters - from environment or defaults
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "rxindexer")
DB_USER = os.environ.get("POSTGRES_USER", "rxindexer")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "rxindexer")

# Partition ranges to process
PARTITION_RANGES = [
    (0, 49999), (50000, 99999), (100000, 149999),
    (150000, 199999), (200000, 249999), (250000, 299999),
    (300000, 349999), (350000, 399999), (400000, 449999),
    (450000, 499999)
]

def main():
    """Main function to update all UTXO partitions"""
    print("Starting UTXO partition schema update...")
    
    # Connect to the database
    conn_string = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
    print(f"Connecting to database: {DB_HOST}:{DB_PORT}/{DB_NAME} as {DB_USER}")
    
    try:
        # Connect with autocommit mode
        conn = psycopg2.connect(conn_string)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Update the main table first
        print("Updating main UTXO table...")
        try:
            cursor.execute("ALTER TABLE utxos ADD COLUMN IF NOT EXISTS script_type VARCHAR")
            cursor.execute("ALTER TABLE utxos ADD COLUMN IF NOT EXISTS script_hex VARCHAR")
            cursor.execute("ALTER TABLE utxos ALTER COLUMN address DROP NOT NULL")
            print("Main UTXO table updated successfully")
        except Exception as e:
            print(f"Error updating main table: {e}")
        
        # Process each partition
        for start, end in PARTITION_RANGES:
            partition_name = f"utxos_{start}_{end}"
            print(f"\nProcessing partition {partition_name}...")
            
            # Check if partition exists
            try:
                cursor.execute(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{partition_name}')")
                exists = cursor.fetchone()[0]
                
                if not exists:
                    print(f"Partition {partition_name} does not exist, skipping")
                    continue
                
                # Update partition schema
                print(f"Adding columns to {partition_name}...")
                try:
                    cursor.execute(f"ALTER TABLE {partition_name} ADD COLUMN IF NOT EXISTS script_type VARCHAR")
                    print(f"Added script_type to {partition_name}")
                except Exception as e:
                    print(f"Error adding script_type to {partition_name}: {e}")
                
                try:
                    cursor.execute(f"ALTER TABLE {partition_name} ADD COLUMN IF NOT EXISTS script_hex VARCHAR")
                    print(f"Added script_hex to {partition_name}")
                except Exception as e:
                    print(f"Error adding script_hex to {partition_name}: {e}")
                
                try:
                    cursor.execute(f"ALTER TABLE {partition_name} ALTER COLUMN address DROP NOT NULL")
                    print(f"Made address nullable in {partition_name}")
                except Exception as e:
                    print(f"Error making address nullable in {partition_name}: {e}")
                
            except Exception as e:
                print(f"Error processing partition {partition_name}: {e}")
        
        # Update alembic_version table to mark migration as complete
        try:
            cursor.execute("SELECT version_num FROM alembic_version")
            current_version = cursor.fetchone()[0]
            print(f"\nCurrent Alembic version: {current_version}")
            
            if current_version == 'performance_indexes_001':
                cursor.execute("UPDATE alembic_version SET version_num = 'script_fields_migration'")
                print("Updated Alembic version to script_fields_migration")
            else:
                print(f"Alembic version is not 'performance_indexes_001', not updating")
        except Exception as e:
            print(f"Error updating Alembic version: {e}")
        
    except Exception as e:
        print(f"Database connection error: {e}")
        return
    finally:
        if 'conn' in locals() and conn:
            conn.close()
    
    print("\nUTXO partition schema update completed")

if __name__ == "__main__":
    main()
