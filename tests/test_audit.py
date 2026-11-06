from .conftest import AGENT, buy


def test_audit_needs_login(client):
    assert client.get("/audit").status_code == 401


def test_agent_key_cannot_read_audit(client):
    assert client.get("/audit", headers=AGENT).status_code == 401


def test_audit_lists_every_decision_newest_first(user):
    buy(user, item="Book one")
    buy(user, item="Over the limit", amount=90.0, quoted_price=90.0)
    rows = user.get("/audit").json()
    assert [r["item"] for r in rows] == ["Over the limit", "Book one"]
    assert [r["status"] for r in rows] == ["blocked", "paid"]
    assert rows[1]["capture_id"] == "capture-auth-1"
    assert any("per-payment limit" in reason for reason in rows[0]["reasons"])


def test_audit_goes_past_the_100_rows_the_payment_list_shows(user):
    from backend import db
    for i in range(101):
        db.add_payment({"item": f"Row {i}", "amount": 1.0, "status": "blocked", "reasons": "[]"})
    assert len(user.get("/payments").json()) == 100
    assert len(user.get("/audit").json()) == 101


def test_audit_leaves_out_page_text(user):
    buy(user)
    assert "page_text" not in user.get("/audit").json()[0]


def test_spent_total_is_rounded_to_cents(user):
    buy(user, item="First", amount=24.99, quoted_price=24.99)
    buy(user, item="Second", amount=38.0, quoted_price=38.0)
    assert user.get("/status").json()["spent_this_month"] == 62.99


def test_own_files_are_rechecked_and_versioned_files_are_kept(client):
    assert client.get("/static/audit.js").headers["Cache-Control"] == "no-cache"
    assert "immutable" in client.get("/static/vendor/ag-grid-community-36.3.0.min.js").headers["Cache-Control"]

