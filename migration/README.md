# CC Medical: SQLite to PostgreSQL migration plan (staging)

**Status: preparation only. Not ready for Render deployment.**

The existing Flask application uses SQLite-specific operations throughout
`app.py`, `product_database.py`, and `employee_auth.py`. Merely setting
`DATABASE_URL` or copying tables to PostgreSQL will **not** make the app work.

## 1. Protect the original inventory

1. Stop the local desktop app before copying `inventory.db`.
2. Make a private backup outside GitHub and verify that it can be opened.
3. Do not upload the database, customer records, or database connection URL
   to GitHub or ChatGPT.
4. Run `python migration/sqlite_preflight.py /path/to/backup-copy.db`
   locally. This checks integrity and reports table row counts without
   changing data. Record the counts privately.

## 2. Database conversion (not yet implemented)

- Build PostgreSQL DDL with explicit primary keys, foreign keys, uniqueness,
  indexes, and case-insensitive RMA behavior.
- Convert SQLite `INTEGER PRIMARY KEY` and `AUTOINCREMENT` sequences.
- Migrate existing data transactionally into a *staging* PostgreSQL database.
- Reset PostgreSQL identity sequences after copying explicit IDs.
- Compare per-table row counts and sample records before cutover.
- Do not rely on the Render free PostgreSQL instance for permanent records;
  it expires and is not a production backup strategy.

## 3. Flask compatibility work (not yet implemented)

- Replace `sqlite3.connect` with PostgreSQL-backed database access.
- Replace SQLite-only `PRAGMA`, `executescript`, `INSERT OR IGNORE`,
  `SELECT changes()`, `lastrowid`, and `COLLATE NOCASE` behavior.
- Ensure schema initialization is explicit, versioned, and safe across
  concurrent requests.
- Use transactions and locking for the shared CC/RP/CR inventory ID sequence.
- Add tests for inventory receiving, transfers, repair history, products,
  costs, and simultaneous employee actions.

## 4. Security and deployment (not yet implemented)

- Implement employee authorization per route and CSRF protection for writes.
- Require a strong secret, HTTPS cookies, and protected bootstrap accounts.
- Verify no credentials or customer data are tracked in Git history.
- Deploy to a private test environment only after migration and security
  checks pass. Never point an unreviewed build at production data.

## 5. PostgreSQL backend integration progress (October 22, 2026)

- `postgres_backend.py` provides an initial psycopg connection wrapper and
  quoted-literal-aware `?` to `%s` bind translation.
- `tests/test_postgres_backend.py` contains offline unit tests for translation
  and explicitly unsupported operations. Tests have not been executed in CI.
- The adapter deliberately refuses SQLite-specific commands and INSERTs without
  `RETURNING`; it is not wired into `app.py` yet.
- Next: PostgreSQL DDL, query-by-query conversion in `app.py` and
  `product_database.py`, then integration tests against an isolated test DB.
- Render deployment must remain blocked until this work and security checks
  are complete.
