"""Deterministic network scan: runs every detector and prints the findings.

No API key needed. This is the baseline the agent builds on, and the input
to evaluate.py.

Usage: python -m rma_agent.scan
"""

import time

from rma_agent import investigate as iv


def scan() -> list[dict]:
    findings = []
    for f in iv.find_receipt_mismatches()["findings"]:
        findings.append({"site": f["site_id"], "issue": f["issue"], "units": f["rmas"], "value": f["value"],
                         "since": f["first_seen"], "rma_ids": f["rma_ids"]})
    for f in iv.find_duplicate_receipts()["findings"]:
        findings.append({"site": f["site_id"], "issue": "duplicate_receipts", "units": f["excess_units"],
                         "value": f["overstated_value"], "rma_ids": f["rma_ids"]})
    for f in iv.find_unposted_receipts()["findings"]:
        w = max(f["date_windows"], key=lambda w: w["units"])
        findings.append({"site": f["site_id"], "issue": "unposted_receipts", "units": f["rmas"], "value": f["value"],
                         "since": w["start"], "until": w["end"], "rma_ids": f["rma_ids"]})
    for g in iv.find_in_transit_gaps()["findings"]:
        findings.append({"site": f"{g['from_site']}->{g['to_site']}", "issue": "lost_in_transit",
                         "units": g["units_missing"], "value": g["value"], "since": g["ship_date"],
                         "transfer_id": g["transfer_id"]})
    for s in iv.compare_onhand()["sites"]:
        if s["unexplained_units"] > 0:
            m = iv.find_missing_warehouse_units(s["site_id"])
            findings.append({"site": s["site_id"], "issue": "missing_from_warehouse", "units": m["units"],
                             "value": m["value"], "since": m["posted_window"][0]["start"], "rma_ids": m["rma_ids"]})
    u = iv.find_unreturned_rmas()
    findings.append({"site": "ALL", "issue": "customer_non_return", "units": u["rmas"], "value": u["value"],
                     "rma_ids": u["rma_ids"]})
    return sorted(findings, key=lambda f: -f["value"])


def main():
    start = time.perf_counter()
    findings = scan()
    elapsed = time.perf_counter() - start
    print(f"{'Site':<10} {'Issue':<24} {'Units':>6} {'Value':>14}  Since")
    for f in findings:
        print(f"{f['site']:<10} {f['issue']:<24} {f['units']:>6} {f['value']:>14,.2f}  {f.get('since', '')}")
    failures = [f for f in findings if f["issue"] != "customer_non_return"]
    print(f"\n3PL and process failures: {len(failures)} issues, "
          f"${sum(f['value'] for f in failures):,.2f} exposure. Scan time {elapsed:.2f}s.")


if __name__ == "__main__":
    main()
