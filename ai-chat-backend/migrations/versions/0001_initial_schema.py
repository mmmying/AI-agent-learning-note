"""创建会话和消息表。

Revision ID: 0001
Revises:
"""

from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _create_tables(conversations="conversations", messages="messages") -> None:
    op.create_table(
        conversations,
        sa.Column("id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        messages,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.String(50), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], [f"{conversations}.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )


def _legacy_metadata() -> sa.MetaData:
    """冻结早期手写 SQL 的结构，避免误接管未知版本的表。"""
    metadata = sa.MetaData()
    sa.Table(
        "conversations", metadata,
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
    )
    sa.Table(
        "messages", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Index("idx_messages_conversation_id", "conversation_id"),
    )
    return metadata


def _upgrade_legacy_database(connection) -> None:
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if tables - {"conversations", "messages", "schema_migrations", "alembic_version"}:
        raise RuntimeError("发现未知旧表，请先检查数据库结构，不能自动迁移。")
    context = MigrationContext.configure(connection, opts={
        "compare_server_default": True,
        # 旧版内联 REFERENCES 的 ondelete 反射可能丢失，下面用 PRAGMA 校验。
        "include_object": lambda obj, name, kind, reflected, compared: (
            kind != "foreign_key_constraint"
            and not (kind == "table" and name == "schema_migrations")
        ),
    })
    differences = compare_metadata(context, _legacy_metadata())
    triggers = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND tbl_name IN ('conversations', 'messages')"
    ).fetchall()
    foreign_keys = [tuple(row)[2:] for row in connection.exec_driver_sql(
        "PRAGMA foreign_key_list(messages)"
    )]
    if (
        differences or triggers
        or foreign_keys != [("conversations", "conversation_id", "id", "NO ACTION", "CASCADE", "NONE")]
        or inspector.get_foreign_keys("conversations")
        or any(inspector.get_check_constraints(table) for table in ("conversations", "messages"))
    ):
        raise RuntimeError(
            "现有数据库不是已知的旧版结构，请先检查并编写迁移；"
            "若已与初始迁移完全一致，可执行 alembic stamp 0001。"
        )
    # 显式开始 SQLite 事务，使建表、复制、替换可一并回滚。
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    sequence = connection.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name = 'messages'"
    ).scalar()
    _create_tables("_new_conversations", "_new_messages")
    connection.exec_driver_sql(
        "INSERT INTO _new_conversations (id, title, created_at, updated_at) "
        "SELECT id, title, created_at, updated_at FROM conversations"
    )
    connection.exec_driver_sql(
        "INSERT INTO _new_messages (id, conversation_id, role, content, created_at) "
        "SELECT id, conversation_id, role, content, created_at FROM messages"
    )
    # 先删子表，再删父表，始终保持外键开启。
    op.drop_table("messages")
    op.drop_table("conversations")
    op.rename_table("_new_conversations", "conversations")
    op.rename_table("_new_messages", "messages")
    if sequence is not None:
        connection.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'messages'",
            (sequence,),
        )


def upgrade() -> None:
    if not op.get_context().as_sql and {
        "conversations", "messages"
    } & set(sa.inspect(op.get_bind()).get_table_names()):
        _upgrade_legacy_database(op.get_bind())
    else:
        _create_tables()
    op.create_index("idx_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_index("idx_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
