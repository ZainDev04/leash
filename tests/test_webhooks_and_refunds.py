import json

import httpx
import pytest

from backend import db, paypal

from .conftest import AGENT, buy


@pytest.fixture
def signed(monkeypatch):
    """PayPal says yes to every signature. Each test sends events through the real endpoint."""
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    monkeypatch.setattr(paypal, "verify_webhook", lambda headers, raw: True)


def event(event_type, resource, event_id="WH-EVENT-1"):
    return {"id": event_id, "event_type": event_type, "resource": resource}


def send(client, body):
    return client.post("/paypal/webhook", content=json.dumps(body))


def refund_event(capture_id, event_id="WH-EVENT-1"):
    return event("PAYMENT.CAPTURE.REFUNDED", {
        "id": "refund-9", "links": [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}],
    }, event_id)


def test_bad_signature_changes_nothing(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    monkeypatch.setattr(paypal, "verify_webhook", lambda headers, raw: False)
    p = buy(client).json()
    assert send(client, refund_event(p["capture_id"])).status_code == 400
    assert db.payment(p["id"])["status"] == "paid"


def test_verify_failure_is_treated_as_a_bad_signature(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")

    def down(headers, raw):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(paypal, "verify_webhook", down)
    assert send(client, refund_event("x")).status_code == 400


def test_no_webhook_id_means_webhooks_are_off(client, monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    assert send(client, refund_event("x")).status_code == 503


def test_body_that_is_not_an_event_is_refused(client, signed):
    assert client.post("/paypal/webhook", content="not json").status_code == 400
    assert client.post("/paypal/webhook", content='["a list"]').status_code == 400
    assert client.post("/paypal/webhook", content='{"event_type": "no id"}').status_code == 400


def test_huge_body_is_refused(client, signed):
    assert client.post("/paypal/webhook", content="x" * 300_000).status_code == 413


def test_signature_check_sends_the_event_exactly_as_received(monkeypatch):
    """PayPal signs the original bytes, so spacing must survive into the verify call."""
    sent = {}

    class Reply:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return self

        def json(self):
            return self.data

    def post(url, **kwargs):
        if url.endswith("/oauth2/token"):
            return Reply({"access_token": "t"})
        sent.update(url=url, content=kwargs["content"])
        return Reply({"verification_status": "SUCCESS"})

    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    monkeypatch.setattr(paypal.httpx, "post", post)
    raw = '{"id":  "E1",   "event_type": "X"}'
    assert paypal.verify_webhook({"paypal-transmission-id": "T1"}, raw) is True
    assert raw in sent["content"]
    assert sent["url"].startswith("https://api-m.sandbox.paypal.com/")
    assert json.loads(sent["content"])["webhook_id"] == "WH-1"


def test_signature_check_says_no_when_paypal_says_failure(monkeypatch):
    class Reply:
        def raise_for_status(self):
            return self

        def json(self):
            return {"access_token": "t", "verification_status": "FAILURE"}

    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    monkeypatch.setattr(paypal.httpx, "post", lambda url, **kwargs: Reply())
    assert paypal.verify_webhook({}, "{}") is False


def test_capture_event_is_recorded_on_the_payment(user, signed):
    p = buy(user).json()
    assert send(user, event("PAYMENT.CAPTURE.COMPLETED", {"id": p["capture_id"]})).status_code == 200
    row = user.get("/audit").json()[0]
    assert db.payment(p["id"])["status"] == "paid"
    assert [e["label"] for e in row["paypal_events"]] == ["Payment taken"]


def test_event_can_arrive_before_the_capture_id_is_stored(user, signed):
    p = buy(user).json()
    db.update_payment(p["id"], capture_id=None)
    send(user, event("PAYMENT.CAPTURE.COMPLETED", {
        "id": "capture-new", "supplementary_data": {"related_ids": {"authorization_id": p["authorization_id"]}}}))
    assert [e["label"] for e in user.get("/audit").json()[0]["paypal_events"]] == ["Payment taken"]


def test_refund_in_paypal_updates_leash_and_gives_budget_back(user, signed):
    p = buy(user).json()
    spent = user.get("/status").json()["spent_this_month"]
    assert send(user, refund_event(p["capture_id"])).status_code == 200
    row = db.payment(p["id"])
    assert row["status"] == "refunded" and row["refund_id"] == "refund-9"
    assert any("PayPal reports" in r for r in row["reasons"])
    assert user.get("/status").json()["spent_this_month"] == spent - 25.0


def test_same_event_twice_is_handled_once(user, signed):
    p = buy(user).json()
    send(user, refund_event(p["capture_id"]))
    assert send(user, refund_event(p["capture_id"])).status_code == 200
    row = db.payment(p["id"])
    assert len([r for r in row["reasons"] if "PayPal reports" in r]) == 1
    assert len(user.get("/audit").json()[0]["paypal_events"]) == 1


def test_denied_capture_closes_the_payment(client, signed):
    p = buy(client).json()
    send(client, event("PAYMENT.CAPTURE.DENIED", {"id": p["capture_id"]}))
    assert db.payment(p["id"])["status"] == "blocked"


def test_hold_voided_in_paypal_cancels_a_waiting_payment(client, signed):
    p = buy(client, amount=64.0, quoted_price=64.0).json()
    assert p["status"] == "waiting"
    send(client, event("PAYMENT.AUTHORIZATION.VOIDED", {"id": p["authorization_id"]}))
    assert db.payment(p["id"])["status"] == "cancelled"


def test_void_event_does_not_touch_a_paid_payment(client, signed):
    p = buy(client).json()
    send(client, event("PAYMENT.AUTHORIZATION.VOIDED", {"id": p["authorization_id"]}))
    assert db.payment(p["id"])["status"] == "paid"


def test_removing_the_saved_paypal_stops_agents(client, signed):
    send(client, event("VAULT.PAYMENT-TOKEN.DELETED", {"id": "vault-1"}))
    assert db.get("vault_id") is None
    assert buy(client).json()["status"] == "blocked"


def test_removing_a_different_token_keeps_the_saved_paypal(client, signed):
    send(client, event("VAULT.PAYMENT-TOKEN.DELETED", {"id": "another-token"}))
    assert db.get("vault_id") == "vault-1"


def test_event_with_no_matching_payment_is_accepted(client, signed):
    assert send(client, event("PAYMENT.CAPTURE.COMPLETED", {"id": "unknown"})).status_code == 200


def test_refund_needs_login(client):
    p = buy(client).json()
    assert client.post(f"/payments/{p['id']}/refund").status_code == 401
    assert client.post(f"/payments/{p['id']}/refund", headers=AGENT).status_code == 401
    assert db.payment(p["id"])["status"] == "paid"


def test_refund_returns_money_and_budget(user, fake):
    p = buy(user).json()
    r = user.post(f"/payments/{p['id']}/refund").json()
    assert r["status"] == "refunded"
    assert r["refund_id"] == f"refund-{p['capture_id']}"
    assert fake.refunds == [p["capture_id"]]
    assert user.get("/status").json()["spent_this_month"] == 0


def test_refund_twice_refunds_once(user, fake):
    p = buy(user).json()
    assert user.post(f"/payments/{p['id']}/refund").status_code == 200
    assert user.post(f"/payments/{p['id']}/refund").status_code == 409
    assert len(fake.refunds) == 1


def test_only_a_paid_payment_can_be_refunded(user, fake):
    blocked = buy(user, category="gift cards").json()
    assert user.post(f"/payments/{blocked['id']}/refund").status_code == 409
    assert fake.refunds == []


def test_failed_refund_stays_paid(user, fake):
    p = buy(user).json()
    fake.fail.add("refund")
    assert user.post(f"/payments/{p['id']}/refund").status_code == 502
    assert db.payment(p["id"])["status"] == "paid"


def test_our_own_refund_and_its_webhook_do_not_double_up(user, fake, signed):
    p = buy(user).json()
    user.post(f"/payments/{p['id']}/refund")
    send(user, refund_event(p["capture_id"]))
    row = db.payment(p["id"])
    assert row["status"] == "refunded"
    assert not any("PayPal reports" in r for r in row["reasons"])


# PayPal can take the money and the reply can still be lost. Found by checking against the APIMatic context plugin.

def test_lost_reply_after_paypal_took_the_money_is_paid(client, fake):
    fake.fail.add("capture")
    fake.taken = True
    p = buy(client).json()
    assert p["status"] == "paid"
    assert fake.voids == []


def test_capture_that_really_failed_is_stopped_and_released(client, fake):
    fake.fail.add("capture")
    p = buy(client).json()
    assert p["status"] == "blocked"
    assert len(fake.voids) == 1
    assert not any("nothing was paid" in r for r in p["reasons"])


def test_no_answer_at_all_is_stopped_without_claiming_nothing_was_taken(client, fake):
    fake.fail |= {"capture", "capture_taken", "void"}
    p = buy(client).json()
    assert p["status"] == "blocked"
    text = " ".join(p["reasons"])
    assert "no money was taken" not in text and "nothing was paid" not in text
    assert "update by itself" in text


def test_late_webhook_corrects_a_payment_that_was_really_taken(user, fake, signed):
    fake.fail |= {"capture", "capture_taken", "void"}
    p = buy(user).json()
    send(user, event("PAYMENT.CAPTURE.COMPLETED", {
        "id": "capture-late", "supplementary_data": {"related_ids": {"authorization_id": p["authorization_id"]}}}))
    row = db.payment(p["id"])
    assert row["status"] == "paid" and row["capture_id"] == "capture-late"
    assert user.get("/status").json()["spent_this_month"] == 25.0
    fake.fail.clear()
    assert user.post(f"/payments/{p['id']}/refund").json()["status"] == "refunded"


def test_blocked_payment_with_no_capture_event_stays_blocked(client, signed):
    p = buy(client, category="gift cards").json()
    send(client, event("PAYMENT.AUTHORIZATION.VOIDED", {"id": "other"}))
    assert db.payment(p["id"])["status"] == "blocked"


def test_approve_with_a_lost_reply_is_paid_once(user, fake):
    p = buy(user, amount=64.0, quoted_price=64.0).json()
    fake.fail.add("capture")
    fake.taken = True
    assert user.post(f"/payments/{p['id']}/approve").json()["status"] == "paid"
    assert user.post(f"/payments/{p['id']}/approve").status_code == 409


def test_capture_taken_reads_the_hold_from_paypal(monkeypatch):
    from types import SimpleNamespace as NS
    seen = {}

    def read(options):
        seen.update(options)
        return NS(body=NS(status="CAPTURED"))
    monkeypatch.undo()
    monkeypatch.setattr(paypal, "_client", lambda: NS(payments=NS(get_authorized_payment=read)))
    assert paypal.capture_taken("AUTH1") is True
    assert seen == {"authorization_id": "AUTH1"}

