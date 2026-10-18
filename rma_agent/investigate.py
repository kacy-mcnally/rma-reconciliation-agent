"""Investigation tools for the RMA returns network.

Each function answers one reconciliation question against the operations
database and returns plain JSON-serializable data. The same functions back
the MCP server, the Claude agent, and the deterministic scan.
"""

import os
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "operations.db"
SAMPLE_SIZE = 5


def _connect() -> sqlite3.Connection:
    path = Path(os.environ.get("RMA_DB", DEFAULT_DB))
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python data/generate.py")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


def _cutoff(con) -> str:
    return con.execute("SELECT MAX(snapshot_date) FROM wms_onhand_snapshot").fetchone()[0]


def _site_filter(site_id, column):
    return (f" AND {column} = ?", (site_id,)) if site_id else ("", ())


def _windows(days: list[str], gap: int = 2) -> list[dict]:
    """Collapse sorted ISO dates into contiguous windows (gaps up to `gap` days)."""
    out = []
    for d in sorted(days):
        cur = date.fromisoformat(d)
        if out and (cur - date.fromisoformat(out[-1]["end"])).days <= gap:
            out[-1]["end"] = d
            out[-1]["units"] += 1
        else:
            out.append({"start": d, "end": d, "units": 1})
    return out


def network_overview() -> dict:
    """Summarize the returns network: RMA status and value by site, and the
    3PL physical vs ERP defective on-hand at each hub. Start here."""
    con = _connect()
    sites = _rows(con, """
        SELECT s.site_id, s.name, s.site_type, s.operator,
               COUNT(r.rma_id) AS rmas,
               SUM(r.erp_status = 'RECEIVED') AS received,
               SUM(r.erp_status = 'OPEN') AS open,
               SUM(r.erp_status = 'BILL_PENDING') AS bill_pending,
               ROUND(SUM(CASE WHEN r.erp_status = 'BILL_PENDING' THEN p.unit_cost END), 2) AS bill_pending_value
        FROM sites s
        LEFT JOIN rmas r ON r.return_site = s.site_id
        LEFT JOIN parts p ON p.part_number = r.part_number
        GROUP BY s.site_id ORDER BY s.site_type, s.site_id""")
    onhand = {r["site_id"]: r for r in _onhand_by_site(con)}
    for s in sites:
        if s["site_id"] in onhand:
            s["defective_onhand_erp"] = onhand[s["site_id"]]["erp_units"]
            s["defective_onhand_3pl"] = onhand[s["site_id"]]["wms_units"]
    return {"as_of": _cutoff(con), "sites": sites}


def find_receipt_mismatches(site_id: str = "") -> dict:
    """Find RMAs where what the 3PL keyed at receiving does not match the RMA:
    wrong part number, missing serial, or wrong serial. Mismatched receipts
    cannot auto-close the RMA, so the customer can be billed for a part they
    returned. Grouped by site and type, with the month the problem started.

    Args:
        site_id: Optional site code (e.g. MEM) to limit the search.
    """
    con = _connect()
    where, params = _site_filter(site_id, "w.site_id")
    rows = _rows(con, f"""
        SELECT r.rma_id, w.site_id, w.received_date, r.part_number AS rma_part,
               w.part_number AS received_part, r.serial_number AS rma_serial,
               w.serial_number AS received_serial, p.unit_cost, r.erp_status
        FROM wms_receipts w
        JOIN rmas r ON r.rma_id = w.rma_ref
        JOIN parts p ON p.part_number = r.part_number
        WHERE (w.part_number != r.part_number OR w.serial_number != r.serial_number){where}
        GROUP BY r.rma_id""", params)
    groups = defaultdict(list)
    for r in rows:
        if r["received_part"] != r["rma_part"]:
            kind = "wrong_part_number"
        elif r["received_serial"] == "":
            kind = "serial_not_captured"
        else:
            kind = "wrong_serial"
        groups[(r["site_id"], kind)].append(r)
    findings = []
    for (site, kind), items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_month = defaultdict(int)
        for i in items:
            by_month[i["received_date"][:7]] += 1
        f = {
            "site_id": site, "issue": kind, "rmas": len(items),
            "value": round(sum(i["unit_cost"] for i in items), 2),
            "customers_flagged_for_billing": sum(i["erp_status"] == "BILL_PENDING" for i in items),
            "first_seen": min(i["received_date"] for i in items),
            "by_month": dict(sorted(by_month.items())),
            "rma_ids": [i["rma_id"] for i in items],
            "examples": items[:SAMPLE_SIZE],
        }
        if kind == "wrong_part_number":
            f["adjacent_part_number_share"] = round(
                sum(abs(int(i["rma_part"][-5:]) - int(i["received_part"][-5:])) == 1 for i in items) / len(items), 2)
        findings.append(f)
    return {"findings": findings}


def find_duplicate_receipts(site_id: str = "") -> dict:
    """Find RMAs received more than once by the 3PL and posted more than once
    in the ERP, which inflates ERP inventory with units that do not exist.

    Args:
        site_id: Optional site code (e.g. SDF) to limit the search.
    """
    con = _connect()
    where, params = _site_filter(site_id, "t.site_id")
    rows = _rows(con, f"""
        SELECT t.rma_ref AS rma_id, t.site_id, COUNT(*) AS erp_receipts,
               MIN(t.txn_date) AS first_receipt, p.unit_cost
        FROM erp_transactions t JOIN parts p ON p.part_number = t.part_number
        WHERE t.txn_type = 'RMA_RECEIPT'{where}
        GROUP BY t.rma_ref HAVING COUNT(*) > 1""", params)
    by_site = defaultdict(list)
    for r in rows:
        by_site[r["site_id"]].append(r)
    return {"findings": [{
        "site_id": s, "rmas": len(items),
        "excess_units": sum(i["erp_receipts"] - 1 for i in items),
        "overstated_value": round(sum((i["erp_receipts"] - 1) * i["unit_cost"] for i in items), 2),
        "rate_of_site_receipts": round(len(items) / con.execute(
            "SELECT COUNT(DISTINCT rma_ref) FROM erp_transactions WHERE txn_type='RMA_RECEIPT' AND site_id=?",
            (s,)).fetchone()[0], 4),
        "rma_ids": [i["rma_id"] for i in items],
        "examples": items[:SAMPLE_SIZE],
    } for s, items in sorted(by_site.items(), key=lambda kv: -len(kv[1]))]}


def find_unposted_receipts(site_id: str = "") -> dict:
    """Find units the 3PL physically received that never posted to the ERP,
    grouped into date windows (an interface outage shows as a tight window).
    Unposted RMAs stay open in the ERP and age into customer billing.

    Args:
        site_id: Optional site code (e.g. RNO) to limit the search.
    """
    con = _connect()
    where, params = _site_filter(site_id, "w.site_id")
    rows = _rows(con, f"""
        SELECT w.rma_ref AS rma_id, w.site_id, w.received_date, p.unit_cost, r.erp_status
        FROM wms_receipts w
        JOIN rmas r ON r.rma_id = w.rma_ref
        JOIN parts p ON p.part_number = r.part_number
        WHERE NOT EXISTS (SELECT 1 FROM erp_transactions t
                          WHERE t.rma_ref = w.rma_ref AND t.txn_type = 'RMA_RECEIPT'){where}
        GROUP BY w.rma_ref""", params)
    by_site = defaultdict(list)
    for r in rows:
        by_site[r["site_id"]].append(r)
    return {"findings": [{
        "site_id": s, "rmas": len(items),
        "value": round(sum(i["unit_cost"] for i in items), 2),
        "customers_flagged_for_billing": sum(i["erp_status"] == "BILL_PENDING" for i in items),
        "date_windows": _windows([i["received_date"] for i in items]),
        "rma_ids": [i["rma_id"] for i in items],
    } for s, items in sorted(by_site.items(), key=lambda kv: -len(kv[1]))]}


def find_in_transit_gaps(min_days: int = 21) -> dict:
    """Find hub-to-depot transfers shipped but never received at the depot
    after `min_days`, i.e. shipments lost or stuck in transit.

    Args:
        min_days: Days since shipping before a transfer counts as missing.
    """
    con = _connect()
    cutoff = date.fromisoformat(_cutoff(con))
    rows = _rows(con, """
        SELECT o.ref_doc AS transfer_id, o.site_id AS from_site, MIN(o.txn_date) AS ship_date,
               COUNT(*) AS units_shipped, ROUND(SUM(o.unit_cost), 2) AS value,
               (SELECT COUNT(*) FROM erp_transactions i
                WHERE i.ref_doc = o.ref_doc AND i.txn_type = 'TRANSFER_IN') AS units_received,
               (SELECT MIN(site_id) FROM erp_transactions i
                WHERE i.ref_doc = o.ref_doc AND i.txn_type = 'TRANSFER_IN') AS to_site
        FROM erp_transactions o WHERE o.txn_type = 'TRANSFER_OUT'
        GROUP BY o.ref_doc""")
    lanes = {"MEM": "DEN", "SDF": "DEN", "RNO": "DEN", "AMS": "PEN", "SIN": "PEN"}
    gaps = []
    for r in rows:
        age = (cutoff - date.fromisoformat(r["ship_date"])).days
        if r["units_received"] < r["units_shipped"] and age >= min_days:
            r["to_site"] = r["to_site"] or lanes.get(r["from_site"])
            r["days_since_ship"] = age
            r["units_missing"] = r["units_shipped"] - r["units_received"]
            gaps.append(r)
    return {"as_of": str(cutoff), "findings": sorted(gaps, key=lambda g: -g["value"])}


def _onhand_by_site(con, site_id: str = "") -> list[dict]:
    where, params = _site_filter(site_id, "site_id")
    erp = {(r["site_id"], r["part_number"]): r["q"] for r in _rows(con, f"""
        SELECT site_id, part_number, SUM(qty) AS q FROM erp_transactions
        WHERE txn_type IN ('RMA_RECEIPT', 'TRANSFER_OUT'){where}
        GROUP BY site_id, part_number""", params)}
    wms = {(r["site_id"], r["part_number"]): r["qty_on_hand"] for r in _rows(con, f"""
        SELECT site_id, part_number, qty_on_hand FROM wms_onhand_snapshot WHERE 1=1{where}""", params)}
    cost = {r["part_number"]: r["unit_cost"] for r in _rows(con, "SELECT part_number, unit_cost FROM parts")}
    sites = defaultdict(lambda: {"erp_units": 0, "wms_units": 0, "variance_value": 0.0, "parts": []})
    for key in set(erp) | set(wms):
        e, w = erp.get(key, 0), wms.get(key, 0)
        s = sites[key[0]]
        s["erp_units"] += e
        s["wms_units"] += w
        if e != w:
            v = (e - w) * cost[key[1]]
            s["variance_value"] += v
            s["parts"].append({"part_number": key[1], "erp": e, "3pl": w, "variance_value": round(v, 2)})
    out = []
    for sid, s in sorted(sites.items()):
        s["parts"].sort(key=lambda p: -abs(p["variance_value"]))
        out.append({"site_id": sid, "erp_units": s["erp_units"], "wms_units": s["wms_units"],
                    "variance_units": s["erp_units"] - s["wms_units"],
                    "variance_value": round(s["variance_value"], 2), "top_parts": s["parts"][:SAMPLE_SIZE]})
    return out


def compare_onhand(site_id: str = "") -> dict:
    """Compare ERP defective-returns on-hand with the 3PL's physical on-hand
    at each hub, and attribute the variance: duplicate ERP receipts (ERP high),
    receipts never posted (ERP low), and any unexplained remainder, which
    points to units missing from the warehouse.

    Args:
        site_id: Optional site code (e.g. AMS) to limit the comparison.
    """
    con = _connect()
    sites = _onhand_by_site(con, site_id)
    dups = {f["site_id"]: f["excess_units"] for f in find_duplicate_receipts(site_id)["findings"]}
    unposted = {f["site_id"]: f["rmas"] for f in find_unposted_receipts(site_id)["findings"]}
    for s in sites:
        d, u = dups.get(s["site_id"], 0), unposted.get(s["site_id"], 0)
        s["explained"] = {"duplicate_erp_receipts": d, "unposted_receipts": -u}
        s["unexplained_units"] = s["variance_units"] - d + u
    return {"as_of": _cutoff(con), "note": "variance = ERP minus 3PL; positive means ERP shows units the 3PL does not have",
            "sites": sites}


def find_missing_warehouse_units(site_id: str) -> dict:
    """List the specific RMAs whose units the ERP still shows at a hub but the
    3PL cannot find: posted, never transferred, absent from the 3PL count, and
    not explained by duplicate receipts. Use after compare_onhand shows an
    unexplained variance.

    Args:
        site_id: Hub site code (e.g. AMS).
    """
    con = _connect()
    rows = _rows(con, """
        SELECT t.rma_ref AS rma_id, t.txn_date AS posted_date, t.part_number, t.serial_number, t.unit_cost
        FROM erp_transactions t
        WHERE t.txn_type = 'RMA_RECEIPT' AND t.site_id = ?
          AND NOT EXISTS (SELECT 1 FROM erp_transactions o WHERE o.txn_type = 'TRANSFER_OUT'
                          AND o.site_id = t.site_id AND o.serial_number IS t.serial_number
                          AND o.part_number = t.part_number AND o.txn_date >= t.txn_date)
        GROUP BY t.rma_ref ORDER BY t.txn_date""", (site_id,))
    wms = defaultdict(int)
    for r in _rows(con, "SELECT part_number, qty_on_hand FROM wms_onhand_snapshot WHERE site_id = ?", (site_id,)):
        wms[r["part_number"]] = r["qty_on_hand"]
    # Units physically present are the most recent receipts of each part; older ones are the missing ones
    missing = []
    by_part = defaultdict(list)
    for r in rows:
        by_part[r["part_number"]].append(r)
    for pn, items in by_part.items():
        keep = wms[pn]
        missing.extend(items[: max(0, len(items) - keep)])
    missing.sort(key=lambda r: r["posted_date"])
    return {"site_id": site_id, "units": len(missing),
            "value": round(sum(m["unit_cost"] for m in missing), 2),
            "posted_window": _windows([m["posted_date"] for m in missing], gap=14),
            "rma_ids": [m["rma_id"] for m in missing], "examples": missing[:SAMPLE_SIZE]}


def find_unreturned_rmas(min_days_past_due: int = 30) -> dict:
    """Find RMAs where no part ever arrived at any 3PL, past due by at least
    `min_days_past_due`. These are genuine customer non-returns (bill the
    customer, not a 3PL failure). Also reports RMAs flagged for billing even
    though the 3PL did receive the part, which would bill customers wrongly.

    Args:
        min_days_past_due: Days past the return due date to count as unreturned.
    """
    con = _connect()
    cutoff = date.fromisoformat(_cutoff(con))
    limit = str(cutoff - timedelta(days=min_days_past_due))
    rows = _rows(con, """
        SELECT r.rma_id, r.return_site, r.customer, r.due_date, r.erp_status, p.unit_cost
        FROM rmas r JOIN parts p ON p.part_number = r.part_number
        WHERE r.due_date <= ? AND NOT EXISTS (SELECT 1 FROM wms_receipts w WHERE w.rma_ref = r.rma_id)""", (limit,))
    wrongly_billed = _rows(con, """
        SELECT r.return_site AS site_id, COUNT(*) AS rmas, ROUND(SUM(p.unit_cost), 2) AS value
        FROM rmas r JOIN parts p ON p.part_number = r.part_number
        WHERE r.erp_status = 'BILL_PENDING' AND EXISTS (SELECT 1 FROM wms_receipts w WHERE w.rma_ref = r.rma_id)
        GROUP BY r.return_site ORDER BY rmas DESC""")
    by_site = defaultdict(lambda: [0, 0.0])
    by_customer = defaultdict(lambda: [0, 0.0])
    for r in rows:
        by_site[r["return_site"]][0] += 1
        by_site[r["return_site"]][1] += r["unit_cost"]
        by_customer[r["customer"]][0] += 1
        by_customer[r["customer"]][1] += r["unit_cost"]
    top_customers = sorted(by_customer.items(), key=lambda kv: -kv[1][1])[:SAMPLE_SIZE]
    return {
        "as_of": str(cutoff), "rmas": len(rows), "value": round(sum(r["unit_cost"] for r in rows), 2),
        "by_site": {s: {"rmas": n, "value": round(v, 2)} for s, (n, v) in sorted(by_site.items())},
        "top_customers": [{"customer": c, "rmas": n, "value": round(v, 2)} for c, (n, v) in top_customers],
        "flagged_for_billing_but_received_by_3pl": wrongly_billed,
        "rma_ids": [r["rma_id"] for r in rows],
    }


def trace_rma(rma_id: str) -> dict:
    """Trace one RMA end to end: the RMA, what the 3PL received, every ERP
    transaction for it, and the unit's transfer and repair history.

    Args:
        rma_id: RMA number, e.g. RMA000123.
    """
    con = _connect()
    rma = _rows(con, "SELECT * FROM rmas WHERE rma_id = ?", (rma_id,))
    if not rma:
        return {"error": f"{rma_id} not found"}
    receipts = _rows(con, "SELECT * FROM wms_receipts WHERE rma_ref = ?", (rma_id,))
    txns = _rows(con, "SELECT * FROM erp_transactions WHERE rma_ref = ? ORDER BY txn_date", (rma_id,))
    serials = {r["serial_number"] for r in receipts if r["serial_number"]}
    downstream = []
    for sn in serials:
        downstream += _rows(con, """SELECT * FROM erp_transactions WHERE serial_number = ?
                                    AND rma_ref IS NULL ORDER BY txn_date""", (sn,))
    return {"rma": rma[0], "wms_receipts": receipts, "erp_transactions": txns, "downstream": downstream}


def run_sql(query: str) -> dict:
    """Run a read-only SQL SELECT against the operations database for questions
    the other tools do not cover. Tables: sites, parts, rmas, wms_receipts,
    erp_transactions, wms_onhand_snapshot. Returns at most 200 rows.

    Args:
        query: A single SELECT statement.
    """
    if not query.lstrip().lower().startswith(("select", "with")):
        return {"error": "Only SELECT queries are allowed."}
    con = _connect()
    try:
        cur = con.execute(query)
    except sqlite3.Error as e:
        return {"error": str(e)}
    cols = [c[0] for c in cur.description or []]
    rows = cur.fetchmany(201)
    return {"columns": cols, "rows": [list(r) for r in rows[:200]], "truncated": len(rows) > 200}


TOOLS = [network_overview, find_receipt_mismatches, find_duplicate_receipts, find_unposted_receipts,
         find_in_transit_gaps, compare_onhand, find_missing_warehouse_units, find_unreturned_rmas,
         trace_rma, run_sql]
