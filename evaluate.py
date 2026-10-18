"""Score the scan's findings against the planted answer key.

For each planted pattern, finds the best-matching finding and reports
precision and recall on the affected RMAs (or the transfer, for in-transit
loss). Customer non-returns are scored only on RMAs past due long enough to
be detectable at the cutoff.

Usage: python evaluate.py
"""

import json
import sqlite3
from datetime import date, timedelta

from rma_agent.investigate import DEFAULT_DB
from rma_agent.scan import scan

NON_RETURN_DAYS_PAST_DUE = 30


def main():
    truth = json.load(open("ground_truth/planted_issues.json"))
    findings = scan()
    con = sqlite3.connect(DEFAULT_DB)
    cutoff = date.fromisoformat(truth["dataset"]["cutoff"])
    due = dict(con.execute("SELECT rma_id, due_date FROM rmas"))

    print(f"{'Pattern':<8} {'Planted issue':<58} {'Recall':>7} {'Precision':>10}")
    matched = set()
    for p in truth["patterns"]:
        if "transfer_id" in p:
            hit = next((i for i, f in enumerate(findings) if f.get("transfer_id") == p["transfer_id"]), None)
            recall = precision = 1.0 if hit is not None else 0.0
        else:
            expected = set(p["rma_ids"])
            if p["id"] == "P6":
                limit = cutoff - timedelta(days=NON_RETURN_DAYS_PAST_DUE)
                expected = {r for r in expected if date.fromisoformat(due[r]) <= limit}
            best, hit = 0, None
            for i, f in enumerate(findings):
                overlap = len(expected & set(f.get("rma_ids", [])))
                if overlap > best:
                    best, hit = overlap, i
            recall = best / len(expected) if expected else 0.0
            precision = best / len(findings[hit]["rma_ids"]) if hit is not None else 0.0
        if hit is not None:
            matched.add(hit)
        print(f"{p['id']:<8} {p['name'][:58]:<58} {recall:>7.0%} {precision:>10.0%}")

    extra = [f for i, f in enumerate(findings) if i not in matched]
    print(f"\nFindings with no planted cause (false alarms): {len(extra)}")
    for f in extra:
        print(f"  {f['site']} {f['issue']} units={f['units']}")


if __name__ == "__main__":
    main()
