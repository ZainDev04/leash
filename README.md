# Leash

Leash lets AI agents pay through PayPal, but only inside rules you set. Every payment is put on hold first, checked, and then paid, sent to you for approval, or cancelled.

Status: early build for the PayPal AI Hackathon 2026. PayPal sandbox only.

## Setup

1. Python 3.11+ and Node 18+.
2. `cp .env.example .env` and fill in your PayPal sandbox Client ID and Secret.
3. `python -m venv .venv` then `.venv/Scripts/pip install -r backend/requirements.txt` (Windows) or `.venv/bin/pip ...` (Mac/Linux).

## Folders

- `backend/` FastAPI app, rules, PayPal calls
- `agents/` demo AI shopping and travel agents
- `store/` fake shop the agents buy from
- `mcp/` MCP server so Claude Desktop and other agents can pay through Leash
- `frontend/` React dashboard
- `docs/spikes/` small test scripts
