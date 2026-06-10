"""set_team_name_server_default

Revision ID: a1b2c3d4e5f6
Revises: dc0d391b3de9
Create Date: 2026-06-11 01:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'dc0d391b3de9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('teams', 'name', server_default='Команда')


def downgrade() -> None:
    op.alter_column('teams', 'name', server_default=None)
