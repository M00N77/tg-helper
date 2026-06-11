"""pm features: standups blockers time_logs sociometry email

Revision ID: pm001
Revises: b2c3d4e5f6a7
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa

revision = "pm001"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Message: reply_to_msg_id
    op.add_column("messages", sa.Column("reply_to_msg_id", sa.BigInteger(), nullable=True))

    # Team: standup fields
    op.add_column("teams", sa.Column("standup_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("teams", sa.Column("standup_time", sa.String(5), nullable=False, server_default="09:30"))
    op.add_column("teams", sa.Column("standup_msg_id", sa.BigInteger(), nullable=True))

    # standups
    op.create_table(
        "standups",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("date", sa.DateTime(), nullable=False, index=True),
        sa.Column("done_today", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan_today", sa.Text(), nullable=False, server_default=""),
        sa.Column("blockers", sa.Text(), nullable=False, server_default=""),
        sa.Column("mood", sa.String(16), nullable=False, server_default="neutral"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", "date", name="uq_standup_team_user_date"),
    )

    # blockers
    op.create_table(
        "blockers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("reported_by", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("standup_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )

    # time_logs
    op.create_table(
        "time_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("date", sa.DateTime(), nullable=False),
    )

    # sociometry_cache
    op.create_table(
        "sociometry_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # email_messages
    op.create_table(
        "email_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sender", sa.String(256), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("has_deadline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("commitment_id", sa.BigInteger(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_table("email_messages")
    op.drop_table("sociometry_cache")
    op.drop_table("time_logs")
    op.drop_table("blockers")
    op.drop_table("standups")
    op.drop_column("teams", "standup_msg_id")
    op.drop_column("teams", "standup_time")
    op.drop_column("teams", "standup_enabled")
    op.drop_column("messages", "reply_to_msg_id")
