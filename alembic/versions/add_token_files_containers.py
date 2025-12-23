"""Add token_files, containers, and backfill_status tables

Revision ID: add_token_files_containers
Revises: production_v2_complete
Create Date: 2025-12-11
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_token_files_containers'
down_revision = 'production_v2_complete'
branch_labels = None
depends_on = None


def upgrade():
    # Create token_files table
    op.create_table(
        'token_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_id', sa.String(), nullable=False),
        sa.Column('token_type', sa.String(), nullable=False),
        sa.Column('file_key', sa.String(), nullable=True),
        sa.Column('mime_type', sa.String(), nullable=True),
        sa.Column('file_data', sa.Text(), nullable=True),
        sa.Column('remote_url', sa.String(), nullable=True),
        sa.Column('file_hash', sa.String(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_token_files_token_id', 'token_files', ['token_id'])
    op.create_index('idx_token_files_token_type', 'token_files', ['token_type'])

    # Create containers table
    op.create_table(
        'containers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('container_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner', sa.String(), nullable=True),
        sa.Column('token_count', sa.Integer(), default=0),
        sa.Column('container_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('container_id')
    )
    op.create_index('idx_containers_container_id', 'containers', ['container_id'])
    op.create_index('idx_containers_owner', 'containers', ['owner'])

    # Create backfill_status table
    op.create_table(
        'backfill_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('backfill_type', sa.String(), nullable=False),
        sa.Column('is_complete', sa.Boolean(), default=False),
        sa.Column('last_processed_id', sa.BigInteger(), nullable=True),
        sa.Column('total_processed', sa.BigInteger(), default=0),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('backfill_type')
    )


def downgrade():
    op.drop_table('backfill_status')
    op.drop_index('idx_containers_owner', 'containers')
    op.drop_index('idx_containers_container_id', 'containers')
    op.drop_table('containers')
    op.drop_index('idx_token_files_token_type', 'token_files')
    op.drop_index('idx_token_files_token_id', 'token_files')
    op.drop_table('token_files')
