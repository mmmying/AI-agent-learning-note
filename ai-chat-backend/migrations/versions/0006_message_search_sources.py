"""为消息增加联网搜索来源与搜索词列；历史消息为 NULL。

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("sources", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("search_query", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("messages", "search_query")
    op.drop_column("messages", "sources")