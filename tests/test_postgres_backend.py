"""Offline tests for PostgreSQL SQL parameter translation (no DB required)."""
import unittest
from unittest.mock import MagicMock, patch
import sys
import types

# The production dependency is installed from migration/requirements.txt.
# For offline parser tests, provide a stub when psycopg is not installed.
try:
    import psycopg
except ImportError:
    psycopg = types.ModuleType("psycopg")
    psycopg.rows = types.ModuleType("psycopg.rows")
    psycopg.rows.dict_row = object()
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = psycopg.rows

from postgres_backend import qmark_to_pyformat, PGConnection

class SQLTranslationTests(unittest.TestCase):
    def test_bind_markers(self):
        self.assertEqual(qmark_to_pyformat("SELECT * FROM units WHERE id=? AND status=?"),
                         "SELECT * FROM units WHERE id=%s AND status=%s")

    def test_literal_question_marks(self):
        self.assertEqual(qmark_to_pyformat("SELECT '?', \"?\" FROM units WHERE id=?"),
                         "SELECT '?', \"?\" FROM units WHERE id=%s")

    def test_comments(self):
        self.assertEqual(qmark_to_pyformat("SELECT ? -- ? ignored\n, /* ? */ ?"),
                         "SELECT %s -- ? ignored\n, /* ? */ %s")

    def test_escaped_quotes(self):
        self.assertEqual(qmark_to_pyformat("SELECT 'it''s ?' WHERE id=?"),
                         "SELECT 'it''s ?' WHERE id=%s")

    def test_unterminated_literal(self):
        with self.assertRaises(ValueError):
            qmark_to_pyformat("SELECT 'unfinished")

    def test_unsupported_sql_fails_closed(self):
        db = object.__new__(PGConnection)
        db._conn = MagicMock()
        for statement in ("PRAGMA table_info(units)",
                          "INSERT OR IGNORE INTO units(id) VALUES (?)",
                          "BEGIN IMMEDIATE",
                          "INSERT INTO units(id) VALUES (?)"):
            with self.subTest(statement=statement), self.assertRaises(NotImplementedError):
                db.execute(statement, (1,))

    def test_select_uses_bound_parameters(self):
        db = object.__new__(PGConnection)
        db._conn = MagicMock()
        db.execute("SELECT * FROM units WHERE id=?", (12,))
        db._conn.execute.assert_called_once_with(
            "SELECT * FROM units WHERE id=%s", (12,))

if __name__ == "__main__":
    unittest.main()
