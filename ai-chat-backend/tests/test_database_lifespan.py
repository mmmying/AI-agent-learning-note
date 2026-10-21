import unittest
from unittest.mock import MagicMock, patch

import main
from db.database import database as shared_database
from db.dependencies import DatabaseDep
from fastapi import FastAPI
from fastapi.testclient import TestClient
from main import lifespan


class DatabaseLifespanTests(unittest.TestCase):
    def test_main_uses_module_shared_database(self):
        self.assertIs(main.database, shared_database)

    def test_requests_share_database_and_shutdown_disposes_engine(self):
        database = MagicMock()
        app = FastAPI(lifespan=lifespan)

        @app.get("/database")
        def database_identity(db: DatabaseDep):
            self.assertIs(db, database)
            return {"shared": True}

        with patch("main.database", database):
            with TestClient(app) as client:
                self.assertIs(app.state.database, database)
                database.init_tables.assert_called_once_with()
                for _ in range(2):
                    self.assertEqual(client.get("/database").json(), {"shared": True})
                database.engine.dispose.assert_not_called()
            database.engine.dispose.assert_called_once_with()
            self.assertFalse(hasattr(app.state, "database"))

    def test_initialization_failure_disposes_engine(self):
        database = MagicMock()
        database.init_tables.side_effect = RuntimeError("initialization failed")
        app = FastAPI(lifespan=lifespan)
        with patch("main.database", database):
            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                with TestClient(app):
                    pass
        database.engine.dispose.assert_called_once_with()
        self.assertFalse(hasattr(app.state, "database"))
