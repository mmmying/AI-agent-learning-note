"""为会话增加滚动摘要与覆盖消息游标。"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations",
        sa.Column("context_summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_through_id", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("conversations", "summary_through_id")
    op.drop_column("conversations", "context_summary")
