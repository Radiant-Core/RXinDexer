"""Update models to match database schema

Revision ID: 1234567890ab
Revises: 
Create Date: 2025-06-05 04:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1234567890ab'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Update blocks table
    op.add_column('blocks', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('blocks', sa.Column('prev_hash', sa.String(length=64), nullable=True))
    op.add_column('blocks', sa.Column('merkle_root', sa.String(length=64), nullable=False))
    op.add_column('blocks', sa.Column('bits', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('blocks', sa.Column('nonce', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('blocks', sa.Column('chainwork', sa.String(length=64), nullable=True))
    op.add_column('blocks', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    
    # Update transactions table
    op.alter_column('transactions', 'block_height', 
                   existing_type=sa.INTEGER(),
                   nullable=False,
                   existing_server_default=sa.text('0'))
    op.alter_column('transactions', 'block_hash', 
                   existing_type=sa.VARCHAR(length=64),
                   nullable=False)
    op.alter_column('transactions', 'version', 
                   existing_type=sa.INTEGER(),
                   nullable=False)
    op.alter_column('transactions', 'locktime', 
                   existing_type=sa.INTEGER(),
                   nullable=False)
    op.alter_column('transactions', 'created_at', 
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=False,
                   existing_server_default=sa.text('now()'))
    op.add_column('transactions', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    
    # Update utxos table
    op.alter_column('utxos', 'txid', 
                   existing_type=sa.VARCHAR(length=64),
                   nullable=False)
    op.alter_column('utxos', 'vout', 
                   existing_type=sa.INTEGER(),
                   nullable=False)
    op.alter_column('utxos', 'address', 
                   existing_type=sa.VARCHAR(length=64),
                   nullable=False)
    op.alter_column('utxos', 'amount', 
                   existing_type=sa.NUMERIC(precision=16, scale=8),
                   nullable=False)
    op.alter_column('utxos', 'spent', 
                   existing_type=sa.BOOLEAN(),
                   nullable=False,
                   existing_server_default=sa.text('false'))
    op.alter_column('utxos', 'block_height', 
                   existing_type=sa.INTEGER(),
                   nullable=False)
    op.alter_column('utxos', 'block_hash', 
                   existing_type=sa.VARCHAR(length=64),
                   nullable=False)
    op.alter_column('utxos', 'created_at', 
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=False,
                   existing_server_default=sa.text('now()'))
    op.add_column('utxos', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    
    # Update glyph_tokens table
    op.alter_column('glyph_tokens', 'type', 
                   existing_type=sa.VARCHAR(length=20),
                   nullable=False)
    op.alter_column('glyph_tokens', 'genesis_txid', 
                   existing_type=sa.VARCHAR(length=64),
                   nullable=False)
    op.alter_column('glyph_tokens', 'genesis_block_height', 
                   existing_type=sa.INTEGER(),
                   nullable=False)
    op.alter_column('glyph_tokens', 'created_at', 
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=False,
                   existing_server_default=sa.text('now()'))
    op.add_column('glyph_tokens', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    
    # Update sync_state table
    op.alter_column('sync_state', 'current_height', 
                   existing_type=sa.INTEGER(),
                   nullable=False,
                   existing_server_default=sa.text('0'))
    op.alter_column('sync_state', 'is_syncing', 
                   existing_type=sa.INTEGER(),
                   nullable=False,
                   existing_server_default=sa.text('0'))
    op.alter_column('sync_state', 'created_at', 
                   existing_type=postgresql.TIMESTAMP(),
                   nullable=False,
                   existing_server_default=sa.text('now()'))
    op.add_column('sync_state', sa.Column('glyph_scan_height', sa.Integer(), server_default='0', nullable=False))
    op.add_column('sync_state', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    
    # Add indexes
    op.create_index('idx_blocks_prev_hash', 'blocks', ['prev_hash'], unique=False)
    op.create_index('idx_blocks_timestamp', 'blocks', ['timestamp'], unique=False)
    op.create_index('idx_transactions_block_height', 'transactions', ['block_height'], unique=False)
    op.create_index('idx_transactions_created_at', 'transactions', ['created_at'], unique=False)
    op.create_index('idx_utxos_address', 'utxos', ['address'], unique=False)
    op.create_index('idx_utxos_block_height', 'utxos', ['block_height'], unique=False)
    op.create_index('idx_utxos_token_ref', 'utxos', ['token_ref'], unique=False)
    op.create_index('idx_sync_state_current_height', 'sync_state', ['current_height'], unique=False)

def downgrade():
    # Drop indexes
    op.drop_index('idx_sync_state_current_height', table_name='sync_state')
    op.drop_index('idx_utxos_token_ref', table_name='utxos')
    op.drop_index('idx_utxos_block_height', table_name='utxos')
    op.drop_index('idx_utxos_address', table_name='utxos')
    op.drop_index('idx_transactions_created_at', table_name='transactions')
    op.drop_index('idx_transactions_block_height', table_name='transactions')
    op.drop_index('idx_blocks_timestamp', table_name='blocks')
    op.drop_index('idx_blocks_prev_hash', table_name='blocks')
    
    # Revert sync_state changes
    op.drop_column('sync_state', 'updated_at')
    op.drop_column('sync_state', 'glyph_scan_height')
    
    # Revert glyph_tokens changes
    op.drop_column('glyph_tokens', 'updated_at')
    
    # Revert utxos changes
    op.drop_column('utxos', 'updated_at')
    
    # Revert transactions changes
    op.drop_column('transactions', 'updated_at')
    
    # Revert blocks changes
    op.drop_column('blocks', 'updated_at')
    op.drop_column('blocks', 'chainwork')
    op.drop_column('blocks', 'nonce')
    op.drop_column('blocks', 'bits')
    op.drop_column('blocks', 'merkle_root')
    op.drop_column('blocks', 'prev_hash')
    op.drop_column('blocks', 'version')
