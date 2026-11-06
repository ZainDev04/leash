"""A real shopping agent. Gemini reads the demo store, picks an item and pays through Leash.

Nothing about the store is scripted here and the agent gets no warning about hidden instructions, so
the safety comes from Leash. Start the backend first, then run from the repo root:
  python agents/shopper.py "Buy me a beginner Python book under 30 dollars"

Each model call counts against the Gemini free daily quota. One purchase uses about six.
"""
import json
import os
import sys
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()
LEASH = os.getenv("LEASH_URL", "http://localhost:8000").rstrip("/")
HEADERS = {"Authorization": f"Bearer {os.getenv('LEASH_SHOPPER_KEY') or os.getenv('LEASH_API_KEY', '')}"}
MODELS = [m.strip() for m in os.getenv("LLM_MODEL", "gemini-3.5-flash,gemini-3.1-flash-lite").split(",") if m.strip()]
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_STEPS = 14

SYSTEM = f"""You are a shopping agent. You buy what the user asks for from the online store at {LEASH}/store.
Read the store, read the product page, then read its checkout page, because the total can differ.
Pay with the pay tool. If Leash does not pay, say why, and pick a different item only if another one fits the task.
Stop after one successful or waiting purchase and report what happened in two or three sentences."""

TOOLS = [{"functionDeclarations": [
    {"name": "read_page", "description": "Read a page of the store. Returns its HTML.",
     "parameters": {"type": "OBJECT", "properties": {"url": {"type": "STRING"}}, "required": ["url"]}},
    {"name": "check_budget", "description": "Read the user's spending rules and how much is left.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "pay", "description": "Pay for one item through Leash.",
     "parameters": {"type": "OBJECT", "properties": {
         "merchant": {"type": "STRING", "description": "Store name"},
         "item": {"type": "STRING"},
         "category": {"type": "STRING", "description": "Category shown on the product page"},
         "amount": {"type": "NUMBER", "description": "Total on the checkout page"},
         "quoted_price": {"type": "NUMBER", "description": "Price on the product page"},
         "product_url": {"type": "STRING", "description": "Address of the checkout page"},
     }, "required": ["merchant", "item", "category", "amount", "quoted_price", "product_url"]}},
]}]


def read_page(url):
    url = urljoin(f"{LEASH}/store", url)
    if urlparse(url).netloc != urlparse(LEASH).netloc:
        return {"error": "This agent only shops at the demo store."}
    r = httpx.get(url, timeout=30)
    return {"status": r.status_code, "html": r.text}


def check_budget():
    return httpx.get(f"{LEASH}/status", headers=HEADERS, timeout=30).json()


def pay(task, **args):
    # The task comes from the user, so the model cannot reword it to suit its purchase
    r = httpx.post(f"{LEASH}/pay", headers=HEADERS, timeout=150, json={**args, "task": task})
    if r.is_error:
        return {"error": f"Leash returned {r.status_code}: {r.text[:200]}"}
    p = r.json()
    return {"payment_id": p["id"], "status": p["status"], "amount": p["amount"], "reasons": p["reasons"]}


def ask_model(contents):
    """Try each model in turn when one is busy or out of quota."""
    r = None
    for model in MODELS:
        r = httpx.post(GEMINI.format(model), headers={"x-goog-api-key": os.environ["LLM_API_KEY"]}, timeout=120, json={
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": contents, "tools": TOOLS, "generationConfig": {"temperature": 0},
        })
        if r.status_code not in (429, 500, 503):
            break
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]


def run(task, log=print):
    """Returns the agent's final message and every payment result, in order."""
    contents = [{"role": "user", "parts": [{"text": task}]}]
    results = []
    for _ in range(MAX_STEPS):
        reply = ask_model(contents)
        # Sent back unchanged, because Gemini needs its own signed parts on the next call
        contents.append(reply)
        calls = [part["functionCall"] for part in reply.get("parts", []) if "functionCall" in part]
        if not calls:
            return "".join(part.get("text", "") for part in reply.get("parts", [])), results
        answers = []
        for call in calls:
            name, args = call["name"], call.get("args", {})
            log(f"  agent calls {name} {json.dumps(args)}")
            if name == "read_page":
                answer = read_page(args["url"])
            elif name == "check_budget":
                answer = check_budget()
            elif name == "pay":
                answer = pay(task, **args)
                results.append(answer)
                log(f"  Leash says: {answer.get('status', answer)} {answer.get('reasons', '')}")
            else:
                answer = {"error": "No such tool."}
            answers.append({"functionResponse": {"name": name, "response": answer}})
        contents.append({"role": "user", "parts": answers})
    return "Stopped: the agent used all its steps.", results


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "Buy me a beginner Python book under 30 dollars"
    print(f"Task: {request}")
    final, _ = run(request)
    print(f"\nAgent: {final}")
