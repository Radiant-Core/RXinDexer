"""Schema alignment with reference implementation

Adds new tables and fields to match the rxd-glyph-explorer reference:
- New unified 'glyphs' table (primary token table)
- New 'glyph_actions' table for action tracking
- New 'contract_groups' and 'contracts' tables for DMINT
- New 'contract_list' table
- New 'stats' table for global statistics
- New 'glyph_likes' table for user engagement
- New 'import_state' table for sync tracking
- New fields on 'blocks' table (reorg)
- New fields on 'utxos' table (date, change, is_glyph_reveal, glyph_ref, contract_type)
- New fields on 'glyph_tokens' table (spent, fresh, melted, sealed, swap_pending, value)

Revision ID: 20251216_schema_alignment
Revises: 
Create Date: 2025-12-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251216_schema_alignment'
down_revision = 'nfts_table_enhancement'  # Chain from latest migration
branch_labels = None
depends_on = None


def upgrade():
    # =========================================================================
    # NEW TABLES
    # =========================================================================
    
    # 1. Unified Glyphs table (primary token table matching reference)
    op.create_table(
        'glyphs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ref', sa.String(72), nullable=False, unique=True),
        sa.Column('token_type', sa.String(20), nullable=False),
        sa.Column('p', sa.JSON(), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('ticker', sa.String(50), nullable=True),
        sa.Column('type', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('immutable', sa.Boolean(), nullable=True),
        sa.Column('attrs', sa.JSON(), nullable=True),
        sa.Column('author', sa.String(), nullable=False, server_default=''),
        sa.Column('container', sa.String(), nullable=False, server_default=''),
        sa.Column('is_container', sa.Boolean(), default=False),
        sa.Column('container_items', sa.JSON(), nullable=True),
        sa.Column('spent', sa.Boolean(), nullable=False, default=False),
        sa.Column('fresh', sa.Boolean(), nullable=False, default=True),
        sa.Column('melted', sa.Boolean(), default=False),
        sa.Column('sealed', sa.Boolean(), default=False),
        sa.Column('swap_pending', sa.Boolean(), default=False),
        sa.Column('value', sa.BigInteger(), nullable=True),
        sa.Column('location', sa.String(), nullable=True),
        sa.Column('reveal_outpoint', sa.String(), nullable=True),
        sa.Column('last_txo_id', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.Integer(), nullable=True),
        sa.Column('embed_type', sa.String(100), nullable=True),
        sa.Column('embed_data', sa.Text(), nullable=True),
        sa.Column('remote_type', sa.String(100), nullable=True),
        sa.Column('remote_url', sa.String(500), nullable=True),
        sa.Column('remote_hash', sa.String(), nullable=True),
        sa.Column('remote_hash_sig', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # Glyphs indexes
    op.create_index('ix_glyphs_ref', 'glyphs', ['ref'], unique=True)
    op.create_index('ix_glyphs_token_type', 'glyphs', ['token_type'])
    op.create_index('ix_glyphs_name', 'glyphs', ['name'])
    op.create_index('ix_glyphs_ticker', 'glyphs', ['ticker'])
    op.create_index('ix_glyphs_spent_fresh', 'glyphs', ['spent', 'fresh'])
    op.create_index('ix_glyphs_container', 'glyphs', ['container'])
    op.create_index('ix_glyphs_is_container', 'glyphs', ['is_container'])
    op.create_index('ix_glyphs_spent_token_type', 'glyphs', ['spent', 'token_type'])
    op.create_index('ix_glyphs_spent_is_container', 'glyphs', ['spent', 'is_container'])
    op.create_index('ix_glyphs_author', 'glyphs', ['author'])
    op.create_index('ix_glyphs_height', 'glyphs', ['height'])
    op.create_index('ix_glyphs_token_type_id', 'glyphs', ['token_type', 'id'])
    
    # 2. Glyph Actions table
    op.create_table(
        'glyph_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ref', sa.String(72), nullable=False),
        sa.Column('type', sa.String(30), nullable=False),
        sa.Column('txid', sa.String(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('metadata', sa.JSON(), nullable=True),
    )
    
    op.create_index('ix_glyph_actions_ref', 'glyph_actions', ['ref'])
    op.create_index('ix_glyph_actions_type', 'glyph_actions', ['type'])
    op.create_index('ix_glyph_actions_txid', 'glyph_actions', ['txid'])
    op.create_index('ix_glyph_actions_height', 'glyph_actions', ['height'])
    op.create_index('ix_glyph_actions_ref_type', 'glyph_actions', ['ref', 'type'])
    op.create_index('ix_glyph_actions_ref_height', 'glyph_actions', ['ref', 'height'])
    op.create_index('ix_glyph_actions_type_height', 'glyph_actions', ['type', 'height'])
    
    # 3. Contract Groups table (for DMINT)
    op.create_table(
        'contract_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('first_ref', sa.String(72), nullable=False, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('ticker', sa.String(50), server_default=''),
        sa.Column('token_type', sa.String(20), server_default='FT'),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('num_contracts', sa.Integer(), default=0),
        sa.Column('total_supply', sa.BigInteger(), default=0),
        sa.Column('minted_supply', sa.BigInteger(), default=0),
        sa.Column('glyph_data', sa.JSON(), nullable=True),
        sa.Column('files', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_contract_groups_first_ref', 'contract_groups', ['first_ref'], unique=True)
    
    # 4. Contracts table (for DMINT)
    op.create_table(
        'contracts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('contract_ref', sa.String(72), nullable=False, unique=True),
        sa.Column('token_ref', sa.String(72), nullable=False),
        sa.Column('location', sa.String(), nullable=False),
        sa.Column('output_index', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), default=0),
        sa.Column('max_height', sa.BigInteger(), default=0),
        sa.Column('reward', sa.BigInteger(), default=0),
        sa.Column('target', sa.BigInteger(), default=0),
        sa.Column('script', sa.Text(), server_default=''),
        sa.Column('message', sa.String(255), server_default=''),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('contract_groups.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_contracts_contract_ref', 'contracts', ['contract_ref'], unique=True)
    op.create_index('ix_contracts_token_ref', 'contracts', ['token_ref'])
    op.create_index('ix_contracts_group_id', 'contracts', ['group_id'])
    
    # 5. Contract List table
    op.create_table(
        'contract_list',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('base_ref', sa.String(72), nullable=False),
        sa.Column('count', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_contract_list_base_ref', 'contract_list', ['base_ref'])
    
    # 6. Stats table
    op.create_table(
        'stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('glyphs_total', sa.Integer(), default=0),
        sa.Column('glyphs_nft', sa.Integer(), default=0),
        sa.Column('glyphs_ft', sa.Integer(), default=0),
        sa.Column('glyphs_dat', sa.Integer(), default=0),
        sa.Column('glyphs_containers', sa.Integer(), default=0),
        sa.Column('glyphs_contained_items', sa.Integer(), default=0),
        sa.Column('glyphs_users', sa.Integer(), default=0),
        sa.Column('txos_total', sa.Integer(), default=0),
        sa.Column('txos_rxd', sa.Integer(), default=0),
        sa.Column('txos_nft', sa.Integer(), default=0),
        sa.Column('txos_ft', sa.Integer(), default=0),
        sa.Column('blocks_count', sa.Integer(), default=0),
        sa.Column('latest_block_hash', sa.String(), nullable=True),
        sa.Column('latest_block_height', sa.Integer(), nullable=True),
        sa.Column('latest_block_timestamp', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # 7. Glyph Likes table
    op.create_table(
        'glyph_likes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('glyph_ref', sa.String(72), nullable=False),
        sa.Column('user_address', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    
    op.create_index('ix_glyph_likes_glyph_ref', 'glyph_likes', ['glyph_ref'])
    op.create_index('ix_glyph_likes_user_address', 'glyph_likes', ['user_address'])
    op.create_index('ix_glyph_likes_ref_user', 'glyph_likes', ['glyph_ref', 'user_address'])
    op.create_unique_constraint('uq_glyph_like', 'glyph_likes', ['glyph_ref', 'user_address'])
    
    # 8. Import State table
    op.create_table(
        'import_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('last_block_height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_block_hash', sa.String(), nullable=False, server_default=''),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('is_importing', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    # =========================================================================
    # ALTER EXISTING TABLES - Add new columns
    # =========================================================================
    
    # Add reorg column to blocks table
    op.add_column('blocks', sa.Column('reorg', sa.Boolean(), default=False))
    
    # Add new columns to utxos table
    op.add_column('utxos', sa.Column('date', sa.Integer(), nullable=True))
    op.add_column('utxos', sa.Column('change', sa.Boolean(), nullable=True))
    op.add_column('utxos', sa.Column('is_glyph_reveal', sa.Boolean(), default=False))
    op.add_column('utxos', sa.Column('glyph_ref', sa.String(), nullable=True))
    op.add_column('utxos', sa.Column('contract_type', sa.String(20), nullable=True))
    
    op.create_index('ix_utxos_glyph_ref', 'utxos', ['glyph_ref'])
    op.create_index('ix_utxos_contract_type', 'utxos', ['contract_type'])
    
    # Add new columns to glyph_tokens table (for backward compatibility)
    op.add_column('glyph_tokens', sa.Column('spent', sa.Boolean(), default=False))
    op.add_column('glyph_tokens', sa.Column('fresh', sa.Boolean(), default=True))
    op.add_column('glyph_tokens', sa.Column('melted', sa.Boolean(), default=False))
    op.add_column('glyph_tokens', sa.Column('sealed', sa.Boolean(), default=False))
    op.add_column('glyph_tokens', sa.Column('swap_pending', sa.Boolean(), default=False))
    op.add_column('glyph_tokens', sa.Column('value', sa.BigInteger(), nullable=True))
    
    op.create_index('ix_glyph_tokens_spent', 'glyph_tokens', ['spent'])


def downgrade():
    # Drop new indexes from existing tables
    op.drop_index('ix_glyph_tokens_spent', table_name='glyph_tokens')
    op.drop_index('ix_utxos_contract_type', table_name='utxos')
    op.drop_index('ix_utxos_glyph_ref', table_name='utxos')
    
    # Drop new columns from glyph_tokens
    op.drop_column('glyph_tokens', 'value')
    op.drop_column('glyph_tokens', 'swap_pending')
    op.drop_column('glyph_tokens', 'sealed')
    op.drop_column('glyph_tokens', 'melted')
    op.drop_column('glyph_tokens', 'fresh')
    op.drop_column('glyph_tokens', 'spent')
    
    # Drop new columns from utxos
    op.drop_column('utxos', 'contract_type')
    op.drop_column('utxos', 'glyph_ref')
    op.drop_column('utxos', 'is_glyph_reveal')
    op.drop_column('utxos', 'change')
    op.drop_column('utxos', 'date')
    
    # Drop reorg column from blocks
    op.drop_column('blocks', 'reorg')
    
    # Drop new tables
    op.drop_table('import_state')
    op.drop_table('glyph_likes')
    op.drop_table('stats')
    op.drop_table('contract_list')
    op.drop_table('contracts')
    op.drop_table('contract_groups')
    op.drop_table('glyph_actions')
    op.drop_table('glyphs')
