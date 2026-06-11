"""add pulse auto-close (auto summary) fields

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-06-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('teams', sa.Column('pulse_auto_close_minutes', sa.Integer(), nullable=True))
    op.execute("UPDATE teams SET pulse_auto_close_minutes = 60 WHERE pulse_auto_close_minutes IS NULL")
    op.alter_column('teams', 'pulse_auto_close_minutes', nullable=False)

    op.add_column('activity_sessions', sa.Column('summary_posted', sa.Boolean(), nullable=True))
    op.execute("UPDATE activity_sessions SET summary_posted = false WHERE summary_posted IS NULL")
    op.alter_column('activity_sessions', 'summary_posted', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('activity_sessions', 'summary_posted')
    op.drop_column('teams', 'pulse_auto_close_minutes')
