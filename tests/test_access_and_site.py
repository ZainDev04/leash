import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, main, security

from .conftest import AGENT, POLICY, buy

FRONTEND = Path(main.__file__).parent.parent / "frontend"
USER_ONLY = [
    ("get", "/payments"), ("get", "/vault/start"), ("get", "/vault/return?token=x"),
    ("post", "/freeze"), ("post", "/unfreeze"), ("post", "/payments/1/approve"), ("post", "/payments/1/reject"),
    ("post", "/policy/parse"), ("put", "/policy"), ("post", "/logout"),
]


@pytest.mark.parametrize("method, path", USER_ONLY)
def test_user_endpoints_refuse_strangers(client, method, path):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method, path", USER_ONLY)
def test_agent_key_cannot_reach_user_endpoints(client, method, path):
    assert getattr(client, method)(path, headers=AGENT).status_code == 401


def test_agent_cannot_approve_its_own_payment(client, fake):
    p = buy(client, amount=50.0, quoted_price=50.0).json()
    assert client.post(f"/payments/{p['id']}/approve", headers=AGENT).status_code == 401
    assert client.post("/unfreeze", headers=AGENT).status_code == 401
    assert fake.captures == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "agent-key"}])
def test_pay_needs_a_valid_key(client, fake, headers):
    r = client.post("/pay", headers=headers, json={"merchant": "m", "item": "i", "category": "books",
                                                   "amount": 1, "task": "t"})
    assert r.status_code == 401
    assert fake.holds == []


def test_logged_in_user_cannot_pay_without_a_key(user, fake):
    r = user.post("/pay", json={"merchant": "m", "item": "i", "category": "books", "amount": 1, "task": "t"})
    assert r.status_code == 401


def test_agent_name_comes_from_the_key(client, fake):
    r = client.post("/pay", headers={"Authorization": "Bearer other-key"}, json={
        "agent": "claude", "merchant": "m", "item": "i", "category": "books", "amount": 1, "task": "t"})
    assert r.json()["agent"] == "other"


def test_agent_can_read_rules_and_its_payment(client, fake):
    assert client.get("/status", headers=AGENT).status_code == 200
    p = buy(client).json()
    assert client.get(f"/payments/{p['id']}", headers=AGENT).json()["status"] == "paid"
    assert client.get("/status").status_code == 401


def test_status_does_not_expose_the_vault_id(client):
    assert "vault-1" not in client.get("/status", headers=AGENT).text


def test_wrong_password(client):
    assert client.post("/login", json={"password": "nope"}).status_code == 401
    assert client.get("/payments").status_code == 401


def test_login_attempts_are_limited(client):
    codes = [client.post("/login", json={"password": "nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    # Even the right password waits
    assert client.post("/login", json={"password": "correct-horse-battery"}).status_code == 429


def test_short_password_turns_login_off(client, monkeypatch):
    monkeypatch.setenv("LEASH_ADMIN_PASSWORD", "short")
    assert client.post("/login", json={"password": "short"}).status_code == 401


def test_cookie_flags(client):
    r = client.post("/login", json={"password": "correct-horse-battery"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie


def test_tampered_or_expired_session_is_refused(client):
    good = security.make_session()
    expires, signature = good.split(".")
    assert security.valid_session(good)
    assert not security.valid_session(f"{int(expires) + 999}.{signature}")
    assert not security.valid_session("garbage")
    assert not security.valid_session("")
    old = str(int(time.time()) - 10)
    assert not security.valid_session(f"{old}.{security._sign(old)}")


def test_logout(user):
    assert user.post("/logout").status_code == 200
    assert user.get("/payments").status_code == 401


def test_changes_from_another_website_are_refused(user):
    assert user.post("/unfreeze", headers={"Origin": "https://evil.example"}).status_code == 403
    assert user.post("/unfreeze", headers={"Origin": "https://leash.example"}).status_code == 200


def test_vault_return_only_accepts_the_started_order(user, monkeypatch):
    from backend import paypal
    monkeypatch.setattr(paypal, "finish_vault", lambda token: "attacker-vault")
    db.put("vault_pending", "mine")
    assert user.get("/vault/return?token=theirs", follow_redirects=False).status_code == 400
    assert db.get("vault_id") == "vault-1"
    assert user.get("/vault/return?token=mine", follow_redirects=False).status_code == 303
    assert db.get("vault_id") == "attacker-vault"
    assert user.get("/vault/return?token=mine", follow_redirects=False).status_code == 400


def test_pay_rate_limit(client, fake):
    codes = [buy(client, category="toys", item=f"x{i}").status_code for i in range(31)]
    assert codes[:30] == [200] * 30
    assert codes[30] == 429


@pytest.mark.parametrize("change", [
    {"per_payment_limit": -1}, {"monthly_budget": "lots"}, {"allowed_categories": None}, {"currency": "dollars"},
])
def test_bad_policy_is_refused(user, change):
    assert user.put("/policy", json={**POLICY, **change}).status_code == 422
    assert db.get("policy") == POLICY


def test_policy_summary_is_dropped_and_names_are_lowered(user):
    saved = user.put("/policy", json={**POLICY, "summary": "x", "blocked_merchants": [" BadStore "]}).json()
    assert "summary" not in saved
    assert saved["blocked_merchants"] == ["badstore"]


def test_unexpected_error_hides_details(user, monkeypatch):
    def boom():
        raise RuntimeError("secret detail")
    monkeypatch.setattr(db, "payments", boom)
    r = user.get("/payments")
    assert r.status_code == 500
    assert "secret detail" not in r.text
    assert r.json()["request_id"] == r.headers["x-request-id"]


# Pages

def test_dashboard_needs_login(client):
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_dashboard_after_login(user):
    r = user.get("/app")
    assert r.status_code == 200 and "Kill switch" in r.text
    assert "noindex" in r.headers["x-robots-tag"]
    assert user.get("/login", follow_redirects=False).headers["location"] == "/app"


def test_security_headers(client):
    h = client.get("/").headers
    assert "script-src 'self'" in h["content-security-policy"]
    assert "unsafe-inline" not in h["content-security-policy"].split("script-src")[1].split(";")[0]
    assert h["x-frame-options"] == "DENY"
    assert h["x-content-type-options"] == "nosniff"
    assert "max-age" in h["strict-transport-security"]
    assert h["x-request-id"]


def test_pages_follow_the_csp():
    # script-src 'self' stops inline scripts and handlers, so none may exist
    for html in FRONTEND.glob("*.html"):
        text = html.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+=", text), html.name
        for tag in re.findall(r"<script\b[^>]*>", text):
            assert "src=" in tag or "application/ld+json" in tag, f"{html.name}: {tag}"
    for js in (FRONTEND / "static").glob("*.js"):
        assert not re.search(r"\son(click|input|change|load)=", js.read_text(encoding="utf-8")), js.name


def test_landing_page_seo(client):
    r = client.get("/")
    t = r.text
    assert r.status_code == 200 and "x-robots-tag" not in r.headers
    assert '<html lang="en">' in t
    assert len(re.findall(r"<h1\b", t)) == 1
    description = re.search(r'name="description" content="(.*?)"', t).group(1)
    assert 50 <= len(description) <= 170
    assert '<link rel="canonical" href="https://leash.example/">' in t
    assert 'property="og:image" content="https://leash.example/static/og.png"' in t
    assert "{{" not in t
    data = json.loads(re.search(r'application/ld\+json">\s*(.*?)\s*</script>', t, re.S).group(1))
    assert data["@type"] == "SoftwareApplication" and data["url"] == "https://leash.example/"


def test_robots_and_sitemap(client):
    robots = client.get("/robots.txt").text
    assert "Allow: /$" in robots and "Disallow: /" in robots
    assert "Sitemap: https://leash.example/sitemap.xml" in robots
    assert "<loc>https://leash.example/</loc>" in client.get("/sitemap.xml").text


def test_share_image_and_icons_exist(client):
    assert client.get("/static/og.png").headers["content-type"] == "image/png"
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/site.webmanifest").json()["name"] == "Leash"


def test_unknown_page(client):
    r = client.get("/nope", headers={"Accept": "text/html"})
    assert r.status_code == 404 and "Page not found" in r.text
    assert client.get("/nope", headers={"Accept": "application/json"}).json()["detail"] == "Not Found"


def test_text_responses_are_compressed(client):
    r = client.get("/static/site.css", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert r.headers["cache-control"] == "no-cache"


def test_load_balancer_checks(client, monkeypatch):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/ready").json() == {"ready": True}
    monkeypatch.setattr(db, "reachable", lambda: False)
    assert client.get("/ready").status_code == 503


def test_two_server_copies_share_a_login():
    # A signed cookie needs no shared memory, so a load balancer can send any request to any copy
    a = TestClient(main.app, base_url="https://leash.example")
    b = TestClient(main.app, base_url="https://leash.example")
    a.post("/login", json={"password": "correct-horse-battery"})
    b.cookies.set(security.COOKIE, a.cookies.get(security.COOKIE))
    assert b.get("/payments").status_code == 200
