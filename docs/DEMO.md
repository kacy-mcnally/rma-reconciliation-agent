# Two-minute demo script

Open the [live dashboard](https://kacy-mcnally.github.io/rma-reconciliation-agent/) before the call. Have the GitHub repo in a second tab.

## 0:00 to 0:20: The problem (no screen yet)

> "At Quantum I recovered more than $8M in aging and disputed service inventory by reconciling orders, RMAs, receiving records, and inventory across 3PLs, by hand. I wanted to see how much of that investigation an AI agent could do, so I built one."

## 0:20 to 0:50: The headline (share the dashboard)

> "This is a synthetic returns network: five 3PL hubs, two repair depots, about 16,000 RMAs. I planted seven failures I've seen in real networks. It found $1.3M in 3PL and process exposure, and it separated two things that usually get mixed up: 566 customers who genuinely never returned a part, and 273 customers about to be billed for parts the 3PL actually received."

Point at the three numbers in the top card.

## 0:50 to 1:30: Two findings (scroll to "When each problem started")

> "Memphis: wrong part numbers keyed at receiving since March. Every one is an adjacent part number, 413 instead of 412, which tells you it's a keying problem, not a parts problem. The fix is scan-to-match at the dock."

> "Reno: one sharp spike, June 10 to 24. That's an interface outage. The 3PL received 94 units but the ERP never heard about them, so those RMAs aged into customer billing."

Scroll to the hub table:

> "Memphis reconciles on inventory even though it has the biggest problem. The error only shows up when you trace RMAs, which is why on-hand counts alone never catch it."

## 1:30 to 1:50: How it works (switch to the GitHub tab)

> "Ten investigation tools, the same checks I'd run manually, exposed through an MCP server so Claude can use them. Claude runs the investigation and drafts a corrective-action note to each 3PL. A scoring script checks every finding against the answer key."

## 1:50 to 2:00: Close

> "This is the work I've done for 18 years, now with tools that make it faster and repeatable. That's what I'd bring to your returns and service operations."

## Questions to expect

**Is this real data?**
> "No, it's fully synthetic, so I can share it. The failure patterns are real; they come from my experience."

**Did AI write the code?**
> "I built it with Claude Code as my engineering partner. I designed the network, the failure patterns, and the reconciliation logic; Claude helped me write and test the code. That's how I'd expect to work with AI on a team."

**Why 100% accuracy?**
> "Because I wrote detectors for failure types I know occur. It proves the method works end to end. In a real network, the agent's value is tracing the unknown ones, using the same tools plus SQL."

**How would you use this here?**
> "Start with your RMA, receiving, and inventory extracts for one 3PL. The tools are plain SQL against those tables, so a first pass takes days, not months."
