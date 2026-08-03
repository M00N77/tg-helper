"""add_pending_invites

Revision ID: fa4f4a5c3d64
Revises: 87b605b5ec3c
Create Date: 2026-07-21 17:51:08.028891

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa4f4a5c3d64'
down_revision: Union[str, Sequence[str], None] = '87b605b5ec3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pending_invites',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('invited_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'username', name='uq_pending_invite')
    )
    with op.batch_alter_table('pending_invites', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_invites_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('pending_invites', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pending_invites_team_id'))
    op.drop_table('pending_invites')
