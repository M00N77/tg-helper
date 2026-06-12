"""add_message_sentiments

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-11 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'message_sentiments',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('sentiment', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_message_sentiments_team_id'), 'message_sentiments', ['team_id'], unique=False)
    op.create_index(op.f('ix_message_sentiments_user_id'), 'message_sentiments', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_message_sentiments_user_id'), table_name='message_sentiments')
    op.drop_index(op.f('ix_message_sentiments_team_id'), table_name='message_sentiments')
    op.drop_table('message_sentiments')
