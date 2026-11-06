"""Fixed code checks. These run before the AI and the AI can never overrule them."""
from . import db


def run(req, policy, pid):
    """Returns (decision, reasons). decision is 'block', 'ask' or 'ok'. pid is this payment's own row."""
    block, ask = [], []
    merchant = req["merchant"].lower()
    amount = req["amount"]

    if db.get("frozen"):
        block.append("All agent payments are frozen (kill switch is on).")
    if not db.get("vault_id"):
        block.append("No saved PayPal account yet.")
    if req["currency"] != policy["currency"]:
        block.append(f"Currency {req['currency']} is not allowed, only {policy['currency']}.")
    if merchant in policy["blocked_merchants"]:
        block.append(f"{req['merchant']} is on your blocked list.")
    if policy["allowed_merchants"] and merchant not in policy["allowed_merchants"]:
        block.append(f"{req['merchant']} is not on your allowed store list.")
    if req["category"].lower() not in policy["allowed_categories"]:
        block.append(f"Category '{req['category']}' is not allowed.")
    if amount > policy["per_payment_limit"]:
        block.append(f"${amount:.2f} is over your ${policy['per_payment_limit']:.2f} per-payment limit.")
    spent = db.spent_this_month(before_id=pid)
    if spent + amount > policy["monthly_budget"]:
        block.append(f"This would take you to ${spent + amount:.2f}, over your ${policy['monthly_budget']:.2f} monthly budget.")
    if req.get("quoted_price") is not None and amount > req["quoted_price"]:
        block.append(f"Price went up from the quoted ${req['quoted_price']:.2f} to ${amount:.2f}.")
    dup = db.recent_duplicate(req["agent"], req["merchant"], req["item"], amount, pid)
    if dup:
        block.append(f"Looks like a repeat of payment #{dup} in the last 10 minutes.")

    if amount > policy["ask_above"]:
        ask.append(f"${amount:.2f} is above your ${policy['ask_above']:.2f} 'ask me first' amount.")

    if block:
        return "block", block
    if ask:
        return "ask", ask
    return "ok", []
