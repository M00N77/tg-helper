"""add team_dictionaries

Revision ID: b4c5d6e7f8a9
Revises: 8026699cc35f
Create Date: 2026-06-12 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "8026699cc35f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_dictionaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("term", sa.String(length=256), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "term", name="uq_team_dictionary_term"),
    )
    op.create_index(
        op.f("ix_team_dictionaries_team_id"),
        "team_dictionaries",
        ["team_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_team_dictionaries_team_id"), table_name="team_dictionaries"
    )
    op.drop_table("team_dictionaries")
