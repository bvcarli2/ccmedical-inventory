"""Staging-only SQLite -> PostgreSQL data migration.

Requires: pip install sqlalchemy "psycopg[binary]"
Never use this script against a production PostgreSQL database. It creates
tables and copies data into a fresh, empty PostgreSQL staging database.
"""
import argparse
import json
import os
from pathlib import Path
from sqlalchemy import create_engine, MetaData, inspect, select, func, text
from sqlalchemy.engine import URL


def migrate(sqlite_path, pg_url, dry_run=True):
    source = Path(sqlite_path).resolve()
    if not source.is_file():
        raise ValueError("SQLite backup copy does not exist")
    if not pg_url.startswith(("postgresql+psycopg://", "postgresql://")):
        raise ValueError("Expected PostgreSQL connection URL")
    sqlite_engine = create_engine(URL.create("sqlite", database=str(source)))
    pg_engine = create_engine(pg_url)
    try:
        with sqlite_engine.connect() as src:
            result = src.exec_driver_sql("PRAGMA integrity_check").scalar_one()
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {result}")
            metadata = MetaData()
            metadata.reflect(bind=src)
            tables = [t for t in metadata.sorted_tables if not t.name.startswith("sqlite_")]
            counts = {t.name: src.execute(select(func.count()).select_from(t)).scalar_one() for t in tables}
        if dry_run:
            return {"mode": "dry-run", "table_counts": counts, "total_rows": sum(counts.values())}

        # The destination must be empty. Refuse to overwrite any tables.
        with pg_engine.connect() as dst:
            existing = inspect(dst).get_table_names(schema="public")
            if existing:
                raise RuntimeError("PostgreSQL staging database is not empty; refusing to overwrite it")

        # One PostgreSQL transaction: either all schema/data is committed, or none.
        with pg_engine.begin() as dst, sqlite_engine.connect() as src:
            metadata.create_all(dst)
            for table in tables:
                for row in src.execute(select(table)).mappings():
                    dst.execute(table.insert().values(**dict(row)))
                copied = dst.execute(select(func.count()).select_from(table)).scalar_one()
                if copied != counts[table.name]:
                    raise RuntimeError(f"Row-count mismatch for {table.name}: {copied} vs {counts[table.name]}")

            # SQLite explicit integer IDs may bypass PostgreSQL identity sequences.
            # Reset any generated serial/identity sequences to the maximum copied ID.
            for table in tables:
                for column in table.primary_key.columns:
                    if not column.autoincrement or not column.type.python_type is int:
                        continue
                    qualified = f"public.{table.name}"
                    seq = dst.execute(
                        text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                        {"table_name": qualified, "column_name": column.name},
                    ).scalar_one_or_none()
                    if seq:
                        # Sequence name comes from PostgreSQL, not user input.
                        dst.execute(
                            text("SELECT setval(CAST(:sequence AS regclass), "
                                 "COALESCE((SELECT MAX(" +
                                 '"' + column.name.replace('"', '""') + '"' +
                                 ") FROM " + '"' + table.name.replace('"', '""') + '"' +
                                 "), 1), " +
                                 "EXISTS(SELECT 1 FROM " + '"' + table.name.replace('"', '""') + '"' +
                                 "))"),
                            {"sequence": seq},
                        )
        return {"mode": "migrated", "table_counts": counts, "total_rows": sum(counts.values())}
    finally:
        sqlite_engine.dispose()
        pg_engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sqlite_copy", help="Path to an offline SQLite backup COPY")
    p.add_argument("--apply", action="store_true", help="Actually write to empty staging PostgreSQL")
    args = p.parse_args()
    url = os.environ.get("CCM_STAGING_DATABASE_URL")
    if not url:
        p.error("Set CCM_STAGING_DATABASE_URL in your private local environment")
    print(json.dumps(migrate(args.sqlite_copy, url, dry_run=not args.apply), indent=2))
