"""Every test gets an empty database and a fake PayPal and Gemini, so no network is used."""
import os
import tempfile
from pathlib import Path

# Set before the app loads. load_dotenv does not overwrite these, so a real .env is ignored.
TEST_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ["LEASH_DB"] = str(TEST_DB)
os.environ["LEASH_ADMIN_PASSWORD"] = "correct-horse-battery"
os.environ["LEASH_AGENT_KEYS"] = "claude:agent-key,other:other-key"
os.environ["PUBLIC_URL"] = "https://leash.example"
os.environ["PAYPAL_CLIENT_ID"] = "unused"
os.environ["PAYPAL_CLIENT_SECRET"] = "unused"
os.environ["LLM_API_KEY"] = "unused"

import pytest
from fastapi.testclient import TestClient

from backend import ai, db, main, paypal

AGENT = {"Authorization": "Bearer agent-key"}
POLICY = {
    "per_payment_limit": 80, "ask_above": 40, "monthly_budget": 100,
    "allowed_categories": ["books"], "allowed_merchants": [],
    "blocked_merchants": ["badstore"], "currency": "USD",
}


class FakePayPal:
    def __init__(self):
        self.holds, self.captures, self.voids = [], [], []
        self.fail = set()

    def _step(self, name):
        if name in self.fail:
            raise paypal.PayPalError(name)

    def hold(self, vault_id, amount, currency, description):
        self._step("hold")
        self.holds.append(amount)
        return f"order-{len(self.holds)}", f"auth-{len(self.holds)}"

    def capture(self, auth_id):
        self._step("capture")
        self.captures.append(auth_id)
        return f"capture-{auth_id}"

    def void(self, auth_id):
        self._step("void")
        self.voids.append(auth_id)


@pytest.fixture
def fake(monkeypatch):
    f = FakePayPal()
    monkeypatch.setattr(paypal, "hold", f.hold)
    monkeypatch.setattr(paypal, "capture", f.capture)
    monkeypatch.setattr(paypal, "void", f.void)
    monkeypatch.setattr(ai, "check_payment", lambda req: {
        "verdict": "ok", "hidden_instructions": False, "item_matches_task": True, "reason": "Looks fine."})
    return f


@pytest.fixture(autouse=True)
def clean_db(fake):
    with db.conn() as c:
        c.executescript("DELETE FROM payments; DELETE FROM settings; DELETE FROM rate_limits;")
    db.init()
    db.put("policy", POLICY)
    db.put("vault_id", "vault-1")


@pytest.fixture
def client():
    return TestClient(main.app, base_url="https://leash.example", raise_server_exceptions=False)


@pytest.fixture
def user(client):
    assert client.post("/login", json={"password": "correct-horse-battery"}).status_code == 200
    return client


def buy(client, **changes):
    body = {"merchant": "BookNook", "item": "Python Crash Course", "category": "books",
            "amount": 25.0, "quoted_price": 25.0, "task": "Buy a Python book",
            "page_text": "Python Crash Course. In stock."}
    return client.post("/pay", json={**body, **changes}, headers=AGENT)
