#!/usr/bin/env python3
"""Read-only preflight for a future SQLite -> PostgreSQL migration.

This script never writes to either database. Use a COPY of inventory.db,
not the live desktop file. Do not commit the copy to GitHub.
"""
import argparse
import json
import sqlite3
from pathlib import Path

def inspect(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {}
        for table in tables:
            escaped = table.replace('"', '""')
            counts[table] = db.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
        fks = {}
        for table in tables:
            escaped = table.replace('"', '""')
            fks[table] = len(db.execute(f'PRAGMA foreign_key_list("{escaped}")').fetchall())
        return {"integrity_check": check, "table_counts": counts,
                "foreign_key_constraints": fks,
                "total_rows": sum(counts.values())}
    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_copy", help="Path to a copy of inventory.db")
    args = parser.parse_args()
    report = inspect(args.sqlite_copy)
    print(json.dumps(report, indent=2))
    if report["integrity_check"] != "ok":
        raise SystemExit("SQLite integrity check failed; stop migration.")
