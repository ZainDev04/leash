"""Storage. SQLite for one server on a laptop, Postgres (DATABASE_URL) when several copies share the data.

The SQL is written to run on both. Times are stored as UTC text in one format, so they compare correctly as text.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("LEASH_DB") or Path(__file__).with_name("leash.db")

# Payments that count against the budget. Unfinished ones count, so nothing can slip through while they run.
COUNTED = "('paid', 'waiting', 'checking', 'processing')"

DEFAULT_POLICY = {
    "per_payment_limit": 100,
    "ask_above": 50,
    "monthly_budget": 300,
    "allowed_categories": ["groceries", "books", "electronics", "travel"],
    "allowed_merchants": [],
    "blocked_merchants": [],
    "currency": "USD",
}

AUDIT_COLUMNS = ("id, created_at, agent, merchant, item, category, amount, quoted_price, currency, task, status, "
                 "reasons, order_id, authorization_id, capture_id, refund_id, product_url, page_verified")

_pool = None


def _postgres():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        # check= replaces connections the database closed while they sat idle
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=True,
                               kwargs={"row_factory": dict_row}, check=ConnectionPool.check_connection)
    return _pool


@contextmanager
def conn():
    """One transaction: it commits at the end of the block, or rolls back on an error."""
    if DATABASE_URL:
        with _postgres().connection() as c:
            yield c
        return
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def run(c, sql, params=()):
    return c.execute(sql.replace("?", "%s") if DATABASE_URL else sql, params)


def _lock(c, number):
    """Postgres only: one holder at a time until this transaction ends, across every server copy."""
    if DATABASE_URL:
        run(c, "SELECT pg_advisory_xact_lock(?)", (number,))


def _now(minus_minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minus_minutes)).strftime("%Y-%m-%d %H:%M:%S")


def init():
    id_column = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    number = "DOUBLE PRECISION" if DATABASE_URL else "REAL"
    with conn() as c:
        _lock(c, 1)  # Copies that start together must not create the tables at the same moment
        run(c, "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        run(c, f"""CREATE TABLE IF NOT EXISTS payments (
            id {id_column},
            created_at TEXT,
            agent TEXT, merchant TEXT, item TEXT, category TEXT,
            amount {number}, quoted_price {number}, currency TEXT,
            task TEXT, page_text TEXT,
            status TEXT, reasons TEXT,
            order_id TEXT, authorization_id TEXT, capture_id TEXT,
            product_url TEXT, page_verified INTEGER DEFAULT 0, refund_id TEXT
        )""")
        run(c, """CREATE TABLE IF NOT EXISTS paypal_events (
            event_id TEXT PRIMARY KEY, event_type TEXT, resource_id TEXT, payment_id BIGINT, received_at TEXT
        )""")
        run(c, "CREATE TABLE IF NOT EXISTS rate_hits (key TEXT, slot BIGINT, hits INTEGER, PRIMARY KEY (key, slot))")
        if DATABASE_URL:
            run(c, "ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_id TEXT")
        if not DATABASE_URL:
            # Databases made by earlier versions of Leash
            run(c, "DROP TABLE IF EXISTS rate_limits")
            columns = [row["name"] for row in run(c, "PRAGMA table_info(payments)")]
            if "product_url" not in columns:
                run(c, "ALTER TABLE payments ADD COLUMN product_url TEXT")
                run(c, "ALTER TABLE payments ADD COLUMN page_verified INTEGER DEFAULT 0")
            if "refund_id" not in columns:
                run(c, "ALTER TABLE payments ADD COLUMN refund_id TEXT")
    if get("policy") is None:
        put("policy", DEFAULT_POLICY)
    if get("frozen") is None:
        put("frozen", False)


def get(key):
    with conn() as c:
        row = run(c, "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def put(key, value):
    with conn() as c:
        run(c, """INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (key, json.dumps(value)))


def add_payment(p):
    p = {**p, "created_at": _now()}
    cols = ", ".join(p)
    marks = ", ".join("?" for _ in p)
    with conn() as c:
        # Postgres hands out ids before commit. With this lock a lower id is always visible to a higher
        # one, which the budget and repeat checks rely on. SQLite already allows one writer at a time.
        _lock(c, 2)
        return run(c, f"INSERT INTO payments ({cols}) VALUES ({marks}) RETURNING id", list(p.values())).fetchone()["id"]


def update_payment(pid, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        run(c, f"UPDATE payments SET {sets} WHERE id = ?", [*fields.values(), pid])


def payment(pid):
    with conn() as c:
        row = run(c, "SELECT * FROM payments WHERE id = ?", (pid,)).fetchone()
    return to_dict(row) if row else None


def payments(limit=100):
    with conn() as c:
        rows = run(c, "SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [to_dict(r) for r in rows]


def audit_log(limit=5000):
    """Every payment for the audit table, newest first. Page text is left out because it can be very long."""
    with conn() as c:
        rows = run(c, f"SELECT {AUDIT_COLUMNS} FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [to_dict(r) for r in rows]


def paypal_events():
    with conn() as c:
        return [dict(r) for r in run(c, "SELECT * FROM paypal_events ORDER BY received_at, event_id").fetchall()]


def add_paypal_event(event_id, event_type, resource_id, payment_id):
    """Returns True the first time this event ID is seen."""
    with conn() as c:
        return run(c, """INSERT INTO paypal_events (event_id, event_type, resource_id, payment_id, received_at)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                   (event_id, event_type, resource_id, payment_id, _now())).fetchone() is not None


def payment_by_paypal_ids(ids):
    """ids maps a column (order_id, authorization_id or capture_id) to the value PayPal sent."""
    allowed = {k: v for k, v in ids.items() if k in ("order_id", "authorization_id", "capture_id")}
    if not allowed:
        return None
    where = " OR ".join(f"{column} = ?" for column in allowed)
    with conn() as c:
        row = run(c, f"SELECT * FROM payments WHERE {where} ORDER BY id DESC LIMIT 1", list(allowed.values())).fetchone()
    return to_dict(row) if row else None


def stuck_payments(minutes=15):
    """Payments still being checked long after any check could have finished: the server stopped halfway."""
    with conn() as c:
        rows = run(c, "SELECT * FROM payments WHERE status = 'checking' AND created_at < ?", (_now(minutes),)).fetchall()
    return [to_dict(r) for r in rows]


def move_status(pid, old, new):
    """Change status only if it is still `old`. Returns True for the one caller that won."""
    with conn() as c:
        return run(c, "UPDATE payments SET status = ? WHERE id = ? AND status = ?", (new, pid, old)).rowcount == 1


def spent_this_month(before_id=None):
    """Total this month. With before_id, only payments that came before that one."""
    params = [_now()[:7] + "-01"]
    earlier = ""
    if before_id is not None:
        earlier = "AND id < ?"
        params.append(before_id)
    with conn() as c:
        row = run(c, f"""SELECT COALESCE(SUM(amount), 0) AS s FROM payments
            WHERE status IN {COUNTED} AND created_at >= ? {earlier}""", params).fetchone()
    return round(row["s"], 2)


def recent_duplicate(agent, merchant, item, amount, before_id, minutes=10):
    with conn() as c:
        row = run(c, f"""SELECT id FROM payments
            WHERE agent = ? AND lower(merchant) = lower(?) AND lower(item) = lower(?) AND amount = ?
            AND id < ? AND status IN {COUNTED} AND created_at >= ?""",
                  (agent, merchant, item, amount, before_id, _now(minutes))).fetchone()
    return row["id"] if row else None


def count_hit(key, seconds):
    """Add one hit to this time window and return the total so far."""
    slot = int(datetime.now(timezone.utc).timestamp()) // seconds
    with conn() as c:
        run(c, "DELETE FROM rate_hits WHERE slot < ?", (slot - 1,))
        return run(c, """INSERT INTO rate_hits (key, slot, hits) VALUES (?, ?, 1)
            ON CONFLICT (key, slot) DO UPDATE SET hits = rate_hits.hits + 1
            RETURNING hits""", (key, slot)).fetchone()["hits"]


def reachable():
    try:
        with conn() as c:
            run(c, "SELECT 1")
        return True
    except Exception:
        return False


def to_dict(row):
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"] or "[]")
    return d
