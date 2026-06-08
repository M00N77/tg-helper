"""set default timezone to Europe/Moscow

Revision ID: 9a5837c2b39e
Revises: ab3faac53e22
Create Date: 2026-06-09 00:32:46.774807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a5837c2b39e'
down_revision: Union[str, Sequence[str], None] = 'ab3faac53e22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE user_settings SET timezone = 'Europe/Moscow' WHERE timezone = 'UTC'")


def downgrade() -> None:
    op.execute("UPDATE user_settings SET timezone = 'UTC' WHERE timezone = 'Europe/Moscow'")
