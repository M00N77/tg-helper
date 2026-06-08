"""rename telemost_url to meeting_url

Revision ID: f7d8e9a0b1c2
Revises: ecc8a2de13a1
Create Date: 2026-06-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7d8e9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'ecc8a2de13a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('meetings', 'telemost_url', new_column_name='meeting_url')


def downgrade() -> None:
    op.alter_column('meetings', 'meeting_url', new_column_name='telemost_url')
