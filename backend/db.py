import json
import os
import sqlite3
import time
from pathlib import Path

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


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            agent TEXT, merchant TEXT, item TEXT, category TEXT,
            amount REAL, quoted_price REAL, currency TEXT,
            task TEXT, page_text TEXT,
            status TEXT, reasons TEXT,
            order_id TEXT, authorization_id TEXT, capture_id TEXT
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT, window INTEGER, hits INTEGER,
            PRIMARY KEY (key, window)
        );
        """)
    if get("policy") is None:
        put("policy", DEFAULT_POLICY)
    if get("frozen") is None:
        put("frozen", False)


def get(key):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def put(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def add_payment(p):
    cols = ", ".join(p)
    marks = ", ".join("?" for _ in p)
    with conn() as c:
        return c.execute(f"INSERT INTO payments ({cols}) VALUES ({marks})", list(p.values())).lastrowid


def update_payment(pid, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE payments SET {sets} WHERE id = ?", [*fields.values(), pid])


def payment(pid):
    with conn() as c:
        row = c.execute("SELECT * FROM payments WHERE id = ?", (pid,)).fetchone()
    return to_dict(row) if row else None


def payments(limit=100):
    with conn() as c:
        rows = c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [to_dict(r) for r in rows]


def move_status(pid, old, new):
    """Change status only if it is still `old`. Returns True for the one caller that won."""
    with conn() as c:
        return c.execute("UPDATE payments SET status = ? WHERE id = ? AND status = ?", (new, pid, old)).rowcount == 1


def spent_this_month(before_id=None):
    """Total this month. With before_id, only payments that came before that one."""
    with conn() as c:
        row = c.execute(f"""SELECT COALESCE(SUM(amount), 0) AS s FROM payments
            WHERE status IN {COUNTED} AND created_at >= date('now', 'start of month')
            AND (? IS NULL OR id < ?)""", (before_id, before_id)).fetchone()
    return row["s"]


def recent_duplicate(agent, merchant, item, amount, before_id, minutes=10):
    with conn() as c:
        row = c.execute(f"""SELECT id FROM payments
            WHERE agent = ? AND lower(merchant) = lower(?) AND lower(item) = lower(?) AND amount = ?
            AND id < ? AND status IN {COUNTED} AND created_at >= datetime('now', ?)""",
            (agent, merchant, item, amount, before_id, f"-{minutes} minutes")).fetchone()
    return row["id"] if row else None


def count_hit(key, seconds):
    """Add one hit to this time window and return the total so far."""
    window = int(time.time()) // seconds
    with conn() as c:
        c.execute("DELETE FROM rate_limits WHERE window < ?", (window - 1,))
        c.execute("""INSERT INTO rate_limits (key, window, hits) VALUES (?, ?, 1)
            ON CONFLICT (key, window) DO UPDATE SET hits = hits + 1""", (key, window))
        return c.execute("SELECT hits FROM rate_limits WHERE key = ? AND window = ?", (key, window)).fetchone()["hits"]


def reachable():
    try:
        with conn() as c:
            c.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


def to_dict(row):
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"] or "[]")
    return d
