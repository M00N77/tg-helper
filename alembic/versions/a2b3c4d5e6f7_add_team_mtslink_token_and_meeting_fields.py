"""add team mtslink_token and meeting fields

Revision ID: a2b3c4d5e6f7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('mtslink_token', sa.Text(), nullable=True))
    op.add_column('meetings', sa.Column('mtslink_record_id', sa.String(length=128), nullable=True))
    op.add_column('meetings', sa.Column('raw_llm_output', sa.Text(), nullable=True))
    op.add_column('meetings', sa.Column('duration_sec', sa.Integer(), nullable=True))
    op.add_column('meetings', sa.Column('processed_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_meetings_mtslink_record_id'), 'meetings', ['mtslink_record_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meetings_mtslink_record_id'), table_name='meetings')
    op.drop_column('meetings', 'processed_at')
    op.drop_column('meetings', 'duration_sec')
    op.drop_column('meetings', 'raw_llm_output')
    op.drop_column('meetings', 'mtslink_record_id')
    op.drop_column('teams', 'mtslink_token')
