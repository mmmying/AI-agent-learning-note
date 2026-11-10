"""为会话增加累计 token 用量列；历史会话默认 0。

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_COLUMNS = ("input_tokens", "output_tokens", "total_tokens")


def upgrade():
    for column in _COLUMNS:
        op.add_column(
            "conversations",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade():
    for column in reversed(_COLUMNS):
        op.drop_column("conversations", column)