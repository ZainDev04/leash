# Demo video script (target 2:45, hard limit 3:00)

## Before recording

1. Clear old test payments so the ledger starts empty (keeps the saved PayPal and your rules). Stop the server first, then from `leash/`:
   ```
   .venv/Scripts/python -c "import sqlite3; c=sqlite3.connect('backend/leash.db'); c.execute('DELETE FROM payments'); c.commit()"
   .venv/Scripts/uvicorn backend.main:app --port 8000
   ```
2. Open http://localhost:8000 in Chrome, light theme, zoom 100%, close other tabs. Hide the bookmarks bar.
3. Open a terminal next to it, in `leash/`, with this command typed but not run:
   `.venv/Scripts/python agents/demo_shopper.py`
4. In a second Chrome tab, log in to https://www.sandbox.paypal.com with the sandbox Business account (to show the money arriving at the end).
5. Record with Windows Game Bar (Win + G, then the record button) or OBS. Use your own voice, no music (copyrighted music is not allowed).

## Script

| Time | Screen | Say |
|---|---|---|
| 0:00 | Dashboard top, card visible | "AI agents can shop for us now. The scary part is letting them pay. Give an agent your PayPal, and it can spend anything, or be tricked by a web page into buying something you never asked for." |
| 0:15 | Move the mouse over the card | "This is Leash. Your agents pay with your PayPal, but only inside your rules. You connect PayPal once using PayPal Vault, so the agent can pay later without a popup." |
| 0:30 | Scroll to "Four checks" | "Every payment goes through four steps. Hard rules in code. Then the money is put on hold with a PayPal authorization. Then Gemini reads the shop page for tricks. Then Leash captures the payment, asks you, or releases the hold." |
| 0:50 | Rules panel, type: "Never more than $80 at once. Ask me above $40. Only books and groceries." Click Read my rules | "I set my rules in plain English. Gemini turns them into rules and shows me what it understood. Nothing changes until I confirm." |
| 1:10 | Click Use these rules | "Confirmed." |
| 1:15 | Scroll to top, run the demo agent in the terminal | "Now a shopping agent gets a task: buy me a beginner Python book under 30 dollars. Watch the card." |
| 1:25 | First payment arrives, card glows green | "A 25 dollar book. It passes my rules, the hold is placed, the AI finds nothing wrong, and PayPal captures it. Paid, no click needed." |
| 1:40 | Second payment, card glows amber | "A 64 dollar box set. It's over my 40 dollar limit for asking, and the AI points out it's over the task's budget. Leash holds the money and waits for me." |
| 1:55 | Third payment, card shakes red | "Here the price jumped from 19 to 29 dollars at checkout. The code catches it before PayPal is even called." |
| 2:05 | Fourth payment, scroll to its card and point at the AI reason | "And this shop page has hidden text telling AI agents to also buy a 90 dollar gift card. A person would never see it. Gemini does, and Leash cancels the hold." |
| 2:20 | Click Approve and pay on the waiting payment | "Back to the box set. I decide it's fine and approve it. Leash captures the held money." |
| 2:30 | Switch to the sandbox PayPal tab, refresh the activity list | "And here it is in the PayPal sandbox: real authorizations, captures and voids." |
| 2:38 | Back to Leash, flip the Kill switch | "If something ever feels wrong, one switch freezes every agent payment." |
| 2:45 | Dashboard | "The AI can only make a decision stricter, never approve what your rules refuse, and if anything fails, nothing is paid. That's Leash. Code is on GitHub." |

## Notes

- Payments take about 7 to 11 seconds each (PayPal plus Gemini). Keep talking while they arrive, or cut the waits when editing (Clipchamp, built into Windows, is enough).
- If you run the demo twice within 10 minutes, the duplicate check blocks the repeats. Clear the payments again before a retake.
- Upload to YouTube as Public, title "Leash: AI agents pay with PayPal, inside your rules", then paste the link into Devpost.
