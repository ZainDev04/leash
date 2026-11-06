import json
import logging
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import ai, checks, db, pages, paypal, security, store, webhooks
from .security import require_agent, require_user, require_user_or_agent

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("leash")

app = FastAPI(title="Leash", docs_url=None, redoc_url=None, openapi_url=None)
db.init()
FRONTEND = Path(__file__).parent.parent / "frontend"
if not security.login_enabled():
    log.warning(f"LEASH_ADMIN_PASSWORD is missing or under {security.MIN_PASSWORD_LENGTH} characters, so nobody can log in.")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; "
       "frame-src https://www.youtube-nocookie.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
PUBLIC_PATHS = ("/", "/robots.txt", "/sitemap.xml", "/site.webmanifest")


def base_url():
    return security.public_url()


@app.middleware("http")
async def around_every_request(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(json.dumps({"request_id": request_id, "path": request.url.path, "error": "unhandled"}))
        response = JSONResponse({"detail": "Something went wrong on the server.", "request_id": request_id}, 500)
    path = request.url.path
    headers = response.headers
    headers["X-Request-ID"] = request_id
    headers["Content-Security-Policy"] = CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if base_url().startswith("https://"):
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if path.startswith("/static/"):
        headers["Cache-Control"] = "public, max-age=3600"
    elif path in PUBLIC_PATHS:
        headers["Cache-Control"] = "no-cache"
    else:
        headers["Cache-Control"] = "no-store"
        headers["X-Robots-Tag"] = "noindex"
    log.info(json.dumps({
        "request_id": request_id, "method": request.method, "path": path, "status": response.status_code,
        "ms": round((time.perf_counter() - started) * 1000), "ip": security.client_ip(request),
    }))
    return response


app.include_router(store.router)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and "text/html" in request.headers.get("accept", ""):
        return page("404.html", 404)
    return JSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)


def page(name, status=200):
    html = (FRONTEND / name).read_text(encoding="utf-8").replace("{{BASE}}", base_url())
    return HTMLResponse(html, status)


class PayRequest(BaseModel):
    merchant: str = Field(min_length=1, max_length=200)
    item: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    amount: float = Field(ge=0.01, le=1_000_000, allow_inf_nan=False)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    quoted_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    task: str = Field(min_length=1, max_length=2000)
    page_text: str = Field(default="", max_length=100_000)
    product_url: str | None = Field(default=None, max_length=2000)

    @field_validator("merchant", "item", "category", "task")
    @classmethod
    def trimmed(cls, value):
        return value.strip()

    @field_validator("amount")
    @classmethod
    def whole_cents(cls, value):
        # PayPal charges whole cents, so the checks must see the amount PayPal will take
        return round(value, 2)


class RulesText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Policy(BaseModel):
    per_payment_limit: float = Field(ge=0, allow_inf_nan=False)
    ask_above: float = Field(ge=0, allow_inf_nan=False)
    monthly_budget: float = Field(ge=0, allow_inf_nan=False)
    allowed_categories: list[str] = Field(max_length=100)
    allowed_merchants: list[str] = Field(default=[], max_length=500)
    blocked_merchants: list[str] = Field(default=[], max_length=500)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")


class Login(BaseModel):
    password: str = Field(max_length=500)


# Pages and search engine files

@app.get("/", include_in_schema=False)
def landing():
    return page("landing.html")


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if security.valid_session(request.cookies.get(security.COOKIE)):
        return RedirectResponse("/app", 303)
    return page("login.html")


@app.get("/app", include_in_schema=False)
def dashboard(request: Request):
    if not security.valid_session(request.cookies.get(security.COOKIE)):
        return RedirectResponse("/login", 303)
    return page("dashboard.html")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(f"User-agent: *\nAllow: /$\nAllow: /static/\nDisallow: /\n\nSitemap: {base_url()}/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"<url><loc>{base_url()}/</loc></url></urlset>\n")
    return Response(xml, media_type="application/xml")


@app.get("/site.webmanifest", include_in_schema=False)
def manifest():
    return JSONResponse({
        "name": "Leash", "short_name": "Leash", "start_url": "/app", "display": "standalone",
        "background_color": "#0b0b0c", "theme_color": "#0b0b0c",
        "icons": [{"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, media_type="application/manifest+json")


# Load balancer checks

@app.get("/health")
def health():
    """The process is up."""
    return {"ok": True}


@app.get("/ready")
def ready():
    """This copy can serve traffic. A load balancer should stop sending it requests when this fails."""
    if not db.reachable():
        return JSONResponse({"ready": False}, 503)
    return {"ready": True}


# Log in and out

@app.post("/login")
def login(body: Login, request: Request):
    security.limit(f"login:{security.client_ip(request)}", 10)
    if not security.password_ok(body.password):
        raise HTTPException(401, "Wrong password.")
    response = JSONResponse({"ok": True})
    response.set_cookie(security.COOKIE, security.make_session(), max_age=security.SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=security.secure_cookies())
    return response


@app.post("/logout", dependencies=[Depends(require_user)])
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(security.COOKIE)
    return response


@app.get("/status", dependencies=[Depends(require_user_or_agent)])
def status():
    return {
        "vault_saved": bool(db.get("vault_id")),
        "frozen": db.get("frozen"),
        "policy": db.get("policy"),
        "spent_this_month": db.spent_this_month(),
    }


# Save PayPal once so agents can pay later with no click

@app.get("/vault/start", dependencies=[Depends(require_user)])
def vault_start():
    order_id, url = paypal.start_vault(f"{base_url()}/vault/return", f"{base_url()}/app")
    db.put("vault_pending", order_id)
    return RedirectResponse(url, 303)


@app.get("/vault/return", dependencies=[Depends(require_user)])
def vault_return(token: str):
    # Only the order this user started may become the saved account
    if token != db.get("vault_pending"):
        raise HTTPException(400, "This PayPal link is not the one you started.")
    try:
        db.put("vault_id", paypal.finish_vault(token))
    except Exception:
        log.exception("vault save failed")
        return RedirectResponse("/app?vault=failed", 303)
    db.put("vault_pending", None)
    return RedirectResponse("/app", 303)


# Rules

@app.post("/policy/parse", dependencies=[Depends(require_user)])
def policy_parse(body: RulesText):
    """Plain-English rules in, draft policy out. Nothing is saved until the user confirms."""
    security.limit("policy-parse", 10)
    return ai.parse_policy(body.text, db.get("policy"))


@app.put("/policy", dependencies=[Depends(require_user)])
def policy_save(body: Policy):
    policy = body.model_dump()
    for key in ("allowed_categories", "allowed_merchants", "blocked_merchants"):
        policy[key] = [s.strip().lower() for s in policy[key]]
    db.put("policy", policy)
    log.info(json.dumps({"event": "policy_changed", "policy": policy}))
    return policy


@app.post("/freeze", dependencies=[Depends(require_user)])
def freeze():
    db.put("frozen", True)
    log.info(json.dumps({"event": "frozen"}))
    return {"frozen": True}


@app.post("/unfreeze", dependencies=[Depends(require_user)])
def unfreeze():
    db.put("frozen", False)
    log.info(json.dumps({"event": "unfrozen"}))
    return {"frozen": False}


# Payments: fixed checks, hold, AI check, then pay / ask / cancel

@app.post("/pay")
def pay(body: PayRequest, agent: str = Depends(require_agent)):
    security.limit(f"pay:{agent}", 30)
    req = {**body.model_dump(), "agent": agent}
    # The row goes in first. Each payment then counts every earlier one, so payments
    # sent at the same moment cannot all see the same unspent budget.
    pid = db.add_payment({**req, "status": "checking", "reasons": "[]"})
    # Read the page before anything else, so every later step uses what the shop really shows
    req["page_text"], read_by_leash, page_block, page_ask = pages.inspect(req)
    db.update_payment(pid, page_text=req["page_text"], page_verified=int(read_by_leash))
    decision, reasons = checks.run(req, db.get("policy"), pid, page_block, page_ask)

    if decision == "block":
        return finish(pid, "blocked", reasons)

    try:
        order_id, auth_id = paypal.hold(
            db.get("vault_id"), req["amount"], req["currency"], f"{req['item']} at {req['merchant']}")
    except Exception:
        log.exception(f"hold failed for payment {pid}")
        return finish(pid, "blocked", ["PayPal could not place the hold, so nothing was paid."])
    db.update_payment(pid, order_id=order_id, authorization_id=auth_id)

    try:
        verdict = ai.check_payment(req)
    except Exception as e:
        verdict = {"verdict": "block", "hidden_instructions": True,
                   "reason": f"AI check failed, so Leash blocked it to be safe ({type(e).__name__})."}
    # The AI blocks outright only for manipulation; anything else it dislikes goes to the user
    if verdict["verdict"] == "block" and not verdict.get("hidden_instructions"):
        verdict["verdict"] = "ask"
    if verdict["verdict"] != "ok":
        reasons.append("AI: " + verdict["reason"])
    elif reasons:
        reasons.append("AI ok: " + verdict["reason"])

    # The AI can only make the decision stricter
    order = {"ok": 0, "ask": 1, "block": 2}
    final = max(decision, verdict["verdict"], key=order.get)

    if final == "block":
        return finish(pid, "blocked", reasons + release(pid, auth_id))
    if final == "ask":
        return finish(pid, "waiting", reasons)
    try:
        capture_id = paypal.capture(auth_id)
    except Exception:
        log.exception(f"capture failed for payment {pid}")
        return finish(pid, "blocked", reasons + ["PayPal could not take the payment, so nothing was paid."] + release(pid, auth_id))
    db.update_payment(pid, capture_id=capture_id)
    return finish(pid, "paid", reasons or ["Passed all your rules and the AI check."])


def release(pid, auth_id):
    """Void a hold. Returns a note for the user only if the void failed."""
    try:
        paypal.void(auth_id)
        return []
    except Exception:
        log.exception(f"void failed for payment {pid}")
        return ["PayPal did not release the hold yet. It will expire by itself and no money was taken."]


def finish(pid, status, reasons):
    db.update_payment(pid, status=status, reasons=json.dumps(reasons))
    return db.payment(pid)


def clear_stuck_payments():
    """A server that stopped mid-payment leaves it on "checking". Close it so it stops using up the budget."""
    for p in db.stuck_payments():
        if db.move_status(p["id"], "checking", "processing"):
            note = release(p["id"], p["authorization_id"]) if p["authorization_id"] else []
            finish(p["id"], "blocked", ["Leash stopped before finishing this payment, so nothing was paid."] + note)


@app.get("/payments", dependencies=[Depends(require_user)])
def payments():
    clear_stuck_payments()
    return db.payments()


@app.get("/audit", dependencies=[Depends(require_user)])
def audit():
    clear_stuck_payments()
    rows = db.audit_log()
    events = {}
    for e in db.paypal_events():
        events.setdefault(e["payment_id"], []).append(
            {"label": webhooks.LABELS.get(e["event_type"], e["event_type"]), "received_at": e["received_at"]})
    return [{**row, "paypal_events": events.get(row["id"], [])} for row in rows]


@app.get("/audit", dependencies=[Depends(require_user)])
def audit():
    clear_stuck_payments()
    return db.audit_log()


@app.get("/payments/{pid}", dependencies=[Depends(require_user_or_agent)])
def payment(pid: int):
    return db.payment(pid) or not_found()


@app.post("/payments/{pid}/approve", dependencies=[Depends(require_user)])
def approve(pid: int):
    p = claim(pid)
    try:
        capture_id = paypal.capture(p["authorization_id"])
    except Exception:
        log.exception(f"capture failed for payment {pid}")
        db.update_payment(pid, status="waiting")
        raise HTTPException(502, "PayPal could not take the payment. It is still waiting, so you can try again.")
    db.update_payment(pid, capture_id=capture_id)
    return finish(pid, "paid", p["reasons"] + ["You approved it."])


@app.post("/payments/{pid}/reject", dependencies=[Depends(require_user)])
def reject(pid: int):
    p = claim(pid)
    try:
        paypal.void(p["authorization_id"])
    except Exception:
        log.exception(f"void failed for payment {pid}")
        db.update_payment(pid, status="waiting")
        raise HTTPException(502, "PayPal could not release the hold. It is still waiting, so you can try again.")
    return finish(pid, "cancelled", p["reasons"] + ["You rejected it. The hold was released."])


@app.post("/payments/{pid}/refund", dependencies=[Depends(require_user)])
def refund(pid: int):
    p = db.payment(pid) or not_found()
    # Only one click can win, so a double click cannot refund twice
    if not db.move_status(pid, "paid", "processing"):
        raise HTTPException(409, f"Payment is {db.payment(pid)['status']}, so it cannot be refunded.")
    try:
        refund_id = paypal.refund(p["capture_id"])
    except Exception:
        log.exception(f"refund failed for payment {pid}")
        db.update_payment(pid, status="paid")
        raise HTTPException(502, "PayPal could not refund this payment. It is still paid, so you can try again.")
    db.update_payment(pid, refund_id=refund_id)
    return finish(pid, "refunded", p["reasons"] + ["You refunded it. The money went back and your budget was restored."])


# PayPal tells Leash what happened to a payment. Open to the internet, so nothing is believed until PayPal
# confirms the signature.

MAX_WEBHOOK_BYTES = 256_000


@app.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    security.limit(f"webhook:{security.client_ip(request)}", 120)
    if not paypal.webhook_id():
        raise HTTPException(503, "Webhooks are not set up on this server.")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Too large.")
    try:
        text = raw.decode("utf-8")
        event = json.loads(text)
        if not isinstance(event, dict) or not event.get("id") or not event.get("event_type"):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Not a PayPal event.")
    try:
        genuine = await run_in_threadpool(paypal.verify_webhook, request.headers, text)
    except Exception:
        log.exception("webhook signature check failed")
        genuine = False
    if not genuine:
        raise HTTPException(400, "Signature not confirmed by PayPal.")
    await run_in_threadpool(webhooks.handle, event)
    return {"ok": True}


def claim(pid):
    """Only one click can win, so a double click or two open tabs cannot pay twice."""
    p = db.payment(pid) or not_found()
    if not db.move_status(pid, "waiting", "processing"):
        current = db.payment(pid)["status"]
        raise HTTPException(409, f"Payment is {current}, not waiting for approval.")
    return p


def not_found():
    raise HTTPException(404, "No such payment.")
