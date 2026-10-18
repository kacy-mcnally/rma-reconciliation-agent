"""Build the exception dashboard: a single self-contained HTML page.

Runs the same scan and investigation tools as the agent, so the dashboard
always agrees with the findings.

Usage: python -m dashboard.build   ->  writes dashboard/index.html
"""

import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from rma_agent import investigate as iv
from rma_agent.scan import scan

OUT = Path(__file__).resolve().parent / "index.html"
PAGES_OUT = Path(__file__).resolve().parent.parent / "docs" / "index.html"  # GitHub Pages copy

ISSUE_LABELS = {
    "wrong_part_number": "Wrong part number keyed at receiving",
    "serial_not_captured": "Serial number not captured at receiving",
    "duplicate_receipts": "Duplicate receipts inflate ERP inventory",
    "unposted_receipts": "Receipts never posted to the ERP",
    "lost_in_transit": "Transfer shipped, never received",
    "missing_from_warehouse": "Units missing from the warehouse",
    "customer_non_return": "Customer never returned the part",
}

ACTIONS = {
    "wrong_part_number": "Require part-number scan-to-match against the RMA at receipt; correct the "
                         "mis-keyed receipts and reverse the customer billing flags.",
    "serial_not_captured": "Make serial number a mandatory scan field in WMS receiving; capture serials "
                           "for the affected units and close their RMAs.",
    "duplicate_receipts": "Block a second receipt against an already-received RMA; reverse the duplicate "
                          "ERP receipts to correct inventory.",
    "unposted_receipts": "Replay the failed WMS-to-ERP messages for the outage window and add an interface "
                         "alert for unacknowledged receipts.",
    "lost_in_transit": "File a carrier claim and require depot receipt confirmation within 14 days of ship.",
    "missing_from_warehouse": "Run a full count of defective stock, investigate the posting window, then "
                              "adjust the ERP once the root cause is confirmed.",
    "customer_non_return": "Bill customers whose parts never arrived; exclude any RMA with a 3PL receipt.",
}


def monthly_counts(con, finding, months):
    """Count a finding's units by month of the event that exposed it."""
    if "transfer_id" in finding:
        counts = Counter({finding["since"][:7]: finding["units"]})
    else:
        ids = finding["rma_ids"]
        marks = ",".join("?" * len(ids))
        if finding["issue"] == "missing_from_warehouse":
            sql = f"SELECT MIN(txn_date) FROM erp_transactions WHERE txn_type='RMA_RECEIPT' AND rma_ref IN ({marks}) GROUP BY rma_ref"
        elif finding["issue"] == "customer_non_return":
            sql = f"SELECT due_date FROM rmas WHERE rma_id IN ({marks})"
        else:
            sql = f"SELECT MIN(received_date) FROM wms_receipts WHERE rma_ref IN ({marks}) GROUP BY rma_ref"
        counts = Counter(r[0][:7] for r in con.execute(sql, ids))
    return [counts.get(m, 0) for m in months]


def build_data():
    findings = scan()
    con = sqlite3.connect(iv.DEFAULT_DB)
    first = con.execute("SELECT MIN(created_date) FROM rmas").fetchone()[0]
    cutoff = date.fromisoformat(con.execute("SELECT MAX(snapshot_date) FROM wms_onhand_snapshot").fetchone()[0])
    months, y, m = [], int(first[:4]), int(first[5:7])
    while (y, m) <= (cutoff.year, cutoff.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    operators = dict(con.execute("SELECT site_id, operator FROM sites"))
    rows = []
    for f in findings:
        site = f["site"].split("->")[0]
        rows.append({
            "site": f["site"].replace("->", " → "), "issue": ISSUE_LABELS[f["issue"]], "key": f["issue"],
            "units": f["units"], "value": round(f["value"], 2), "since": f.get("since", ""),
            "owner": operators.get(site, "Customer billing"), "action": ACTIONS[f["issue"]],
            "monthly": monthly_counts(con, f, months),
        })

    overview = iv.network_overview()
    onhand = {s["site_id"]: s for s in iv.compare_onhand()["sites"]}
    billing = iv.find_unreturned_rmas()
    wrongly_billed = billing["flagged_for_billing_but_received_by_3pl"]
    hubs = []
    for s in overview["sites"]:
        if s["site_type"] != "3PL_HUB":
            continue
        o = onhand[s["site_id"]]
        hubs.append({"site": s["site_id"], "name": s["name"], "operator": s["operator"], "rmas": s["rmas"],
                     "bill_pending": s["bill_pending"], "erp": o["erp_units"], "tpl": o["wms_units"],
                     "variance": o["variance_units"], "variance_value": o["variance_value"]})

    failures = [r for r in rows if r["key"] != "customer_non_return"]
    totals = {
        "as_of": overview["as_of"],
        "exposure": round(sum(r["value"] for r in failures), 2),
        "issues": len(failures),
        "rmas": con.execute("SELECT COUNT(*) FROM rmas").fetchone()[0],
        "transactions": con.execute("SELECT COUNT(*) FROM erp_transactions").fetchone()[0],
        "wrongly_billed": sum(w["rmas"] for w in wrongly_billed),
        "wrongly_billed_value": round(sum(w["value"] for w in wrongly_billed), 2),
        "non_returns": billing["rmas"], "non_return_value": billing["value"],
    }
    return {"totals": totals, "months": months, "findings": rows, "hubs": hubs}


def main():
    data = build_data()
    template = (Path(__file__).resolve().parent / "template.html").read_text()
    html = template.replace("/*__DATA__*/null", json.dumps(data))
    for path in (OUT, PAGES_OUT):
        path.write_text(html)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
