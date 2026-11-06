"""Two copies of the server as separate processes, the way a load balancer would see them.

Requests take turns between the copies. Runs on SQLite locally and on real Postgres in CI.
"""
import itertools
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend import db, security

from .conftest import POLICY

BODY = {"merchant": "BookNook", "category": "books", "task": "Buy a book"}
AGENT = {"Authorization": "Bearer agent-key"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def copies():
    urls, processes = [], []
    for _ in range(2):
        port = free_port()
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tests.fake_server:app", "--port", str(port), "--log-level", "warning"],
            env={**os.environ, "PUBLIC_URL": "http://localhost"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        urls.append(f"http://127.0.0.1:{port}")
    for url in urls:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/ready", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail("A server copy did not start.")
    yield urls, processes
    for process in processes:
        process.kill()
        process.wait()


def pay_through(url, item, amount=39.0):
    return httpx.post(f"{url}/pay", headers=AGENT, timeout=60,
                      json={**BODY, "item": item, "amount": amount, "quoted_price": amount}).json()


def test_budget_holds_across_copies(copies):
    urls, _ = copies
    turn = itertools.cycle(urls)
    jobs = [(next(turn), f"Book {i}") for i in range(10)]
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda job: pay_through(*job)["status"], jobs))
    # Budget is 100, so at most two 39.00 payments fit, whichever copy took them
    assert results.count("paid") == 2
    assert results.count("blocked") == 8


def test_repeat_is_caught_across_copies(copies):
    urls, _ = copies
    jobs = [urls[i % 2] for i in range(8)]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda url: pay_through(url, "Same book", 5.0)["status"], jobs))
    assert results.count("paid") == 1


def test_login_on_one_copy_works_on_the_other(copies):
    urls, _ = copies
    r = httpx.post(f"{urls[0]}/login", json={"password": "correct-horse-battery"})
    assert r.status_code == 200
    cookies = {security.COOKIE: r.cookies[security.COOKIE]}
    assert httpx.get(f"{urls[1]}/payments", cookies=cookies).status_code == 200


def test_kill_switch_on_one_copy_stops_the_other(copies):
    urls, _ = copies
    cookies = {security.COOKIE: security.make_session()}
    assert httpx.post(f"{urls[0]}/freeze", cookies=cookies).status_code == 200
    p = pay_through(urls[1], "Frozen book", 5.0)
    assert p["status"] == "blocked"
    assert any("frozen" in r for r in p["reasons"])


def test_approve_on_two_copies_captures_once(copies):
    urls, _ = copies
    p = pay_through(urls[0], "Big book", 45.0)
    assert p["status"] == "waiting"
    cookies = {security.COOKIE: security.make_session()}
    jobs = [urls[i % 2] for i in range(6)]
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(
            lambda url: httpx.post(f"{url}/payments/{p['id']}/approve", cookies=cookies, timeout=30).status_code, jobs))
    assert codes.count(200) == 1


def test_login_limit_is_shared_between_copies(copies):
    urls, _ = copies
    # Each copy sees the same address from this machine, so the count must add up across both
    codes = [httpx.post(f"{urls[i % 2]}/login", json={"password": "nope"}).status_code for i in range(11)]
    assert codes[-1] == 429


def test_other_copy_keeps_working_when_one_dies(copies):
    urls, processes = copies
    processes[0].kill()
    processes[0].wait()
    with pytest.raises(httpx.TransportError):
        httpx.get(f"{urls[0]}/ready", timeout=2)
    assert httpx.get(f"{urls[1]}/ready", timeout=5).json() == {"ready": True}
    assert pay_through(urls[1], "After the crash", 5.0)["status"] == "paid"
    assert db.get("policy") == POLICY
