"""add_display_name_to_team_members

Revision ID: b2c3d4e5f6a7
Revises: 51a6a3d0a092
Create Date: 2026-06-11 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '51a6a3d0a092'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('team_members', sa.Column('display_name', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('team_members', 'display_name')
