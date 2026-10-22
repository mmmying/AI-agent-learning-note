import io
import shutil
import sqlite3
import tempfile
import unittest
import pytest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from core.paths import BASE_DIR
from db.database import Database, init_db
from db.migrations import migration_config
from db.orm_models import Base
from sqlalchemy import inspect, text

pytestmark = pytest.mark.database


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "nested" / "chat.db")
        self.db.engine.echo = False

    def tearDown(self):
        self.db.engine.dispose()
        self.temp.cleanup()

    def run_command(self, operation, revision):
        config = migration_config()
        with self.db.engine.begin() as connection:
            config.attributes["connection"] = connection
            operation(config, revision)

    def test_upgrade_matches_models_and_records_revision(self):
        init_db(self.db)
        with self.db.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0001",
            )
            context = MigrationContext.configure(connection)
            self.assertEqual(compare_metadata(context, Base.metadata), [])
            self.assertEqual(connection.scalar(text("PRAGMA foreign_keys")), 1)
            sql = connection.scalar(
                text("SELECT sql FROM sqlite_master WHERE name = 'messages'")
            )
            self.assertIn("AUTOINCREMENT", sql)

    def test_downgrade_and_reupgrade(self):
        init_db(self.db)
        self.run_command(command.downgrade, "base")
        self.assertEqual(inspect(self.db.engine).get_table_names(), ["alembic_version"])
        init_db(self.db)
        self.assertIn("messages", inspect(self.db.engine).get_table_names())

    def test_stamp_adopts_existing_orm_database_without_losing_data(self):
        # 模拟接入 Alembic 前由 create_all 创建的数据库。
        Base.metadata.create_all(self.db.engine)
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                ("old", "历史会话", "2020-01-01", "2020-01-01"),
            )
            connection.exec_driver_sql(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("old", "user", "历史消息", "2020-01-01"),
            )
        self.run_command(command.stamp, "0001")
        init_db(self.db)
        init_db(self.db)
        with self.db.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT content FROM messages")), "历史消息"
            )

    def create_legacy_database(self):
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            connection.executescript(
                (BASE_DIR / "tests/fixtures/legacy_schema.sql").read_text(
                    encoding="utf-8"
                )
            )
            connection.execute(
                "INSERT INTO conversations (id, title) VALUES ('old', '旧标题')"
            )
            connection.execute(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES (42, 'old', 'user', '历史消息')"
            )
            connection.execute(
                "UPDATE sqlite_sequence SET seq = 100 WHERE name = 'messages'"
            )
            connection.execute("INSERT INTO schema_migrations (name) VALUES ('legacy')")

    def test_legacy_upgrade_preserves_data_sequence_and_foreign_keys(self):
        self.create_legacy_database()
        init_db(self.db)
        init_db(self.db)
        with self.db.engine.begin() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT title FROM conversations")), "旧标题"
            )
            self.assertEqual(
                connection.scalar(text("SELECT content FROM messages")), "历史消息"
            )
            self.assertEqual(connection.scalar(text("SELECT id FROM messages")), 42)
            self.assertEqual(
                connection.scalar(text("SELECT name FROM schema_migrations")), "legacy"
            )
            connection.exec_driver_sql(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES ('old', 'assistant', 'new', '2020-01-01')"
            )
            self.assertEqual(
                connection.scalar(text("SELECT max(id) FROM messages")), 101
            )
            connection.exec_driver_sql("DELETE FROM conversations WHERE id = 'old'")
            self.assertEqual(
                connection.scalar(text("SELECT count(*) FROM messages")), 0
            )
        config = migration_config()
        with self.db.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.check(config)

    def test_unknown_legacy_schema_is_rejected_without_data_loss(self):
        self.create_legacy_database()
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN custom TEXT"
            )
        with self.assertRaisesRegex(RuntimeError, "现有数据库不是已知"):
            init_db(self.db)
        with self.db.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT content FROM messages")), "历史消息"
            )
            self.assertIsNone(
                connection.scalar(text("SELECT version_num FROM alembic_version"))
            )

    def test_legacy_copy_failure_rolls_back_table_replacement(self):
        self.create_legacy_database()
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            # 模拟历史库未开启外键时写入的孤立消息。
            connection.execute("UPDATE messages SET conversation_id = 'missing'")
        from sqlalchemy.exc import IntegrityError

        with self.assertRaises(IntegrityError):
            init_db(self.db)
        self.assertNotIn(
            "_new_conversations", inspect(self.db.engine).get_table_names()
        )
        with self.db.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT content FROM messages")), "历史消息"
            )

    def test_cli_environment_uses_configured_database(self):
        config = migration_config()
        # 不传 connection，覆盖 CLI 创建连接的分支。
        with patch("core.config.settings.db_path", self.db.path):
            command.upgrade(config, "head")
            command.check(config)
        with self.db.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0001",
            )

    def test_offline_sql_generation_does_not_create_database(self):
        output = io.StringIO()
        config = migration_config()
        config.output_buffer = output
        with patch("core.config.settings.db_path", self.db.path):
            command.upgrade(config, "head", sql=True)
        self.assertFalse(self.db.path.exists())
        self.assertIn("CREATE TABLE conversations", output.getvalue())
        self.assertIn("CREATE TABLE messages", output.getvalue())

    def test_autogenerate_creates_importable_revision_template(self):
        init_db(self.db)
        # 将生成结果限制在临时目录，不修改项目迁移历史。
        config = migration_config()
        with tempfile.TemporaryDirectory() as generated:
            shutil.copy(
                BASE_DIR / "migrations/versions/0001_initial_schema.py", generated
            )
            config.set_main_option("version_locations", generated)
            with self.db.engine.begin() as connection:
                config.attributes["connection"] = connection
                revision = command.revision(
                    config, message="template smoke test", autogenerate=True
                )
            source = Path(revision.path).read_text(encoding="utf-8")
            compile(source, revision.path, "exec")
            self.assertIn("def upgrade()", source)
            self.assertIn("def downgrade()", source)
