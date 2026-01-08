"""Add data retention policies

Revision ID: 20260108_data_retention
Revises: 20260107_fix_mv_concurrent
Create Date: 2026-01-08

"""

from alembic import op
from sqlalchemy import text
import datetime

# revision identifiers, used by Alembic.
revision = '20260108_data_retention'
down_revision = '20260107_fix_mv_concurrent'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Enable pg_partman extension for automated partition management (optional)
    try:
        conn.execute(
            text(
                """
                CREATE EXTENSION IF NOT EXISTS pg_partman;
                """
            )
        )
    except Exception as e:
        # pg_partman is not available, skip partition management setup
        print(f"Warning: pg_partman extension not available: {e}")
        return
    
    # Create partitioned versions of the tables
    # 1. glyph_actions - partition by timestamp (monthly)
    conn.execute(
        text(
            """
            -- Create partitioned glyph_actions table
            CREATE TABLE glyph_actions_partitioned (
                LIKE glyph_actions INCLUDING ALL
            ) PARTITION BY RANGE (timestamp);
            
            -- Create initial partitions (past, current, future months)
            CREATE TABLE glyph_actions_2025_01 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
            CREATE TABLE glyph_actions_2025_02 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
            CREATE TABLE glyph_actions_2025_03 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
            CREATE TABLE glyph_actions_2025_04 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
            CREATE TABLE glyph_actions_2025_05 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
            CREATE TABLE glyph_actions_2025_06 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
            CREATE TABLE glyph_actions_2025_07 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
            CREATE TABLE glyph_actions_2025_08 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
            CREATE TABLE glyph_actions_2025_09 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
            CREATE TABLE glyph_actions_2025_10 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
            CREATE TABLE glyph_actions_2025_11 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
            CREATE TABLE glyph_actions_2025_12 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
            CREATE TABLE glyph_actions_2026_01 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
            CREATE TABLE glyph_actions_2026_02 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
            CREATE TABLE glyph_actions_2026_03 PARTITION OF glyph_actions_partitioned
                FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
            """
        )
    )
    
    # 2. token_price_history - partition by recorded_at (monthly)
    conn.execute(
        text(
            """
            -- Create partitioned token_price_history table
            CREATE TABLE token_price_history_partitioned (
                LIKE token_price_history INCLUDING ALL
            ) PARTITION BY RANGE (recorded_at);
            
            -- Create initial partitions
            CREATE TABLE token_price_history_2025_01 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
            CREATE TABLE token_price_history_2025_02 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
            CREATE TABLE token_price_history_2025_03 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
            CREATE TABLE token_price_history_2025_04 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
            CREATE TABLE token_price_history_2025_05 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
            CREATE TABLE token_price_history_2025_06 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
            CREATE TABLE token_price_history_2025_07 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
            CREATE TABLE token_price_history_2025_08 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
            CREATE TABLE token_price_history_2025_09 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
            CREATE TABLE token_price_history_2025_10 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
            CREATE TABLE token_price_history_2025_11 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
            CREATE TABLE token_price_history_2025_12 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
            CREATE TABLE token_price_history_2026_01 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
            CREATE TABLE token_price_history_2026_02 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
            CREATE TABLE token_price_history_2026_03 PARTITION OF token_price_history_partitioned
                FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
            """
        )
    )
    
    # 3. token_volume_daily - partition by date (monthly)
    conn.execute(
        text(
            """
            -- Create partitioned token_volume_daily table
            CREATE TABLE token_volume_daily_partitioned (
                LIKE token_volume_daily INCLUDING ALL
            ) PARTITION BY RANGE (date);
            
            -- Create initial partitions
            CREATE TABLE token_volume_daily_2025_01 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
            CREATE TABLE token_volume_daily_2025_02 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
            CREATE TABLE token_volume_daily_2025_03 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
            CREATE TABLE token_volume_daily_2025_04 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
            CREATE TABLE token_volume_daily_2025_05 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
            CREATE TABLE token_volume_daily_2025_06 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
            CREATE TABLE token_volume_daily_2025_07 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
            CREATE TABLE token_volume_daily_2025_08 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
            CREATE TABLE token_volume_daily_2025_09 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
            CREATE TABLE token_volume_daily_2025_10 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
            CREATE TABLE token_volume_daily_2025_11 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
            CREATE TABLE token_volume_daily_2025_12 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
            CREATE TABLE token_volume_daily_2026_01 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
            CREATE TABLE token_volume_daily_2026_02 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
            CREATE TABLE token_volume_daily_2026_03 PARTITION OF token_volume_daily_partitioned
                FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
            """
        )
    )
    
    # Create indexes on partitioned tables
    conn.execute(
        text(
            """
            -- Indexes for glyph_actions_partitioned
            CREATE INDEX idx_glyph_actions_partitioned_ref ON glyph_actions_partitioned(ref);
            CREATE INDEX idx_glyph_actions_partitioned_type ON glyph_actions_partitioned(type);
            CREATE INDEX idx_glyph_actions_partitioned_txid ON glyph_actions_partitioned(txid);
            CREATE INDEX idx_glyph_actions_partitioned_height ON glyph_actions_partitioned(height);
            CREATE INDEX idx_glyph_actions_partitioned_timestamp ON glyph_actions_partitioned(timestamp);
            
            -- Indexes for token_price_history_partitioned
            CREATE INDEX idx_token_price_history_partitioned_token_id ON token_price_history_partitioned(token_id);
            CREATE INDEX idx_token_price_history_partitioned_recorded_at ON token_price_history_partitioned(recorded_at);
            CREATE INDEX idx_token_price_history_partitioned_txid ON token_price_history_partitioned(txid);
            
            -- Indexes for token_volume_daily_partitioned
            CREATE INDEX idx_token_volume_daily_partitioned_token_id ON token_volume_daily_partitioned(token_id);
            CREATE INDEX idx_token_volume_daily_partitioned_date ON token_volume_daily_partitioned(date);
            """
        )
    )
    
    # Create partition management functions
    conn.execute(
        text(
            """
            -- Function to create new partitions automatically
            CREATE OR REPLACE FUNCTION create_monthly_partitions()
            RETURNS void AS $$
            DECLARE
                v_table_name TEXT;
                v_partition_name TEXT;
                v_start_date DATE;
                v_end_date DATE;
                v_month_ahead INTERVAL := '1 month';
                v_months_to_create INTEGER := 3; -- Create 3 months ahead
            BEGIN
                -- Create partitions for glyph_actions
                FOR i IN 0..v_months_to_create-1 LOOP
                    v_start_date := date_trunc('month', CURRENT_DATE + (v_month_ahead * i));
                    v_end_date := v_start_date + v_month_ahead;
                    v_partition_name := 'glyph_actions_' || to_char(v_start_date, 'YYYY_MM');
                    
                    EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF glyph_actions_partitioned
                        FOR VALUES FROM (%L) TO (%L)', 
                        v_partition_name, v_start_date, v_end_date);
                END LOOP;
                
                -- Create partitions for token_price_history
                FOR i IN 0..v_months_to_create-1 LOOP
                    v_start_date := date_trunc('month', CURRENT_DATE + (v_month_ahead * i));
                    v_end_date := v_start_date + v_month_ahead;
                    v_partition_name := 'token_price_history_' || to_char(v_start_date, 'YYYY_MM');
                    
                    EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF token_price_history_partitioned
                        FOR VALUES FROM (%L) TO (%L)', 
                        v_partition_name, v_start_date, v_end_date);
                END LOOP;
                
                -- Create partitions for token_volume_daily
                FOR i IN 0..v_months_to_create-1 LOOP
                    v_start_date := date_trunc('month', CURRENT_DATE + (v_month_ahead * i));
                    v_end_date := v_start_date + v_month_ahead;
                    v_partition_name := 'token_volume_daily_' || to_char(v_start_date, 'YYYY_MM');
                    
                    EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF token_volume_daily_partitioned
                        FOR VALUES FROM (%L) TO (%L)', 
                        v_partition_name, v_start_date, v_end_date);
                END LOOP;
                
                RAISE NOTICE 'Monthly partitions created successfully';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    
    # Create function to migrate data from original tables to partitioned tables
    conn.execute(
        text(
            """
            -- Function to migrate data to partitioned tables
            CREATE OR REPLACE FUNCTION migrate_to_partitioned_tables()
            RETURNS void AS $$
            BEGIN
                RAISE NOTICE 'Starting migration to partitioned tables...';
                
                -- Migrate glyph_actions
                INSERT INTO glyph_actions_partitioned 
                SELECT * FROM glyph_actions 
                ON CONFLICT DO NOTHING;
                
                -- Migrate token_price_history  
                INSERT INTO token_price_history_partitioned
                SELECT * FROM token_price_history
                ON CONFLICT DO NOTHING;
                
                -- Migrate token_volume_daily
                INSERT INTO token_volume_daily_partitioned
                SELECT * FROM token_volume_daily
                ON CONFLICT DO NOTHING;
                
                RAISE NOTICE 'Migration to partitioned tables completed';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    
    # Create configuration table for partition management
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS partition_config (
                id SERIAL PRIMARY KEY,
                table_name VARCHAR(100) NOT NULL UNIQUE,
                partition_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
                retention_months INTEGER DEFAULT 48, -- Keep 48 months (4 years)
                auto_create BOOLEAN DEFAULT true,
                last_partition_created TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            
            -- Insert default partition configuration
            INSERT INTO partition_config (table_name, partition_type, retention_months)
            VALUES 
                ('glyph_actions', 'monthly', 48),
                ('token_price_history', 'monthly', 48), 
                ('token_volume_daily', 'monthly', 48)
            ON CONFLICT (table_name) DO UPDATE SET
                partition_type = EXCLUDED.partition_type,
                retention_months = EXCLUDED.retention_months,
                updated_at = NOW();
            """
        )
    )
    
    # Schedule automatic partition creation using pg_cron
    conn.execute(
        text(
            """
            -- Schedule monthly partition creation (1st of each month at 3 AM)
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                    -- Remove existing job if it exists
                    PERFORM cron.unschedule('rxindexer-partition-creation');
                    
                    -- Schedule new job
                    PERFORM cron.schedule(
                        'rxindexer-partition-creation',
                        '0 3 1 * *',  -- 1st of each month at 3 AM
                        'SELECT create_monthly_partitions();'
                    );
                    
                    RAISE NOTICE 'Scheduled monthly partition creation via pg_cron';
                ELSE
                    RAISE NOTICE 'pg_cron extension not available, manual partition creation required';
                END IF;
            END $$;
            """
        )
    )
    
    print("Time-based partitioning implemented successfully")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Remove scheduled partition creation job
    conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                    PERFORM cron.unschedule('rxindexer-partition-creation');
                END IF;
            END $$;
            """
        )
    )
    
    # Drop partition management functions
    conn.execute(text("DROP FUNCTION IF EXISTS create_monthly_partitions()"))
    conn.execute(text("DROP FUNCTION IF EXISTS migrate_to_partitioned_tables()"))
    
    # Drop partitioned tables and their partitions
    conn.execute(text("DROP TABLE IF EXISTS glyph_actions_partitioned CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS token_price_history_partitioned CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS token_volume_daily_partitioned CASCADE"))
    
    # Drop config table
    conn.execute(text("DROP TABLE IF EXISTS partition_config"))
    
    # Note: We keep the original tables (glyph_actions, token_price_history, token_volume_daily)
    # as they contain the original data
    
    print("Time-based partitioning removed")
