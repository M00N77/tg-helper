"""add_role_permissions

Revision ID: 8026699cc35f
Revises: d2854d2a3417
Create Date: 2026-06-12 20:06:06.707051

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8026699cc35f'
down_revision: Union[str, Sequence[str], None] = 'd2854d2a3417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_taskstatus_enum = sa.Enum('pending', 'processing', 'approved', 'rejected', name='taskstatus')


def upgrade() -> None:
    """Upgrade schema."""
    # Явно создаём ENUM-тип перед alter_column — иначе PG падает с «тип не существует».
    _taskstatus_enum.create(op.get_bind())

    op.create_table('role_permissions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('team_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('allowed_intents', sa.JSON(), nullable=False),
    sa.Column('denied_intents', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'role', name='uq_role_per_team')
    )
    op.create_index(op.f('ix_role_permissions_team_id'), 'role_permissions', ['team_id'], unique=False)
    op.add_column('pending_team_tasks', sa.Column('telegram_trigger_id', sa.String(length=64), nullable=True))
    op.alter_column('pending_team_tasks', 'status',
               existing_type=sa.VARCHAR(length=16),
               type_=_taskstatus_enum,
               existing_nullable=False,
               postgresql_using='status::text::taskstatus')
    op.add_column('teams', sa.Column('is_supergroup', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('teams', sa.Column('thread_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('teams', 'thread_id')
    op.drop_column('teams', 'is_supergroup')
    op.alter_column('pending_team_tasks', 'status',
               existing_type=_taskstatus_enum,
               type_=sa.VARCHAR(length=16),
               existing_nullable=False)
    op.drop_column('pending_team_tasks', 'telegram_trigger_id')
    op.drop_index(op.f('ix_role_permissions_team_id'), table_name='role_permissions')
    op.drop_table('role_permissions')
    _taskstatus_enum.drop(op.get_bind())
