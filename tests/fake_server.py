"""The real app with a fake PayPal and Gemini, for running several copies as separate processes."""
import time
import uuid

from backend import ai, main, paypal


def slow_hold(vault_id, amount, currency, description):
    # Slow enough that parallel payments overlap
    time.sleep(0.2)
    return uuid.uuid4().hex, uuid.uuid4().hex


paypal.hold = slow_hold
paypal.capture = lambda auth_id: uuid.uuid4().hex
paypal.void = lambda auth_id: None
ai.check_payment = lambda req: {"verdict": "ok", "hidden_instructions": False, "reason": "Fine."}

app = main.app
