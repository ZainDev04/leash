"""Step 4 spike: save PayPal once (vault), then pay later with no buyer click.

Run from the repo root:
  python docs/spikes/vault_spike.py setup    # creates first order, prints approval link
  python docs/spikes/vault_spike.py finish   # after approving: authorize + capture, saves vault_id
  python docs/spikes/vault_spike.py pay      # new order from vault_id, authorize + capture (no click)
  python docs/spikes/vault_spike.py void     # new order from vault_id, authorize + void
"""
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
STATE = Path(__file__).with_name("vault_state.json")


def token():
    r = httpx.post(
        f"{BASE}/v1/oauth2/token",
        auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def call(method, path, body=None):
    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": str(uuid.uuid4()),
        "Prefer": "return=representation",
    }
    r = httpx.request(method, f"{BASE}{path}", headers=headers, json=body, timeout=30)
    print(method, path, r.status_code)
    if r.status_code >= 400:
        print(json.dumps(r.json(), indent=2))
        sys.exit(1)
    return r.json() if r.content else {}


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(state):
    STATE.write_text(json.dumps(state, indent=2))


def unit(amount):
    return [{"amount": {"currency_code": "USD", "value": amount}, "description": "Leash vault test"}]


def setup():
    order = call("POST", "/v2/checkout/orders", {
        "intent": "AUTHORIZE",
        "purchase_units": unit("1.00"),
        "payment_source": {"paypal": {
            "attributes": {"vault": {
                "store_in_vault": "ON_SUCCESS",
                "usage_type": "MERCHANT",
                "customer_type": "CONSUMER",
            }},
            "experience_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
                "user_action": "CONTINUE",
            },
        }},
    })
    save({"first_order_id": order["id"]})
    link = next(l["href"] for l in order["links"] if l["rel"] == "payer-action")
    print("\nOpen this link, log in with the sandbox PERSONAL account, approve:\n" + link)
    print("\nThen run: python docs/spikes/vault_spike.py finish")


def authorize_and_settle(order_id, settle):
    order = call("POST", f"/v2/checkout/orders/{order_id}/authorize")
    auth = order["purchase_units"][0]["payments"]["authorizations"][0]
    print("authorization", auth["id"], auth["status"])
    if settle == "capture":
        cap = call("POST", f"/v2/payments/authorizations/{auth['id']}/capture", {})
        print("capture", cap["id"], cap["status"])
    else:
        call("POST", f"/v2/payments/authorizations/{auth['id']}/void")
        print("voided", auth["id"])
    return order


def finish():
    state = load()
    order = authorize_and_settle(state["first_order_id"], "capture")
    vault = order.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {})
    print("vault", vault)
    if not vault.get("id"):
        print("No vault_id returned. Vault may not be enabled on this app or account.")
        sys.exit(1)
    state["vault_id"] = vault["id"]
    save(state)
    print("\nSaved vault_id. Now run: pay, then void")


def pay_from_vault(settle):
    vault_id = load()["vault_id"]
    order = call("POST", "/v2/checkout/orders", {
        "intent": "AUTHORIZE",
        "purchase_units": unit("2.50"),
        "payment_source": {"paypal": {"vault_id": vault_id}},
    })
    print("order", order["id"], order["status"])
    authorize_and_settle(order["id"], settle)
    print("\nNo buyer click needed. Step 4 works." if settle == "capture" else "\nVoid works.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    actions = {"setup": setup, "finish": finish,
               "pay": lambda: pay_from_vault("capture"), "void": lambda: pay_from_vault("void")}
    if cmd not in actions:
        print(__doc__)
        sys.exit(1)
    actions[cmd]()
