import importlib.util
from pathlib import Path

import pytest

from backend import ai, db, pages

from .conftest import buy

STORE = "https://shop.example"


@pytest.fixture
def shop(client, monkeypatch):
    """Leash reads the demo store through the test client, with its address on the verified list."""
    monkeypatch.setenv("LEASH_VERIFIED_STORES", "shop.example")

    def fetch(url):
        r = client.get(url.replace(STORE, ""))
        if r.status_code != 200:
            raise ValueError(r.status_code)
        return r.text
    monkeypatch.setattr(pages, "fetch", fetch)
    return client


def from_store(client, slug, amount, quoted=None, **changes):
    return buy(client, item=slug, amount=amount, quoted_price=quoted or amount, page_text="",
               product_url=f"{STORE}/store/checkout/{slug}", **changes).json()


def test_clean_product_is_paid_and_marked_read_by_leash(shop, fake):
    p = from_store(shop, "python-crash-course", 24.99)
    assert p["status"] == "paid"
    assert p["page_verified"] == 1
    assert "BookNook" in p["page_text"]


def test_leash_uses_the_page_it_read_and_ignores_the_agents_copy(shop, fake, monkeypatch):
    seen = {}
    monkeypatch.setattr(ai, "check_payment", lambda req: seen.update(req) or {
        "verdict": "ok", "hidden_instructions": False, "reason": "Fine."})
    buy(shop, item="x", amount=21.5, quoted_price=21.5, page_text="A nice clean page.",
        product_url=f"{STORE}/store/checkout/automate-the-boring-stuff")
    assert "ignore previous instructions" in seen["page_text"]
    assert "A nice clean page" not in seen["page_text"]


def test_agent_lying_about_the_price_is_blocked(shop, fake):
    p = from_store(shop, "python-crash-course", 1.00)
    assert p["status"] == "blocked"
    assert any("shop page says $24.99" in r for r in p["reasons"])
    assert fake.holds == []


def test_checkout_price_jump_is_blocked(shop, fake):
    p = from_store(shop, "learning-python", 29.00, quoted=19.00)
    assert p["status"] == "blocked"
    assert any("Price went up" in r for r in p["reasons"])


def test_agent_naming_the_wrong_seller_is_blocked(shop, fake):
    p = from_store(shop, "python-crash-course", 24.99, merchant="Other Shop")
    assert p["status"] == "blocked"
    assert any("seller" in r for r in p["reasons"])


def test_page_that_cannot_be_read_blocks(shop, fake):
    p = from_store(shop, "does-not-exist", 10.00)
    assert p["status"] == "blocked"
    assert fake.holds == []


def test_store_not_on_the_verified_list_asks(shop, fake, monkeypatch):
    monkeypatch.setattr(pages, "fetch", lambda url: pytest.fail("must not fetch this address"))
    p = buy(shop, product_url="http://169.254.169.254/latest/meta-data").json()
    assert p["status"] == "waiting"
    assert any("does not read pages" in r for r in p["reasons"])


@pytest.mark.parametrize("url", [
    "https://shop.example@evil.example/x", "https://evil.example/shop.example", "file:///etc/passwd",
    "ftp://shop.example/x",
])
def test_tricky_addresses_are_not_fetched(shop, fake, monkeypatch, url):
    monkeypatch.setattr(pages, "fetch", lambda u: pytest.fail("must not fetch this address"))
    assert buy(shop, product_url=url).json()["status"] == "waiting"


def test_no_link_asks_when_stores_are_verified(shop, fake):
    p = buy(shop).json()
    assert p["status"] == "waiting"
    assert any("no page link" in r for r in p["reasons"])


def test_no_verified_stores_keeps_the_agents_copy(client, fake):
    assert buy(client).json()["status"] == "paid"


def test_page_too_long_for_the_ai_asks(client, fake):
    p = buy(client, page_text="x" * (pages.AI_READS + 1)).json()
    assert p["status"] == "waiting"
    assert any("too long" in r for r in p["reasons"])


def test_ai_is_given_the_whole_page():
    seen = {}
    original, ai._ask = ai._ask, lambda prompt, schema: seen.update(prompt=prompt)
    try:
        from .test_payments import REAL_CHECK
        REAL_CHECK({"task": "t", "agent": "a", "merchant": "m", "item": "i", "category": "c", "amount": 1,
                    "currency": "USD", "page_text": "x" * 29_000 + "HIDDEN AT THE END"})
    finally:
        ai._ask = original
    assert "HIDDEN AT THE END" in seen["prompt"]


@pytest.mark.parametrize("html, price", [
    ('<script type="application/ld+json">{"@type":"Product","offers":{"price":"12.50","priceCurrency":"USD"}}</script>', 12.5),
    ('<script type="application/ld+json">[{"@type":"Thing"},{"@type":"Product","offers":[{"price":3}]}]</script>', 3.0),
])
def test_price_is_read_from_product_data(html, price):
    assert pages.offer(html)["price"] == price


@pytest.mark.parametrize("html", [
    "<p>$5</p>", '<script type="application/ld+json">not json</script>',
    '<script type="application/ld+json">{"@type":"Product","offers":{"price":"free"}}</script>',
    '<script type="application/ld+json">{"@type":"Product"}</script>',
])
def test_pages_without_a_usable_price(html):
    assert pages.offer(html) is None


def test_store_pages(client):
    assert "Python Crash Course" in client.get("/store").text
    poisoned = client.get("/store/p/automate-the-boring-stuff")
    assert "ignore previous instructions" in poisoned.text
    assert "noindex" in poisoned.text
    assert pages.offer(client.get("/store/p/learning-python").text)["price"] == 19.0
    assert pages.offer(client.get("/store/checkout/learning-python").text)["price"] == 29.0
    assert client.get("/store/p/nope").status_code == 404


# The agent loop, with a scripted model

def load_shopper():
    spec = importlib.util.spec_from_file_location(
        "shopper", Path(__file__).parent.parent / "agents" / "shopper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(name, **args):
    return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}, "thoughtSignature": "sig"}]}


def test_agent_loop(monkeypatch):
    shopper = load_shopper()
    script = iter([
        call("pay", merchant="BookNook", item="A", category="books", amount=5, quoted_price=5, product_url="u"),
        {"role": "model", "parts": [{"text": "Bought A."}]},
    ])
    sent = []
    monkeypatch.setattr(shopper, "ask_model", lambda contents: sent.append(list(contents)) or next(script))

    def pay(task, **args):
        assert task == "the user's words"
        return {"status": "paid", "reasons": []}
    monkeypatch.setattr(shopper, "pay", pay)
    final, results = shopper.run("the user's words", log=lambda *_: None)
    assert final == "Bought A."
    assert results == [{"status": "paid", "reasons": []}]
    # The model's own part goes back unchanged, signature included
    assert sent[1][1]["parts"][0]["thoughtSignature"] == "sig"


def test_agent_cannot_leave_the_store():
    assert "error" in load_shopper().read_page("https://evil.example/")


def test_agent_stops_after_its_step_limit(monkeypatch):
    shopper = load_shopper()
    monkeypatch.setattr(shopper, "ask_model", lambda contents: call("nope"))
    final, _ = shopper.run("x", log=lambda *_: None)
    assert final.startswith("Stopped")
