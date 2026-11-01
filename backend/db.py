import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("leash.db")

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
    c = sqlite3.connect(DB_PATH)
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


def spent_this_month():
    with conn() as c:
        row = c.execute("""SELECT COALESCE(SUM(amount), 0) AS s FROM payments
            WHERE status IN ('paid', 'waiting') AND created_at >= date('now', 'start of month')""").fetchone()
    return row["s"]


def recent_duplicate(agent, merchant, item, amount, minutes=10):
    with conn() as c:
        row = c.execute("""SELECT id FROM payments
            WHERE agent = ? AND merchant = ? AND item = ? AND amount = ?
            AND status IN ('paid', 'waiting') AND created_at >= datetime('now', ?)""",
            (agent, merchant, item, amount, f"-{minutes} minutes")).fetchone()
    return row["id"] if row else None


def to_dict(row):
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"] or "[]")
    return d
