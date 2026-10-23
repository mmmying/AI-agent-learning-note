"""增加用户与会话归属；旧会话保持未分配。

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # SQLite 原地增加 nullable 外键列，避免重建父表导致消息被级联删除。
    op.execute("ALTER TABLE conversations ADD COLUMN user_id VARCHAR(36) "
               "REFERENCES users(id)")
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])


def downgrade():
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_column("conversations", "user_id")
    op.drop_table("users")
