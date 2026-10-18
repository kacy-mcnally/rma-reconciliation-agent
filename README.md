# RMA Reconciliation Agent

An AI agent that investigates service-parts returns (RMA) and inventory discrepancies across a 3PL and repair-depot network: it traces units through each handoff, finds root causes, quantifies financial exposure, and drafts corrective actions.

## Status

- [x] Step 1: Synthetic operations dataset with planted failures
- [x] Step 2: MCP server and investigation agent
- [ ] Step 3: Dashboard
- [ ] Step 4: Vision intake, demo script

## The dataset

`python data/generate.py` builds `data/operations.db` (SQLite, deterministic seed). All companies, sites, parts, and customers are fictional.

| Table | What it represents |
|---|---|
| `sites` | 5 regional 3PL returns hubs and 2 repair depots |
| `parts` | 210 field-replaceable units (drives, controllers, tape drives, robotics, ...) |
| `rmas` | ~16,000 advance-replacement RMAs and their ERP status |
| `wms_receipts` | What each 3PL physically received and keyed |
| `erp_transactions` | ~75,000 ERP inventory transactions: receipts, transfers, repairs, scrap |
| `wms_onhand_snapshot` | 3PL physical on-hand at the cutoff date |

Seven failure patterns are planted, the kind that hide in real returns networks: keying errors, missing serials, duplicate receipts, interface outages, in-transit loss, warehouse shrink, and genuine customer non-returns that must be separated from 3PL failures. The answer key lives in `ground_truth/` and is used only to score the agent.

## Quick start

```bash
pip install -r requirements.txt
python data/generate.py        # build the dataset
python -m rma_agent.scan       # deterministic scan, no API key needed
python evaluate.py             # score findings against the answer key
python -m rma_agent.agent      # Claude investigates (needs ANTHROPIC_API_KEY)
python -m rma_agent.agent "Why are customers at Memphis being billed for parts they returned?"
```

## How it works

| Component | Role |
|---|---|
| `rma_agent/investigate.py` | Ten investigation tools: network overview, receipt mismatches, duplicate receipts, unposted receipts, in-transit gaps, on-hand variance attribution, missing warehouse units, customer non-returns, end-to-end RMA trace, and read-only SQL |
| `rma_agent/mcp_server.py` | Exposes the tools over the Model Context Protocol, so any MCP client can use them |
| `rma_agent/agent.py` | A Claude agent that connects to the MCP server, investigates, and writes an exception report with corrective-action notes for each 3PL |
| `rma_agent/scan.py` | Runs every detector without an LLM: the baseline and the input to scoring |
| `evaluate.py` | Scores findings against the planted answer key |

## Results

The scan finds all seven planted failure patterns with 100% recall and precision and no false alarms, in under a second across about 16,000 RMAs and 75,000 transactions. The detectors were written knowing which failure types exist in returns networks, so the score shows the method works end to end rather than measuring discovery of unknown problems.

| Site | Issue | Units | Exposure |
|---|---|---|---|
| Memphis | Wrong part number keyed at receiving (adjacent part numbers) since March 2026 | 197 | $443,934 |
| Singapore | Serial numbers not captured at receiving since May 2026 | 116 | $277,467 |
| Louisville | Duplicate receipts overstating ERP inventory | 97 | $226,788 |
| Reno | WMS-to-ERP interface outage, June 10 to 24, 2026 | 94 | $157,234 |
| Amsterdam to Penang | Transfer TO00169 shipped, never received | 66 | $145,236 |
| Amsterdam | Units missing from the warehouse while ERP shows them on hand | 19 | $48,091 |
| **Total 3PL and process failures** | | | **$1,298,750** |

Separately, 566 RMAs ($1.24M) are genuine customer non-returns that should be billed, while 273 customers at Memphis, Reno, and Singapore are flagged for billing even though the 3PL received their parts.

## Use the tools from Claude Desktop or Claude Code

Add the server to your MCP client configuration:

```json
{
  "mcpServers": {
    "rma-reconciliation": {
      "command": "python",
      "args": ["-m", "rma_agent.mcp_server"],
      "cwd": "/path/to/rma-reconciliation-agent"
    }
  }
}
```
