"""add mtslink_event_id to meetings

Revision ID: d1e2f3a4b5c6
Revises: f9a8b7c6d5e4
Create Date: 2026-06-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'f9a8b7c6d5e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meetings', sa.Column('mtslink_event_id', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('meetings', 'mtslink_event_id')
