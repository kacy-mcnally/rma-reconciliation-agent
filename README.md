# RMA Reconciliation Agent

An AI agent that investigates service-parts returns (RMA) and inventory discrepancies across a 3PL and repair-depot network: it traces units through each handoff, finds root causes, quantifies financial exposure, and drafts corrective actions.

## Status

- [x] Step 1: Synthetic operations dataset with planted failures
- [ ] Step 2: MCP server and investigation agent
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
