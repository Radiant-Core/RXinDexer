"""Merge migration branches

Revision ID: 6db246955ba2
Revises: 20260107_json_size, 20260108_data_retention
Create Date: 2026-01-08 06:21:32.496066

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6db246955ba2'
down_revision: Union[str, Sequence[str], None] = ('20260107_json_size', '20260108_data_retention')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
