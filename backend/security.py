"""Who may call what.

People log in to the dashboard with a password and get a signed cookie. Agents send an API key.
A key only unlocks the agent endpoints, so an agent can never approve its own payment.
Nothing is kept in memory, so any number of server copies can answer any request.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from . import db

COOKIE = "leash_session"
SESSION_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 12


def _password():
    password = os.getenv("LEASH_ADMIN_PASSWORD", "")
    return password if len(password) >= MIN_PASSWORD_LENGTH else ""


def _sign(text):
    # Changing the password signs everyone out
    key = hashlib.sha256(b"leash-session:" + _password().encode()).digest()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def login_enabled():
    return bool(_password())


def password_ok(given):
    password = _password()
    return bool(password) and hmac.compare_digest(given.encode(), password.encode())


def make_session():
    expires = str(int(time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}"


def valid_session(value):
    if not value or not _password() or "." not in value:
        return False
    expires, signature = value.split(".", 1)
    return (hmac.compare_digest(signature.encode(), _sign(expires).encode())
            and expires.isdigit() and int(expires) > time.time())


def secure_cookies():
    return os.getenv("PUBLIC_URL", "").startswith("https://")


def agent_for_key(given):
    """LEASH_AGENT_KEYS looks like claude:key1,demo-shopper:key2. The name comes from the key, not the request."""
    match = None
    for pair in os.getenv("LEASH_AGENT_KEYS", "").split(","):
        name, _, key = pair.strip().partition(":")
        if name and key and hmac.compare_digest(given.encode(), key.encode()):
            match = name
    return match


def _agent(request):
    scheme, _, key = request.headers.get("authorization", "").partition(" ")
    return agent_for_key(key.strip()) if scheme.lower() == "bearer" and key else None


def _is_user(request):
    return valid_session(request.cookies.get(COOKIE))


def require_user(request: Request):
    """Dashboard only. The origin check stops another website from sending the user's cookie with a change."""
    if not _is_user(request):
        raise HTTPException(401, "Log in to the dashboard first.")
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "This request came from another website.")


def require_agent(request: Request):
    name = _agent(request)
    if not name:
        raise HTTPException(401, "A valid agent API key is required.")
    return name


def require_user_or_agent(request: Request):
    if not _is_user(request) and not _agent(request):
        raise HTTPException(401, "Log in or send a valid agent API key.")


def client_ip(request):
    return request.client.host if request.client else "unknown"


def limit(key, max_hits, seconds=60):
    """Counted in the database, so the limit holds across server copies."""
    if db.count_hit(key, seconds) > max_hits:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.", {"Retry-After": str(seconds)})
