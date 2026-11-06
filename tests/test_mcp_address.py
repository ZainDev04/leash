import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("leash_mcp", Path(__file__).parent.parent / "mcp_server" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_store_path_becomes_a_full_address_leash_can_read(monkeypatch):
    monkeypatch.setattr(server, "LEASH", "https://leash.example")
    assert server.full_address("/store/checkout/fluent-python") == "https://leash.example/store/checkout/fluent-python"


@pytest.mark.parametrize("address", ["https://other.example/book", "evil.example/book", ""])
def test_other_addresses_are_left_for_leash_to_judge(address):
    assert server.full_address(address) == address


def test_pay_sends_the_full_address(monkeypatch):
    sent = {}
    monkeypatch.setattr(server, "LEASH", "https://leash.example")
    monkeypatch.setattr(server, "call", lambda method, path, **kwargs: sent.update(kwargs["json"]) or {"status": "paid"})
    monkeypatch.setattr(server, "outcome", lambda result: result)
    server.pay("BookNook", "Book", "books", 38.0, 38.0, "task", product_url="/store/checkout/fluent-python")
    assert sent["product_url"] == "https://leash.example/store/checkout/fluent-python"
