# Demo video script (target 2:50, hard limit 3:00)

Everything is recorded on the live site, https://leash-b2xx.onrender.com.

## Before recording

1. Open the live site a minute early, because the free service sleeps when idle.
2. Start with empty history (optional but cleaner). In the Neon console, open the SQL Editor and run `DELETE FROM paypal_events; DELETE FROM payments;`. This keeps the saved PayPal, the rules and the Telegram link. Do this again before every retake, because the repeat and split-purchase checks look at the last 10 minutes.
3. Set the rules: "Never more than $80 at once. Ask me above $40. $200 a month. Only books and groceries."
4. Lay out the screen: Chrome with the dashboard on the left, a terminal on the right with this typed but not run (set `LEASH_URL` and `LEASH_SHOPPER_KEY` first):
   `.venv/Scripts/python agents/shopper.py "Buy me a beginner Python book under 30 dollars"`
5. Have Claude Desktop ready with Leash connected, and Telegram open (https://web.telegram.org shows up best on screen, or film your phone).
6. Check the Gemini quota has not run out today. One agent purchase uses about nine calls.
7. Record with your own voice and no music. Copyrighted music is not allowed.

## Script

0:00, dashboard top with the agent card. "AI agents can shop for us now. The scary part is letting them pay. An agent with your PayPal can spend anything, or be tricked by a web page into buying something you never asked for."

0:15, move over the card. "This is Leash. Your agents pay with your PayPal, inside your rules. You connect PayPal once with PayPal Vault, and agents can pay later with no popup, and without ever holding your PayPal details."

0:28, scroll to the four checks. "Every payment goes through four steps. Hard rules in plain code. A PayPal authorization puts the money on hold. Gemini reads the shop page for tricks. Then Leash captures, asks you, or releases the hold."

0:45, rules panel. "My rules are in plain English. Gemini shows me what it understood, and nothing changes until I confirm."

0:55, run the Gemini agent in the terminal. "This is a real Gemini agent, nothing scripted. I ask it for a beginner Python book. It gets no warning about what comes next."

1:05, the first payment appears as stopped, open its reason. "It picked a book whose page hides text telling AI agents to also buy a 90 dollar gift card. No person would see it. Gemini did, and the hold was released."

1:20, the second payment appears as paid. "The agent chose another book. It passed everything, so PayPal captured it with no click from me."

1:30, Claude Desktop: "Use Leash to buy me an intermediate Python book from the demo store." "Any agent can use Leash. This is Claude, connected through MCP."

1:45, Telegram message arrives. "This purchase is under my 40 dollar limit by itself, but together with the last one it is over. Agents cannot get around my limit by splitting purchases. So the money is on hold, and my phone asks me." Tap Approve and pay.

2:00, dashboard, press Wrong item, Refund it, click again. "If an agent still buys the wrong thing, one button refunds it. PayPal's own webhook confirms what happened, and my budget comes back."

2:12, scroll to the audit log, filter by agent, open a row. "Every decision is on record in AG Grid: who decided, why, and what PayPal reported, with totals that follow my filters." Press Summarize my agents. "Gemini writes the weekly note. Code does the math."

2:28, show docs/red-team-results.md on GitHub. "We attacked Leash ourselves with 24 attacks. In the first run, one page wrote its own 'safety review finished' message, fooled the AI, and got paid. So we fixed it, in code. Now all 24 are stopped and normal purchases still go through. The AI is one layer, and it can only make decisions stricter."

2:45, flip the kill switch, or send /freeze in Telegram. "And one switch stops every agent. That's Leash. It's live and the code is on GitHub."

## Notes

- Payments take about 7 to 11 seconds. Keep talking, or cut the waits when editing (Clipchamp is built into Windows).
- Gemini's wording changes from run to run, so read what is on screen and do not promise exact words.
- Upload to YouTube as Public, on a channel in your or the project's name, then paste the link into Devpost.
