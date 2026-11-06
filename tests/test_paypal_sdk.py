"""Checks the requests sent to the SDK and how PayPal's documented responses are read, with no network."""
from types import SimpleNamespace as NS

import pytest
from paypalserversdk.models.order import Order

from backend import paypal

# Saved here because the shared fixtures replace these functions with fakes
HOLD, CAPTURE, VOID, START, FINISH = paypal.hold, paypal.capture, paypal.void, paypal.start_vault, paypal.finish_vault


def order(status="COMPLETED", auth_status="CREATED", vault=None):
    data = {
        "id": "ORDER1", "status": status,
        "purchase_units": [{"payments": {"authorizations": [{"id": "AUTH1", "status": auth_status}]}}],
        "links": [{"rel": "self", "href": "https://api/self", "method": "GET"},
                  {"rel": "payer-action", "href": "https://paypal/approve", "method": "GET"}],
    }
    if vault:
        data["payment_source"] = {"paypal": {"attributes": {"vault": vault}}}
    return Order.from_dictionary(data)


class FakeClient:
    def __init__(self, created=None, authorized=None):
        self.calls = []
        self.orders = NS(
            create_order=lambda options: self._done("create", options, created),
            authorize_order=lambda options: self._done("authorize", options, authorized))
        self.payments = NS(
            capture_authorized_payment=lambda options: self._done("capture", options, NS(id="CAPTURE1")),
            void_payment=lambda options: self._done("void", options, None))

    def _done(self, name, options, body):
        self.calls.append((name, options))
        return NS(body=body)

    def names(self):
        return [name for name, _ in self.calls]


def use(monkeypatch, **kwargs):
    client = FakeClient(**kwargs)
    monkeypatch.setattr(paypal, "_client", lambda: client)
    return client


def test_hold_from_saved_paypal(monkeypatch):
    client = use(monkeypatch, created=order())
    assert HOLD("VAULT1", 24.5, "USD", "x" * 200) == ("ORDER1", "AUTH1")
    # A saved account comes back already authorized, and authorizing again is an error at PayPal
    assert client.names() == ["create"]
    body = client.calls[0][1]["body"]
    assert body.intent == "AUTHORIZE"
    assert body.payment_source.paypal.vault_id == "VAULT1"
    assert body.purchase_units[0].amount.value == "24.50"
    assert len(body.purchase_units[0].description) == 127


def test_hold_authorizes_when_paypal_has_not(monkeypatch):
    client = use(monkeypatch, created=order(status="CREATED"), authorized=order())
    assert HOLD("VAULT1", 5, "USD", "d") == ("ORDER1", "AUTH1")
    assert client.names() == ["create", "authorize"]


def test_refused_hold_raises(monkeypatch):
    use(monkeypatch, created=order(auth_status="DENIED"))
    with pytest.raises(paypal.PayPalError):
        HOLD("VAULT1", 5, "USD", "d")


def test_start_vault_asks_paypal_to_save_the_account(monkeypatch):
    client = use(monkeypatch, created=order(status="PAYER_ACTION_REQUIRED"))
    assert START("https://me/return", "https://me/cancel") == ("ORDER1", "https://paypal/approve")
    wallet = client.calls[0][1]["body"].payment_source.paypal
    assert wallet.attributes.vault.store_in_vault == "ON_SUCCESS"
    assert wallet.experience_context.return_url == "https://me/return"


def test_finish_vault_voids_the_dollar_and_returns_the_id(monkeypatch):
    client = use(monkeypatch, authorized=order(vault={"id": "VAULT9", "status": "VAULTED"}))
    monkeypatch.setattr(paypal, "void", VOID)
    assert FINISH("ORDER1") == "VAULT9"
    assert client.names() == ["authorize", "void"]
    assert client.calls[1][1]["authorization_id"] == "AUTH1"


def test_finish_vault_refuses_an_unsaved_account(monkeypatch):
    use(monkeypatch, authorized=order(vault={"id": "VAULT9", "status": "APPROVED"}))
    with pytest.raises(paypal.PayPalError):
        FINISH("ORDER1")


def test_capture_and_void(monkeypatch):
    client = use(monkeypatch)
    assert CAPTURE("AUTH1") == "CAPTURE1"
    VOID("AUTH1")
    assert client.calls[0][1]["authorization_id"] == "AUTH1"
    assert client.calls[1][1]["authorization_id"] == "AUTH1"


def test_each_change_has_its_own_request_id(monkeypatch):
    client = use(monkeypatch)
    CAPTURE("A")
    CAPTURE("B")
    assert client.calls[0][1]["paypal_request_id"] != client.calls[1][1]["paypal_request_id"]
