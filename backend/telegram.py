"""Approve or reject waiting payments from Telegram.

One chat is linked with a one-time code from the logged-in dashboard. Telegram proves each update with a
secret only it and Leash know, and buttons work only in the linked chat. Agents have no route to any of this.

The bot token is part of every Telegram address, so errors here are logged by type only, never with the address.
"""
import hashlib
import logging
import os
import re
import secrets
import time

import httpx

from . import db

log = logging.getLogger("leash")
CODE_MINUTES = 10
BUTTON = re.compile(r"^(approve|reject):(\d+)$")


def token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def webhook_secret():
    """Sent back by Telegram with every update. Derived, so it needs no setting of its own."""
    return hashlib.sha256(f"leash-telegram:{token()}".encode()).hexdigest()


def secret_matches(sent):
    return bool(token()) and secrets.compare_digest(sent or "", webhook_secret())


def _call(method, **fields):
    """Returns Telegram's result. Raises RuntimeError with no address in it if the call fails."""
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token()}/{method}", json=fields, timeout=8)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"Telegram {method} failed ({type(e).__name__})") from None
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} refused: {str(data.get('description'))[:120]}")
    return data["result"]


def linked_chat():
    return db.get("telegram_chat_id")


def start_link(webhook_url):
    """Point the bot at this site and make a one-time link. Returns the address the user opens."""
    _call("setWebhook", url=webhook_url, secret_token=webhook_secret(),
          allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    code = secrets.token_urlsafe(24)
    db.put("telegram_link", {"code": code, "expires": time.time() + CODE_MINUTES * 60})
    return f"https://t.me/{_call('getMe')['username']}?start={code}"


def unlink():
    db.put("telegram_chat_id", None)
    db.put("telegram_link", None)


def _use_code(sent):
    link = db.get("telegram_link")
    if not link or time.time() > link["expires"] or not secrets.compare_digest(sent, link["code"]):
        return False
    db.put("telegram_link", None)
    return True


def _say(chat_id, text, **extra):
    # No parse_mode, so item names written by shops show as plain text and cannot format or fake anything
    return _call("sendMessage", chat_id=chat_id, text=text[:4000], **extra)


def _describe(p):
    lines = [f"{p['item']}", f"${p['amount']:.2f} at {p['merchant']}", f"Agent: {p['agent']}"]
    reasons = [r.replace("AI ok:", "AI:") for r in p["reasons"]]
    return "\n".join(lines + ([""] + reasons if reasons else []))


def notify(p):
    """Best effort. A Telegram problem must never change what happens to a payment."""
    chat = linked_chat() if token() else None
    if not chat:
        return
    try:
        if p["status"] == "waiting":
            _say(chat, f"Your agent wants to pay. Nothing is taken unless you approve.\n\n{_describe(p)}", reply_markup={
                "inline_keyboard": [[{"text": "Approve and pay", "callback_data": f"approve:{p['id']}"},
                                     {"text": "Reject", "callback_data": f"reject:{p['id']}"}]]})
        elif p["status"] == "blocked":
            _say(chat, f"Leash stopped a payment. Nothing was taken.\n\n{_describe(p)}")
    except RuntimeError as e:
        log.warning(str(e))


def _handle_message(message, freeze):
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    if chat.get("type") != "private":
        return
    if text.startswith("/start "):
        if _use_code(text.split(" ", 1)[1].strip()):
            db.put("telegram_chat_id", chat["id"])
            _say(chat["id"], "Linked. Payments that need you will arrive here. Send /freeze to stop all agent payments, /status to see where things stand.")
        else:
            _say(chat["id"], "This link has expired or was already used. Make a new one on the Leash dashboard.")
        return
    # Everything else works only for the linked chat. Anyone else gets no answer at all.
    if chat.get("id") != linked_chat():
        return
    if text == "/freeze":
        freeze()
        _say(chat["id"], "All agent payments are frozen. Turn the kill switch off on the dashboard to allow them again.")
    elif text == "/status":
        policy = db.get("policy")
        state = "frozen" if db.get("frozen") else "running"
        _say(chat["id"], f"Agents are {state}. Spent this month: ${db.spent_this_month():.2f} of ${policy['monthly_budget']:.2f}.")
    else:
        _say(chat["id"], "Send /freeze to stop all agent payments, or /status to see where things stand.")


def _handle_button(query, approve, reject):
    message = query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    found = BUTTON.match(query.get("data") or "")
    if not found or chat_id is None or chat_id != linked_chat() or (query.get("from") or {}).get("id") != linked_chat():
        _call("answerCallbackQuery", callback_query_id=query["id"], text="This chat is not linked to Leash.")
        return
    action, pid = found.group(1), int(found.group(2))
    outcome = (approve if action == "approve" else reject)(pid)
    _call("answerCallbackQuery", callback_query_id=query["id"], text=outcome[:190])
    _call("editMessageText", chat_id=chat_id, message_id=message.get("message_id"), text=f"{message.get('text', '')}\n\n{outcome}"[:4000])


def handle_update(update, approve, reject, freeze):
    """approve and reject take a payment ID and return a sentence for the user. Failures are logged, because
    Telegram repeats an update it thinks failed, and a repeat must not act twice."""
    try:
        if update.get("callback_query"):
            _handle_button(update["callback_query"], approve, reject)
        elif update.get("message"):
            _handle_message(update["message"], freeze)
    except RuntimeError as e:
        log.warning(str(e))
