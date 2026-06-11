"""add group activities (pulse polls) tables and team scheduling columns

Revision ID: a1b2c3d4e5f6
Revises: edbf521baec0
Create Date: 2026-06-11 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'edbf521baec0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── teams: расписание групповых активностей ──
    op.add_column('teams', sa.Column('activities_enabled', sa.Boolean(), nullable=True))
    op.add_column('teams', sa.Column('pulse_time', sa.String(length=5), nullable=True))
    op.execute("UPDATE teams SET activities_enabled = false WHERE activities_enabled IS NULL")
    op.execute("UPDATE teams SET pulse_time = '17:00' WHERE pulse_time IS NULL")
    op.alter_column('teams', 'activities_enabled', nullable=False)
    op.alter_column('teams', 'pulse_time', nullable=False)

    # ── activity_sessions ──
    op.create_table(
        'activity_sessions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.BigInteger(), nullable=False),
        sa.Column('activity_code', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('is_anonymous', sa.Boolean(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_activity_sessions_team_id'), 'activity_sessions', ['team_id'], unique=False)
    op.create_index(op.f('ix_activity_sessions_activity_code'), 'activity_sessions', ['activity_code'], unique=False)
    op.create_index(op.f('ix_activity_sessions_chat_id'), 'activity_sessions', ['chat_id'], unique=False)
    op.create_index(op.f('ix_activity_sessions_started_at'), 'activity_sessions', ['started_at'], unique=False)

    # ── activity_responses ──
    op.create_table(
        'activity_responses',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.BigInteger(), nullable=False),
        sa.Column('respondent_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('answer_value', sa.Integer(), nullable=True),
        sa.Column('answer_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['activity_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'respondent_hash', name='uq_activity_resp_session_hash'),
    )
    op.create_index(op.f('ix_activity_responses_session_id'), 'activity_responses', ['session_id'], unique=False)
    op.create_index(op.f('ix_activity_responses_respondent_hash'), 'activity_responses', ['respondent_hash'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_activity_responses_respondent_hash'), table_name='activity_responses')
    op.drop_index(op.f('ix_activity_responses_session_id'), table_name='activity_responses')
    op.drop_table('activity_responses')
    op.drop_index(op.f('ix_activity_sessions_started_at'), table_name='activity_sessions')
    op.drop_index(op.f('ix_activity_sessions_chat_id'), table_name='activity_sessions')
    op.drop_index(op.f('ix_activity_sessions_activity_code'), table_name='activity_sessions')
    op.drop_index(op.f('ix_activity_sessions_team_id'), table_name='activity_sessions')
    op.drop_table('activity_sessions')
    op.drop_column('teams', 'pulse_time')
    op.drop_column('teams', 'activities_enabled')
