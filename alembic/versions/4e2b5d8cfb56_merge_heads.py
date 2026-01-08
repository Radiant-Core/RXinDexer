"""merge heads

Revision ID: 4e2b5d8cfb56
Revises: 20260108_glyphs_columns, 6db246955ba2
Create Date: 2026-01-08 14:43:49.033598

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e2b5d8cfb56'
down_revision: Union[str, Sequence[str], None] = ('20260108_glyphs_columns', '6db246955ba2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
