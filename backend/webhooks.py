"""PayPal webhooks: what happened to a payment after Leash's own call returned.

Nothing here is trusted until PayPal has confirmed the signature. PayPal can send an event more than
once, so each event ID is handled one time only.
"""
import json
import re

from . import db

# Shown in the audit log
LABELS = {
    "PAYMENT.AUTHORIZATION.CREATED": "Hold placed",
    "PAYMENT.AUTHORIZATION.VOIDED": "Hold released",
    "PAYMENT.CAPTURE.COMPLETED": "Payment taken",
    "PAYMENT.CAPTURE.DENIED": "Payment denied",
    "PAYMENT.CAPTURE.REFUNDED": "Refunded",
    "PAYMENT.CAPTURE.REVERSED": "Reversed",
    "VAULT.PAYMENT-TOKEN.CREATED": "PayPal account saved",
    "VAULT.PAYMENT-TOKEN.DELETED": "Saved PayPal account removed",
}
EVENT_TYPES = list(LABELS)


def _up_link_id(resource, kind):
    """The capture or authorization a PayPal resource belongs to, from its "up" link."""
    for link in resource.get("links") or []:
        if link.get("rel") == "up":
            found = re.search(rf"/{kind}/([^/?]+)", link.get("href", ""))
            if found:
                return found.group(1)
    return None


def _find_payment(event_type, resource):
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    resource_id = resource.get("id")
    if event_type.startswith("PAYMENT.AUTHORIZATION"):
        ids = {"authorization_id": resource_id, "order_id": related.get("order_id")}
    elif event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        ids = {"capture_id": _up_link_id(resource, "captures") or related.get("capture_id"),
               "authorization_id": related.get("authorization_id"), "order_id": related.get("order_id")}
    else:
        # The event can arrive before Leash has stored the capture ID, so the hold and order IDs count too
        ids = {"capture_id": resource_id, "authorization_id": related.get("authorization_id") or _up_link_id(resource, "authorizations"),
               "order_id": related.get("order_id")}
    return db.payment_by_paypal_ids({k: v for k, v in ids.items() if v})


def _add_reason(pid, text):
    p = db.payment(pid)
    db.update_payment(pid, reasons=json.dumps(p["reasons"] + [text]))


def _apply(event_type, resource, payment):
    """Change Leash's record only where PayPal reports something Leash did not do itself."""
    if event_type == "VAULT.PAYMENT-TOKEN.DELETED":
        if resource.get("id") and resource["id"] == db.get("vault_id"):
            db.put("vault_id", None)
        return
    if not payment:
        return
    pid = payment["id"]
    if event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        if db.move_status(pid, "paid", "refunded"):
            db.update_payment(pid, refund_id=resource.get("id"))
            _add_reason(pid, f"PayPal reports this payment was {LABELS[event_type].lower()}, so it no longer counts against your budget.")
    elif event_type == "PAYMENT.CAPTURE.DENIED":
        if db.move_status(pid, "paid", "blocked"):
            _add_reason(pid, "PayPal denied this payment after Leash sent it, so no money was taken.")
    elif event_type == "PAYMENT.AUTHORIZATION.VOIDED":
        if db.move_status(pid, "waiting", "cancelled"):
            _add_reason(pid, "The hold was released in PayPal, so this payment can no longer be approved.")


def handle(event):
    """Record one verified event. Returns False if it was seen before."""
    event_type = event.get("event_type", "")
    resource = event.get("resource") or {}
    payment = _find_payment(event_type, resource) if event_type.startswith("PAYMENT.") else None
    is_new = db.add_paypal_event(event["id"], event_type, resource.get("id"), payment["id"] if payment else None)
    if is_new:
        _apply(event_type, resource, payment)
    return is_new
