# Leash

Leash lets AI agents pay through PayPal, but only inside rules you set.

You save your PayPal once. After that, an agent can ask Leash to pay for something while you are away. Leash runs every payment through the same steps:

1. Fixed code checks: per-payment limit, monthly budget, allowed categories, allowed and blocked stores, currency, price changed since the quote, the same purchase twice in 10 minutes, and the freeze switch.
2. If those pass, Leash puts the money on hold in PayPal (an authorization, nothing is taken yet).
3. Gemini reads the shop page and the user's original task. It looks for hidden instructions aimed at AI agents and checks that the item fits the task.
4. Leash then captures the payment, keeps the hold and asks you, or voids the hold.

The AI can only make a decision stricter. It can never approve something the code checks reject. If Gemini or PayPal fails, Leash blocks the payment.

Built for the PayPal AI Hackathon 2026. PayPal sandbox only, no real money.

## How PayPal is used

- Orders v2 with intent `AUTHORIZE` to hold money before the checks finish
- Vault (`store_in_vault: ON_SUCCESS`) so the user approves once and agents can pay later with no click
- Authorizations v2 capture and void to pay or release the hold

## How AI is used

- Google Gemini (`gemini-3.5-flash`) checks each payment that passes the code checks: hidden instructions in the page, item vs task, and a plain-English reason for the user.
- Gemini turns rules written in plain English into the policy JSON. Leash shows a summary and only saves it after the user confirms.

## Run it

You need Python 3.11 or newer, a PayPal developer account and a Gemini API key.

1. PayPal sandbox app
   - Log in at https://developer.paypal.com, switch to Sandbox, open Apps & Credentials and create an app (type Merchant).
   - In the app's features, turn on Vault (save payment methods).
   - Under Testing Tools > Sandbox Accounts, note the Personal account's email and password. You log in with it when saving PayPal.
2. Gemini key from https://aistudio.google.com
3. Set up:
   ```
   git clone https://github.com/ZainDev04/leash.git
   cd leash
   python -m venv .venv
   .venv/Scripts/pip install -r backend/requirements.txt   # Mac/Linux: .venv/bin/pip
   cp .env.example .env
   ```
   Fill in `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `LLM_API_KEY` in `.env`.
4. Start Leash:
   ```
   .venv/Scripts/uvicorn backend.main:app --port 8000
   ```
5. Open http://localhost:8000 and click "Save my PayPal for agents". Log in with the sandbox Personal account and approve. A $1 hold is placed and released at once.
6. Optional: type your own rules in plain English, click "Read my rules", check the summary and confirm.
7. Run the demo agent in a second terminal and watch the dashboard:
   ```
   .venv/Scripts/python agents/demo_shopper.py
   ```
   It makes four purchases: one is paid, one waits for your approval, one is blocked because the price went up, and one is blocked because the shop page hides instructions for AI agents.

## Shop through Leash from Claude (MCP)

`mcp_server/server.py` gives an MCP client three tools: `pay`, `check_budget` and `check_payment`. There is no tool to approve a payment, change the rules or lift the freeze, so an agent cannot release its own leash.

Start the backend first (step 4 above), then add the server. Use the full path to your copy of the repo.

Claude Code:
```
claude mcp add leash -- /full/path/to/leash/.venv/Scripts/python /full/path/to/leash/mcp_server/server.py
```

Claude Desktop, in `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "leash": {
      "command": "/full/path/to/leash/.venv/Scripts/python",
      "args": ["/full/path/to/leash/mcp_server/server.py"]
    }
  }
}
```

On Mac or Linux the Python path ends in `.venv/bin/python`. Set `LEASH_URL` if the backend is not on `http://localhost:8000`, and `LEASH_AGENT` to change the name shown on the dashboard.

Then ask Claude to buy something and paste the shop page text. The payment appears on the dashboard. Leash only checks the page text the agent sends, so the `pay` tool tells the agent to send it unchanged.

## API

- `POST /pay`: agent, merchant, item, category, amount, currency, quoted_price, task, page_text. Returns the payment with status `paid`, `waiting` or `blocked` and the reasons.
- `GET /payments`, `POST /payments/{id}/approve`, `POST /payments/{id}/reject`
- `POST /policy/parse` (plain English in, draft rules out), `PUT /policy`
- `POST /freeze`, `POST /unfreeze`
- `GET /vault/start` (save PayPal), `GET /status`

## Folders

- `backend/` FastAPI app: checks, PayPal calls, Gemini calls, SQLite
- `frontend/` dashboard page
- `agents/` demo shopping agent
- `mcp_server/` MCP server for Claude and other MCP clients
- `docs/spikes/` the first Vault test script

## License

MIT
