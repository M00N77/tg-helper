"""add reminder work hours and days to user_settings

Revision ID: e6f7a8b9c0d1
Revises: e570f9fd76ac
Create Date: 2026-06-09 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'e570f9fd76ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_settings', sa.Column('reminder_work_hours_start', sa.Integer(), server_default='9', nullable=False))
    op.add_column('user_settings', sa.Column('reminder_work_hours_end', sa.Integer(), server_default='21', nullable=False))
    op.add_column('user_settings', sa.Column('reminder_work_days', sa.String(13), server_default='1,2,3,4,5', nullable=False))


def downgrade() -> None:
    op.drop_column('user_settings', 'reminder_work_days')
    op.drop_column('user_settings', 'reminder_work_hours_end')
    op.drop_column('user_settings', 'reminder_work_hours_start')
