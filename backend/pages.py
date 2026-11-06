"""Leash reads the shop page itself, so it does not have to trust what an agent says the page shows.

Only stores listed in LEASH_VERIFIED_STORES are fetched, because a server that fetches any address an
agent names could be pointed at private addresses on its own network. Redirects are not followed for
the same reason.

The price and seller come from the schema.org Product data many shops already put in their pages.
"""
import json
import os
import re
from urllib.parse import urlparse

import httpx

from .security import public_url

MAX_BYTES = 1_000_000
# The most page text the AI reads. A longer page could hide instructions past the cut, so it goes to the user.
AI_READS = 30_000
LD_JSON = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def verified_hosts():
    """Hosts from LEASH_VERIFIED_STORES. The word self means this site, for the demo store."""
    hosts = [h.strip().lower() for h in os.getenv("LEASH_VERIFIED_STORES", "").split(",") if h.strip()]
    return [urlparse(public_url()).netloc.lower() if h == "self" else h for h in hosts]


def fetch(url):
    with httpx.stream("GET", url, timeout=10, follow_redirects=False) as r:
        if r.status_code != 200:
            raise ValueError(f"status {r.status_code}")
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("page too large")
    return body.decode("utf-8", "replace")


def offer(html):
    """The first schema.org Product on the page as price, currency and seller, or None."""
    for block in LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for thing in data if isinstance(data, list) else [data]:
            if not isinstance(thing, dict) or thing.get("@type") != "Product":
                continue
            found = thing.get("offers")
            found = found[0] if isinstance(found, list) and found else found
            if not isinstance(found, dict):
                continue
            try:
                price = float(found["price"])
            except (KeyError, TypeError, ValueError):
                continue
            seller = found.get("seller")
            return {"price": price, "currency": found.get("priceCurrency"),
                    "seller": seller.get("name") if isinstance(seller, dict) else None}
    return None


def inspect(req):
    """Returns (page_text, read_by_leash, block_reasons, ask_reasons)."""
    url, text, hosts = req.get("product_url"), req["page_text"], verified_hosts()
    block, ask = [], []
    if not url:
        if hosts:
            ask.append("The agent sent no page link, so Leash could not read the shop page itself.")
        return text, False, block, ask + too_long(text)

    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or parts.netloc.lower() not in hosts:
        ask.append(f"Leash does not read pages from {parts.netloc or 'that address'}, so it relied on the agent's copy.")
        return text, False, block, ask + too_long(text)

    try:
        text = fetch(url)
    except Exception:
        block.append("Leash could not read the shop page, so nothing was paid.")
        return text, False, block, ask

    found = offer(text)
    if not found:
        ask.append("The shop page shows no machine-readable price, so Leash could not confirm the amount.")
    else:
        if abs(found["price"] - req["amount"]) >= 0.005:
            block.append(f"The agent asked to pay ${req['amount']:.2f} but the shop page says ${found['price']:.2f}.")
        if found["currency"] and found["currency"] != req["currency"]:
            block.append(f"The agent asked to pay in {req['currency']} but the shop page says {found['currency']}.")
        if found["seller"] and found["seller"].strip().lower() != req["merchant"].lower():
            block.append(f"The agent named {req['merchant']} as the store but the page says the seller is {found['seller']}.")
    return text, True, block, ask + too_long(text)


def too_long(text):
    if len(text) > AI_READS:
        return ["The shop page is too long for the AI to read in full."]
    return []
