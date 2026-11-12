import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import bridge


class BridgeSchemaMigrationTest(unittest.TestCase):
    def test_adds_summary_and_routing_columns_to_existing_database(self):
        original_db = bridge.DB
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "old.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    'CREATE TABLE tasks (id TEXT PRIMARY KEY, request TEXT NOT NULL, '
                    'agent TEXT NOT NULL DEFAULT "workbuddy", status TEXT NOT NULL, '
                    'result TEXT, created REAL NOT NULL, updated REAL NOT NULL, '
                    'idem TEXT UNIQUE, display_no INTEGER)'
                )
                connection.commit()
            try:
                bridge.DB = database
                bridge.init()
                with closing(sqlite3.connect(database)) as connection:
                    columns = {row[1] for row in connection.execute('PRAGMA table_info(tasks)')}
                self.assertIn('summary', columns)
                self.assertIn('routing_reason', columns)
            finally:
                bridge.DB = original_db


if __name__ == "__main__":
    unittest.main()
