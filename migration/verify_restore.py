"""Verify a PostgreSQL backup against a restored disposable database.

Use only with disposable CI databases. Never run against a live Render database.
"""
import os
import hashlib
import sys
import psycopg
from psycopg import sql

TABLES = (
    "units", "core_items", "customer_repairs", "customer_repair_history",
    "core_transactions", "activity_log", "products",
)

def snapshot(url):
    with psycopg.connect(url) as conn:
        results = {}
        for table in TABLES:
            exists = conn.execute(
                "SELECT to_regclass(%s) AS name", ("public." + table,)
            ).fetchone()[0]
            if exists is None:
                raise RuntimeError("Missing required table: " + table)
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
            digest = hashlib.sha256()
            records = conn.execute(
                sql.SQL("SELECT row_to_json(t)::text FROM {} AS t ORDER BY row_to_json(t)::text").format(sql.Identifier(table))
            )
            for record in records:
                digest.update(record[0].encode("utf-8"))
                digest.update(b"\\n")
            results[table] = (row[0], digest.hexdigest())
        return results

def main():
    source = os.environ["CCM_TEST_DATABASE_URL"]
    restored = os.environ["CCM_RESTORE_TEST_DATABASE_URL"]
    if source == restored:
        raise RuntimeError("Source and restore database must differ")
    if os.environ.get("CCM_BACKUP_RESTORE_CI") != "1":
        raise RuntimeError("Restore verification is restricted to disposable CI")
    before = snapshot(source)
    after = snapshot(restored)
    if before != after:
        raise RuntimeError("Restored table counts differ: %r != %r" % (before, after))
    with psycopg.connect(restored) as conn:
        broken = conn.execute(
            """SELECT count(*) FROM core_items c
               LEFT JOIN units u ON u.id=c.converted_unit_id
               WHERE c.converted_unit_id IS NOT NULL AND u.id IS NULL"""
        ).fetchone()[0]
        if broken:
            raise RuntimeError("Broken restored core conversion references")
    print("Restore verified:", before)

if __name__ == "__main__":
    main()
