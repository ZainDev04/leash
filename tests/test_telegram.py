import logging
import time

import httpx
import pytest

from backend import db, telegram

from .conftest import AGENT, buy

CHAT = 555
STRANGER = 999


@pytest.fixture
def tg(monkeypatch):
    """The bot exists and every Telegram call is recorded instead of sent."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    calls = []

    def call(method, **fields):
        calls.append((method, fields))
        return {"username": "leash_test_bot"} if method == "getMe" else {}
    monkeypatch.setattr(telegram, "_call", call)
    return calls


def said(calls):
    return [f["text"] for m, f in calls if m == "sendMessage"]


def post(client, update, secret=None):
    return client.post("/telegram/webhook", json=update, headers={
        "X-Telegram-Bot-Api-Secret-Token": telegram.webhook_secret() if secret is None else secret})


def message(text, chat=CHAT, kind="private"):
    return {"message": {"text": text, "chat": {"id": chat, "type": kind}}}


def button(data, chat=CHAT, sender=CHAT):
    return {"callback_query": {"id": "q1", "data": data, "from": {"id": sender},
                               "message": {"message_id": 7, "text": "Your agent wants to pay.", "chat": {"id": chat}}}}


def link(client):
    url = client.post("/telegram/link").json()["url"]
    assert url.startswith("https://t.me/leash_test_bot?start=")
    post(client, message(f"/start {url.split('start=')[1]}"))
    assert telegram.linked_chat() == CHAT


def waiting(client):
    p = buy(client, amount=64.0, quoted_price=64.0).json()
    assert p["status"] == "waiting"
    return p


# Linking

def test_link_needs_login(client, tg):
    assert client.post("/telegram/link").status_code == 401
    assert client.post("/telegram/link", headers=AGENT).status_code == 401
    assert client.post("/telegram/unlink", headers=AGENT).status_code == 401
    assert client.get("/telegram", headers=AGENT).status_code == 401


def test_link_tells_telegram_the_secret(user, tg):
    link(user)
    method, fields = tg[0]
    assert method == "setWebhook"
    assert fields["secret_token"] == telegram.webhook_secret()
    assert fields["url"].endswith("/telegram/webhook")
    assert user.get("/telegram").json() == {"available": True, "linked": True}


def test_no_bot_token_means_telegram_is_off(user, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    assert user.get("/telegram").json() == {"available": False, "linked": False}
    assert user.post("/telegram/link").status_code == 503
    assert user.post("/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": ""}).status_code == 404


def test_wrong_code_does_not_link(user, tg):
    user.post("/telegram/link")
    post(user, message("/start wrong-code", chat=STRANGER))
    assert telegram.linked_chat() is None


def test_code_works_once(user, tg):
    url = user.post("/telegram/link").json()["url"]
    code = url.split("start=")[1]
    post(user, message(f"/start {code}"))
    post(user, message(f"/start {code}", chat=STRANGER))
    assert telegram.linked_chat() == CHAT


def test_expired_code_does_not_link(user, tg):
    url = user.post("/telegram/link").json()["url"]
    db.put("telegram_link", {**db.get("telegram_link"), "expires": time.time() - 1})
    post(user, message(f"/start {url.split('start=')[1]}"))
    assert telegram.linked_chat() is None


def test_group_chat_cannot_link(user, tg):
    url = user.post("/telegram/link").json()["url"]
    post(user, message(f"/start {url.split('start=')[1]}", kind="group"))
    assert telegram.linked_chat() is None


def test_unlink_stops_messages_and_buttons(user, tg, fake):
    link(user)
    p = waiting(user)
    assert user.post("/telegram/unlink").status_code == 200
    post(user, button(f"approve:{p['id']}"))
    assert db.payment(p["id"])["status"] == "waiting"
    tg.clear()
    waiting_two = buy(user, item="Another", amount=64.0, quoted_price=64.0).json()
    assert waiting_two["status"] in ("waiting", "blocked")
    assert said(tg) == []


# Proof that an update really came from Telegram

@pytest.mark.parametrize("secret", ["", "wrong"])
def test_update_without_the_secret_does_nothing(user, tg, fake, secret):
    link(user)
    p = waiting(user)
    assert post(user, button(f"approve:{p['id']}"), secret=secret).status_code == 401
    assert db.payment(p["id"])["status"] == "waiting"
    assert fake.captures == []


def test_body_that_is_not_an_update_is_ignored(user, tg):
    r = user.post("/telegram/webhook", content="not json", headers={"X-Telegram-Bot-Api-Secret-Token": telegram.webhook_secret()})
    assert r.status_code == 200


# Buttons

def test_waiting_payment_arrives_with_buttons(user, tg):
    link(user)
    p = waiting(user)
    method, fields = [c for c in tg if c[0] == "sendMessage"][-1]
    assert fields["chat_id"] == CHAT
    assert p["item"] in fields["text"]
    keys = fields["reply_markup"]["inline_keyboard"][0]
    assert [k["callback_data"] for k in keys] == [f"approve:{p['id']}", f"reject:{p['id']}"]
    assert "parse_mode" not in fields


def test_approve_button_pays(user, tg, fake):
    link(user)
    p = waiting(user)
    assert post(user, button(f"approve:{p['id']}")).status_code == 200
    assert db.payment(p["id"])["status"] == "paid"
    assert len(fake.captures) == 1
    assert any(m == "editMessageText" and "paid" in f["text"].lower() for m, f in tg)


def test_reject_button_releases_the_hold(user, tg, fake):
    link(user)
    p = waiting(user)
    post(user, button(f"reject:{p['id']}"))
    assert db.payment(p["id"])["status"] == "cancelled"
    assert len(fake.voids) == 1 and fake.captures == []


def test_double_tap_pays_once(user, tg, fake):
    link(user)
    p = waiting(user)
    post(user, button(f"approve:{p['id']}"))
    post(user, button(f"approve:{p['id']}"))
    assert len(fake.captures) == 1


def test_button_after_the_dashboard_decided_changes_nothing(user, tg, fake):
    link(user)
    p = waiting(user)
    user.post(f"/payments/{p['id']}/reject")
    post(user, button(f"approve:{p['id']}"))
    assert db.payment(p["id"])["status"] == "cancelled"
    assert fake.captures == []


@pytest.mark.parametrize("chat, sender", [(STRANGER, STRANGER), (CHAT, STRANGER), (STRANGER, CHAT)])
def test_other_people_cannot_press_buttons(user, tg, fake, chat, sender):
    link(user)
    p = waiting(user)
    post(user, button(f"approve:{p['id']}", chat=chat, sender=sender))
    assert db.payment(p["id"])["status"] == "waiting"
    assert fake.captures == []


@pytest.mark.parametrize("data", ["approve:abc", "pay:1", "approve:1;drop", ""])
def test_strange_button_data_is_ignored(user, tg, fake, data):
    link(user)
    p = waiting(user)
    assert post(user, button(data)).status_code == 200
    assert db.payment(p["id"])["status"] == "waiting"


def test_button_for_a_payment_that_does_not_exist(user, tg):
    link(user)
    assert post(user, button("approve:999999")).status_code == 200


def test_failed_capture_tells_the_user_and_stays_waiting(user, tg, fake):
    link(user)
    p = waiting(user)
    fake.fail.add("capture")
    assert post(user, button(f"approve:{p['id']}")).status_code == 200
    assert db.payment(p["id"])["status"] == "waiting"
    assert any(m == "answerCallbackQuery" and "still waiting" in f["text"] for m, f in tg)


# Commands

def test_freeze_from_telegram(user, tg):
    link(user)
    post(user, message("/freeze"))
    assert db.get("frozen") is True


def test_stranger_cannot_freeze_or_read_status(user, tg):
    link(user)
    tg.clear()
    post(user, message("/freeze", chat=STRANGER))
    post(user, message("/status", chat=STRANGER))
    assert db.get("frozen") is False
    assert said(tg) == []


def test_status_shows_spending(user, tg):
    link(user)
    buy(user)
    post(user, message("/status"))
    assert "$25.00" in said(tg)[-1]


def test_telegram_cannot_lift_the_freeze(user, tg):
    link(user)
    post(user, message("/freeze"))
    post(user, message("/unfreeze"))
    assert db.get("frozen") is True


# Notifications must never affect a payment

def test_blocked_payment_sends_a_note_without_buttons(user, tg):
    link(user)
    tg.clear()
    buy(user, category="gift cards")
    sends = [f for m, f in tg if m == "sendMessage"]
    assert len(sends) == 1 and "reply_markup" not in sends[0]


def test_paid_payment_sends_nothing(user, tg):
    link(user)
    tg.clear()
    assert buy(user).json()["status"] == "paid"
    assert said(tg) == []


def test_telegram_being_down_does_not_affect_the_payment(user, tg, monkeypatch):
    link(user)

    def down(method, **fields):
        raise RuntimeError("Telegram sendMessage failed (ConnectError)")
    monkeypatch.setattr(telegram, "_call", down)
    assert buy(user, amount=64.0, quoted_price=64.0).json()["status"] == "waiting"


# The token is in every Telegram address, so it must not reach the logs

def test_failed_call_does_not_put_the_token_in_the_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")

    def boom(url, **kwargs):
        raise httpx.ConnectError(f"cannot reach {url}")
    monkeypatch.setattr(telegram.httpx, "post", boom)
    with pytest.raises(RuntimeError) as e:
        telegram._call("getMe")
    assert "secret-token" not in str(e.value)
    assert e.value.__suppress_context__ is True


def test_httpx_does_not_log_request_addresses():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_refused_setup_gives_a_clear_error(user, tg, monkeypatch):
    def refused(method, **fields):
        raise RuntimeError("Telegram setWebhook refused: Bad Request")
    monkeypatch.setattr(telegram, "_call", refused)
    assert user.post("/telegram/link").status_code == 502
