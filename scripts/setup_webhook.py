"""Register this site's webhook with the PayPal sandbox and print the ID for PAYPAL_WEBHOOK_ID.

Run from the repo root with the PayPal keys in .env. Safe to run again: an existing webhook for the
same address is reused.
  python scripts/setup_webhook.py https://your-site.example
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.webhooks import EVENT_TYPES  # noqa: E402

load_dotenv()
API = "https://api-m.sandbox.paypal.com"

if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
    sys.exit("Give the site's https address, for example: python scripts/setup_webhook.py https://your-site.example")
url = sys.argv[1].rstrip("/") + "/paypal/webhook"

token = httpx.post(f"{API}/v1/oauth2/token", data={"grant_type": "client_credentials"}, timeout=30,
                   auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])).raise_for_status().json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

existing = httpx.get(f"{API}/v1/notifications/webhooks", headers=headers, timeout=30).raise_for_status().json()["webhooks"]
match = next((w for w in existing if w["url"] == url), None)
if match:
    print("Already registered.")
    print(match["id"])
else:
    body = {"url": url, "event_types": [{"name": name} for name in EVENT_TYPES]}
    created = httpx.post(f"{API}/v1/notifications/webhooks", headers=headers, json=body, timeout=30).raise_for_status().json()
    print("Registered.")
    print(created["id"])
