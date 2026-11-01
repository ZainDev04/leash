import os
import uuid

import httpx

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")


class PayPalError(Exception):
    pass


def _token():
    r = httpx.post(
        f"{BASE}/v1/oauth2/token",
        auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _call(method, path, body=None):
    headers = {
        "Authorization": f"Bearer {_token()}",
        "PayPal-Request-Id": str(uuid.uuid4()),
        "Prefer": "return=representation",
    }
    r = httpx.request(method, f"{BASE}{path}", headers=headers, json=body, timeout=30)
    if r.status_code >= 400:
        raise PayPalError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _unit(amount, currency, description):
    return [{"amount": {"currency_code": currency, "value": f"{amount:.2f}"}, "description": description[:127]}]


def start_vault(return_url, cancel_url):
    """$1 order that saves the buyer's PayPal. The $1 is voided after approval."""
    order = _call("POST", "/v2/checkout/orders", {
        "intent": "AUTHORIZE",
        "purchase_units": _unit(1, "USD", "Leash: save PayPal for your agents"),
        "payment_source": {"paypal": {
            "attributes": {"vault": {
                "store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT", "customer_type": "CONSUMER"}},
            "experience_context": {"return_url": return_url, "cancel_url": cancel_url, "user_action": "CONTINUE"},
        }},
    })
    return next(l["href"] for l in order["links"] if l["rel"] == "payer-action")


def finish_vault(order_id):
    order = _call("POST", f"/v2/checkout/orders/{order_id}/authorize")
    void(order["purchase_units"][0]["payments"]["authorizations"][0]["id"])
    vault = order["payment_source"]["paypal"]["attributes"]["vault"]
    if vault.get("status") != "VAULTED":
        raise PayPalError(f"Vault not saved: {vault}")
    return vault["id"]


def hold(vault_id, amount, currency, description):
    """Order from the saved PayPal. It comes back already authorized, so the money is on hold."""
    order = _call("POST", "/v2/checkout/orders", {
        "intent": "AUTHORIZE",
        "purchase_units": _unit(amount, currency, description),
        "payment_source": {"paypal": {"vault_id": vault_id}},
    })
    if order["status"] != "COMPLETED":
        order = _call("POST", f"/v2/checkout/orders/{order['id']}/authorize")
    auth = order["purchase_units"][0]["payments"]["authorizations"][0]
    if auth["status"] != "CREATED":
        raise PayPalError(f"Hold failed: {auth['status']}")
    return order["id"], auth["id"]


def capture(auth_id):
    return _call("POST", f"/v2/payments/authorizations/{auth_id}/capture", {})["id"]


def void(auth_id):
    _call("POST", f"/v2/payments/authorizations/{auth_id}/void")
