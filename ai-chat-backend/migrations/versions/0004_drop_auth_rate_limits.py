"""限流计数改存 Redis，删除数据库里的计数表。

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_auth_rate_limits_expires_at", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")


def downgrade():
    op.create_table(
        "auth_rate_limits",
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("action", "username"),
    )
    op.create_index(
        "ix_auth_rate_limits_expires_at", "auth_rate_limits", ["expires_at"]
    )
