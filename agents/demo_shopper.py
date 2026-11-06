"""Scripted demo agent. Sends four purchases from the demo store to Leash so you can watch the dashboard react.
For a real AI agent see shopper.py.

Start the backend first, then run from the repo root:
  python agents/demo_shopper.py
"""
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
LEASH = os.getenv("LEASH_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.getenv('LEASH_API_KEY', '')}"}
TASK = "Buy me a beginner Python book"

PURCHASES = [
    ("A normal book that fits every rule", "python-crash-course", "Python Crash Course", 24.99, 24.99),
    ("Over the 'ask me first' amount", "python-box-set", "Python books box set of 4", 64.00, 64.00),
    ("Price went up at checkout", "learning-python", "Learning Python", 29.00, 19.00),
    ("Shop page hides instructions for AI agents", "automate-the-boring-stuff", "Automate the Boring Stuff", 21.50, 21.50),
]

for label, slug, item, amount, quoted in PURCHASES:
    print(f"\n{label}: {item} for ${amount:.2f}")
    r = httpx.post(f"{LEASH}/pay", timeout=120, headers=HEADERS, json={
        "merchant": "BookNook", "item": item, "category": "books", "amount": amount, "quoted_price": quoted,
        "task": TASK, "product_url": f"{LEASH}/store/checkout/{slug}"})
    r.raise_for_status()
    p = r.json()
    print(f"  Leash says: {p['status']}")
    for reason in p["reasons"]:
        print(f"  - {reason}")
    time.sleep(2)
