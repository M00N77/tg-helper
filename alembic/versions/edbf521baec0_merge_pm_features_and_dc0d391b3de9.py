"""merge pm_features and dc0d391b3de9

Revision ID: edbf521baec0
Revises: pm001, dc0d391b3de9
Create Date: 2026-06-11 18:14:41.889619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'edbf521baec0'
down_revision: Union[str, Sequence[str], None] = ('pm001', 'dc0d391b3de9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
