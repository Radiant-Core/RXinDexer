#!/usr/bin/env python3
# Script to check if any glyph tokens exist in the database using psycopg2

import os
import sys
import psycopg2

# Get database connection parameters from environment or use defaults
POSTGRES_DB = os.getenv("POSTGRES_DB", "rxindexer")
POSTGRES_USER = os.getenv("POSTGRES_USER", "rxindexer")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "rxindexerpass")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "db")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

def check_glyph_tokens():
    """Check if any glyph tokens exist in the database and print details"""
    print("Checking for glyph tokens in database...")
    print(f"Attempting to connect to database at: {POSTGRES_HOST}:{POSTGRES_PORT}")
    
    conn = None
    try:
        # Connect directly with psycopg2
        conn = psycopg2.connect(
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT
        )
        
        # Create a cursor
        cur = conn.cursor()
        
        # Check if table exists
        cur.execute("SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'glyph_tokens')")
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            print("The glyph_tokens table does not exist in the database!")
            return
            
        # Count tokens
        cur.execute("SELECT COUNT(*) FROM glyph_tokens")
        count = cur.fetchone()[0]
        print(f"Total glyph tokens found: {count}")
        
        # Get sample tokens
        if count > 0:
            cur.execute("SELECT * FROM glyph_tokens LIMIT 5")
            tokens = cur.fetchall()
            
            # Get column names
            col_names = [desc[0] for desc in cur.description]
            
            print("\nSample of glyph tokens:")
            for token in tokens:
                print("Token data:")
                for i, col_name in enumerate(col_names):
                    print(f"  {col_name}: {token[i]}")
                print("-" * 50)
        else:
            print("No glyph tokens found in the database.")
            
    except Exception as e:
        print(f"Error connecting to database or querying glyph tokens: {e}")
    finally:
        if conn is not None:
            conn.close()

if __name__ == "__main__":
    check_glyph_tokens()
