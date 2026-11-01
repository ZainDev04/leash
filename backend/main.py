import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from . import ai, checks, db, paypal

app = FastAPI(title="Leash")
db.init()
FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


class PayRequest(BaseModel):
    agent: str
    merchant: str
    item: str
    category: str
    amount: float
    currency: str = "USD"
    quoted_price: float | None = None
    task: str
    page_text: str = ""


class RulesText(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def home():
    return FileResponse(FRONTEND)


@app.get("/status")
def status():
    return {
        "vault_saved": bool(db.get("vault_id")),
        "frozen": db.get("frozen"),
        "policy": db.get("policy"),
        "spent_this_month": db.spent_this_month(),
    }


# Save PayPal once so agents can pay later with no click

@app.get("/vault/start")
def vault_start():
    return RedirectResponse(paypal.start_vault(
        "http://localhost:8000/vault/return", "http://localhost:8000/"))


@app.get("/vault/return")
def vault_return(token: str):
    db.put("vault_id", paypal.finish_vault(token))
    return RedirectResponse("/")


# Rules

@app.post("/policy/parse")
def policy_parse(body: RulesText):
    """Plain-English rules in, draft policy out. Nothing is saved until the user confirms."""
    return ai.parse_policy(body.text, db.get("policy"))


@app.put("/policy")
def policy_save(policy: dict):
    policy.pop("summary", None)
    policy.setdefault("currency", "USD")
    for key in ("allowed_categories", "allowed_merchants", "blocked_merchants"):
        policy[key] = [s.lower() for s in policy.get(key, [])]
    db.put("policy", policy)
    return policy


@app.post("/freeze")
def freeze():
    db.put("frozen", True)
    return {"frozen": True}


@app.post("/unfreeze")
def unfreeze():
    db.put("frozen", False)
    return {"frozen": False}


# Payments: fixed checks, hold, AI check, then pay / ask / cancel

@app.post("/pay")
def pay(body: PayRequest):
    req = body.model_dump()
    decision, reasons = checks.run(req, db.get("policy"))
    pid = db.add_payment({**req, "status": "checking", "reasons": "[]"})

    if decision == "block":
        return finish(pid, "blocked", reasons)

    try:
        order_id, auth_id = paypal.hold(
            db.get("vault_id"), req["amount"], req["currency"], f"{req['item']} at {req['merchant']}")
    except Exception as e:
        return finish(pid, "blocked", [f"PayPal hold failed, so nothing was paid: {e}"])
    db.update_payment(pid, order_id=order_id, authorization_id=auth_id)

    try:
        verdict = ai.check_payment(req)
    except Exception as e:
        verdict = {"verdict": "block", "reason": f"AI check failed, so Leash blocked it to be safe ({type(e).__name__})."}
    if verdict["verdict"] != "ok" or reasons:
        reasons.append("AI: " + verdict["reason"])

    # The AI can only make the decision stricter
    order = {"ok": 0, "ask": 1, "block": 2}
    final = max(decision, verdict["verdict"], key=order.get)

    if final == "block":
        paypal.void(auth_id)
        return finish(pid, "blocked", reasons)
    if final == "ask":
        return finish(pid, "waiting", reasons)
    capture_id = paypal.capture(auth_id)
    db.update_payment(pid, capture_id=capture_id)
    return finish(pid, "paid", reasons or ["Passed all your rules and the AI check."])


def finish(pid, status, reasons):
    db.update_payment(pid, status=status, reasons=json.dumps(reasons))
    return db.payment(pid)


@app.get("/payments")
def payments():
    return db.payments()


@app.get("/payments/{pid}")
def payment(pid: int):
    return db.payment(pid) or not_found()


@app.post("/payments/{pid}/approve")
def approve(pid: int):
    p = waiting(pid)
    capture_id = paypal.capture(p["authorization_id"])
    db.update_payment(pid, capture_id=capture_id)
    return finish(pid, "paid", p["reasons"] + ["You approved it."])


@app.post("/payments/{pid}/reject")
def reject(pid: int):
    p = waiting(pid)
    paypal.void(p["authorization_id"])
    return finish(pid, "cancelled", p["reasons"] + ["You rejected it. The hold was released."])


def waiting(pid):
    p = db.payment(pid) or not_found()
    if p["status"] != "waiting":
        raise HTTPException(409, f"Payment is {p['status']}, not waiting for approval.")
    return p


def not_found():
    raise HTTPException(404, "No such payment.")
