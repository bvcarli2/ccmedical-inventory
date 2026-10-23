"""PostgreSQL DB-API adapter for the CC Medical Flask application.

This is a compatibility layer, NOT a completed production migration.
The application still requires PostgreSQL DDL, SQLite query rewrites,
transaction review, CSRF, and role authorization before cloud deployment.
"""
import re
import psycopg
from psycopg.rows import dict_row

def qmark_to_pyformat(sql):
    """Translate SQLite ? bind markers without modifying quoted literals.

    Supports single/double-quoted strings and SQL line/block comments.
    PostgreSQL-specific dollar-quoted strings are intentionally unsupported.
    """
    out = []
    i = 0
    state = "normal"
    while i < len(sql):
        c = sql[i]
        n = sql[i + 1] if i + 1 < len(sql) else ""
        if state == "normal":
            if c == "'":
                state = "single"
            elif c == '"':
                state = "double"
            elif c == "-" and n == "-":
                state = "line"
                out.extend((c, n)); i += 2; continue
            elif c == "/" and n == "*":
                state = "block"
                out.extend((c, n)); i += 2; continue
            elif c == "?":
                out.append("%s"); i += 1; continue
        elif state == "single":
            if c == "'" and n == "'":
                out.extend((c, n)); i += 2; continue
            if c == "'":
                state = "normal"
        elif state == "double":
            if c == '"' and n == '"':
                out.extend((c, n)); i += 2; continue
            if c == '"':
                state = "normal"
        elif state == "line" and c == "\n":
            state = "normal"
        elif state == "block" and c == "*" and n == "/":
            out.extend((c, n)); i += 2; state = "normal"; continue
        out.append(c)
        i += 1
    if state in ("single", "double", "block"):
        raise ValueError("Unterminated SQL string or block comment")
    return "".join(out)

class PGConnection:
    def __init__(self, url, strict_inserts=True):
        self._conn = psycopg.connect(url, row_factory=dict_row)
        self._lastrowid = None
        self._strict_inserts = strict_inserts

    def execute(self, sql, params=()):
        statement = sql.strip()
        # Fail explicitly rather than accidentally executing SQLite-specific SQL.
        if re.search(r"\b(PRAGMA|AUTOINCREMENT)\b|\bINSERT\s+OR\s+IGNORE\b|\bBEGIN\s+IMMEDIATE\b|\bCOLLATE\s+NOCASE\b", statement, re.I):
            raise NotImplementedError("SQLite-specific SQL must be migrated explicitly")
        if self._strict_inserts and re.search(r"\bINSERT\s+INTO\b", statement, re.I) and not re.search(r"\bRETURNING\b", statement, re.I):
            raise NotImplementedError("INSERT statements must use RETURNING id or explicit conflict handling on PostgreSQL")
        cursor = self._conn.execute(qmark_to_pyformat(statement), params)
        return cursor

    def executescript(self, script):
        raise NotImplementedError("Use versioned PostgreSQL migrations; SQLite executescript is unsupported")

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)
