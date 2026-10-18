"""Generate a synthetic service-parts returns (RMA) dataset with planted failures.

Models an advance-replacement returns network: customers return defective
field-replaceable units (FRUs) to regional 3PL hubs, the 3PL receives them in
its WMS, receipts post to the ERP, and units transfer to repair depots.

Seven failure patterns are planted. The answer key is written to
ground_truth/planted_issues.json and is never exposed to the agent.

All companies, sites, parts, and customers are fictional.

Usage: python data/generate.py
"""

import json
import random
import sqlite3
import string
from datetime import date, timedelta
from pathlib import Path

SEED = 42
START = date(2025, 1, 1)
CUTOFF = date(2026, 9, 30)  # snapshot / "today" for the dataset
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "operations.db"
TRUTH_PATH = ROOT / "ground_truth" / "planted_issues.json"

rng = random.Random(SEED)

SITES = [
    # site_id, name, type, region, operator
    ("MEM", "Memphis Returns Hub", "3PL_HUB", "NA", "Riverline Logistics"),
    ("SDF", "Louisville Returns Hub", "3PL_HUB", "NA", "Bluegrass Fulfillment"),
    ("RNO", "Reno Returns Hub", "3PL_HUB", "NA", "Sierra 3PL"),
    ("AMS", "Amsterdam Returns Hub", "3PL_HUB", "EMEA", "Polder Logistics"),
    ("SIN", "Singapore Returns Hub", "3PL_HUB", "APAC", "Straits Supply Co."),
    ("DEN", "Centennial Repair Depot", "REPAIR_DEPOT", "NA", "Internal"),
    ("PEN", "Penang Repair Depot", "REPAIR_DEPOT", "APAC", "Internal"),
]
HUB_WEIGHTS = {"MEM": 0.30, "SDF": 0.20, "RNO": 0.20, "AMS": 0.18, "SIN": 0.12}
HUB_TO_DEPOT = {"MEM": "DEN", "SDF": "DEN", "RNO": "DEN", "AMS": "PEN", "SIN": "PEN"}

CATEGORIES = [
    # code, name, cost range, count
    ("HDD", "Hard Disk Drive", (180, 650), 40),
    ("SSD", "Solid State Drive", (400, 2200), 30),
    ("CTL", "RAID Controller", (1200, 4800), 20),
    ("PSU", "Power Supply", (220, 900), 20),
    ("FAN", "Fan Module", (60, 240), 14),
    ("TPD", "Tape Drive", (1800, 6500), 24),
    ("ROB", "Library Robotics Assembly", (3500, 12000), 12),
    ("MBD", "System Board", (2500, 9000), 16),
    ("MEM", "Memory Module", (90, 700), 18),
    ("CBL", "Cable Assembly", (25, 180), 16),
]

CUSTOMERS = [
    "Northwind Data", "Contoso Cloud", "Fabrikam Media", "Tailspin Research",
    "Wingtip Analytics", "Adatum Health", "Litware Archives", "Proseware Bank",
    "Coho Genomics", "Fourth Coffee Labs", "Woodgrove Financial", "Alpine Ski Media",
    "Blue Yonder Airlines", "Margie's Travel", "Lucerne Publishing", "Trey Research",
    "Humongous Insurance", "VanArsdel Studios", "Relecloud", "Bellows College",
    "Graphic Design Institute", "Southridge Video", "City Power & Light",
    "School of Fine Art", "Consolidated Messenger", "Wide World Importers",
    "Tasmanian Traders", "Nod Publishers", "Datum Hyperscale", "Lamna Healthcare",
]


def d(x: date) -> str:
    return x.isoformat()


def serial(prefix: str) -> str:
    return prefix + "".join(rng.choices(string.ascii_uppercase + string.digits, k=9))


def build_parts():
    parts = []
    for code, name, (lo, hi), n in CATEGORIES:
        base = rng.randint(10000, 80000)
        for i in range(n):
            pn = f"FRU-{code}-{base + i:05d}"
            cost = round(rng.uniform(lo, hi), 2)
            parts.append((pn, f"{name} {chr(65 + i % 26)}{i // 26 + 1}", name, cost, 1))
    return parts


def sibling(pn: str, part_set: set) -> str:
    """Adjacent part number, the classic keying error (…412 vs …413)."""
    prefix, num = pn.rsplit("-", 1)
    for delta in (1, -1):
        cand = f"{prefix}-{int(num) + delta:05d}"
        if cand in part_set:
            return cand
    return pn


def main():
    parts = build_parts()
    part_cost = {p[0]: p[3] for p in parts}
    part_set = set(part_cost)
    # Zipf-like demand: a few parts fail far more often
    weights = [1 / (i + 1) ** 0.8 for i in range(len(parts))]
    order = list(part_cost)
    rng.shuffle(order)

    rmas, receipts, txns = [], [], []
    truth = {k: [] for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P7")}
    rma_n = rcpt_n = txn_n = 0

    def txn(day, site, ttype, pn, sn, qty, rma_ref=None, ref_doc=None):
        nonlocal txn_n
        txn_n += 1
        txns.append((f"TX{txn_n:07d}", d(day), site, ttype, pn, sn, qty, rma_ref, ref_doc, part_cost[pn]))

    # Physical units sitting at hubs awaiting transfer: hub -> list of (avail_day, pn, sn, erp_posted)
    hub_stock = {h: [] for h in HUB_WEIGHTS}

    day = START
    while day <= CUTOFF:
        for _ in range(max(0, int(rng.gauss(26, 6)))):
            rma_n += 1
            rma_id = f"RMA{rma_n:06d}"
            pn = rng.choices(order, weights)[0]
            sn = serial(pn.split("-")[1][:1])
            hub = rng.choices(list(HUB_WEIGHTS), list(HUB_WEIGHTS.values()))[0]
            cust = rng.choice(CUSTOMERS)
            due = day + timedelta(days=30)
            status = "OPEN"

            # Advance replacement ships from the hub
            txn(day, hub, "ADV_REPLACEMENT_SHIP", pn, None, -1, rma_id)

            # Customer behavior: P6 true non-returns
            returns = rng.random() > 0.04
            lag = max(3, int(rng.gauss(18, 9)))
            recv_day = day + timedelta(days=lag)
            if not returns:
                if due + timedelta(days=60) <= CUTOFF:
                    status = "BILL_PENDING"
                truth["P6"].append(rma_id)
                rmas.append((rma_id, d(day), cust, pn, sn, hub, d(due), status))
                continue
            if recv_day > CUTOFF:
                rmas.append((rma_id, d(day), cust, pn, sn, hub, d(due), status))
                continue

            # 3PL WMS receipt (what physically arrived and what was keyed)
            w_pn, w_sn = pn, sn
            if hub == "MEM" and recv_day >= date(2026, 3, 2) and rng.random() < 0.14:
                w_pn = sibling(pn, part_set)  # P1 wrong part keyed
                truth["P1"].append(rma_id)
            if hub == "SIN" and recv_day >= date(2026, 5, 4) and rng.random() < 0.30:
                w_sn = ""  # P2 serial not captured
                truth["P2"].append(rma_id)

            rcpt_n += 1
            receipts.append((f"WR{rcpt_n:07d}", hub, d(recv_day), rma_id, w_pn, w_sn, 1, "DEFECTIVE"))

            dup = hub == "SDF" and rng.random() < 0.03  # P3 double receipt
            if dup:
                rcpt_n += 1
                receipts.append((f"WR{rcpt_n:07d}", hub, d(recv_day + timedelta(days=rng.randint(0, 2))),
                                 rma_id, w_pn, w_sn, 1, "DEFECTIVE"))
                truth["P3"].append(rma_id)

            # ERP posting via WMS interface; P4 interface outage at RNO
            outage = hub == "RNO" and date(2026, 6, 10) <= recv_day <= date(2026, 6, 24)
            if outage:
                truth["P4"].append(rma_id)
                hub_stock[hub].append((recv_day, w_pn, w_sn, False))
                status = "BILL_PENDING" if due + timedelta(days=60) <= CUTOFF else "OPEN"
                rmas.append((rma_id, d(day), cust, pn, sn, hub, d(due), status))
                continue

            post_day = recv_day + timedelta(days=rng.randint(0, 1))
            txn(post_day, hub, "RMA_RECEIPT", w_pn, w_sn or None, 1, rma_id)
            if dup:
                txn(post_day + timedelta(days=1), hub, "RMA_RECEIPT", w_pn, w_sn or None, 1, rma_id)

            matched = (w_pn == pn) and (w_sn == sn)
            if matched:
                status = "RECEIVED"
            elif due + timedelta(days=60) <= CUTOFF:
                status = "BILL_PENDING"  # ERP can't match the receipt, so it bills the customer
            if hub == "AMS" and date(2026, 7, 1) <= post_day <= date(2026, 8, 31) and rng.random() < 0.06:
                truth["P7"].append(rma_id)  # P7 unit goes missing in the warehouse; ERP still holds it
            else:
                hub_stock[hub].append((post_day, w_pn, w_sn, True))
            rmas.append((rma_id, d(day), cust, pn, sn, hub, d(due), status))
        day += timedelta(days=1)

    # Biweekly hub -> depot transfers of ERP-posted stock
    shipments = []
    to_n = 0
    lost_shipment = None
    for hub, units in hub_stock.items():
        depot = HUB_TO_DEPOT[hub]
        units.sort()
        ship_day = START + timedelta(days=(7 - START.weekday()) % 7 + 1)  # Tuesdays
        i = 0
        remaining = []
        while ship_day <= CUTOFF:
            batch = []
            while i < len(units) and units[i][0] <= ship_day - timedelta(days=5):
                if units[i][3]:
                    batch.append(units[i])
                else:
                    remaining.append(units[i])  # unposted units can't be transferred in ERP
                i += 1
            if batch:
                to_n += 1
                shipments.append((f"TO{to_n:05d}", hub, depot, ship_day, batch))
            ship_day += timedelta(days=14)
        remaining.extend(units[i:])
        hub_stock[hub] = remaining

    # P5: one AMS -> PEN shipment lost in transit (shipped, never received)
    candidates = [s for s in shipments if s[1] == "AMS" and date(2026, 4, 1) <= s[3] <= date(2026, 5, 15)]
    lost_shipment = max(candidates, key=lambda s: sum(part_cost[u[1]] for u in s[4]))
    truth["P5"] = {"transfer_id": lost_shipment[0], "units": len(lost_shipment[4])}

    for to_id, hub, depot, ship_day, batch in shipments:
        transit = rng.randint(2, 4) if HUB_TO_DEPOT[hub] == "DEN" else rng.randint(6, 12)
        arrive = ship_day + timedelta(days=transit)
        for _, pn, sn, _ in batch:
            txn(ship_day, hub, "TRANSFER_OUT", pn, sn or None, -1, None, to_id)
            if to_id == lost_shipment[0] or arrive > CUTOFF:
                continue
            txn(arrive, depot, "TRANSFER_IN", pn, sn or None, 1, None, to_id)
            done = arrive + timedelta(days=rng.randint(5, 30))
            if done <= CUTOFF:
                ttype = "REPAIR_COMPLETE" if rng.random() < 0.86 else "SCRAP"
                txn(done, depot, ttype, pn, sn or None, -1, None, to_id)

    # 3PL physical on-hand snapshot (defective units still at hub)
    snapshot = {}
    for hub, units in hub_stock.items():
        for _, pn, _, _ in units:
            snapshot[(hub, pn)] = snapshot.get((hub, pn), 0) + 1

    write_db(parts, rmas, receipts, txns, snapshot)
    write_truth(truth, rmas, part_cost, lost_shipment)


def write_db(parts, rmas, receipts, txns, snapshot):
    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE sites (site_id TEXT PRIMARY KEY, name TEXT, site_type TEXT, region TEXT, operator TEXT);
    CREATE TABLE parts (part_number TEXT PRIMARY KEY, description TEXT, category TEXT,
                        unit_cost REAL, serialized INTEGER);
    CREATE TABLE rmas (rma_id TEXT PRIMARY KEY, created_date TEXT, customer TEXT, part_number TEXT,
                       serial_number TEXT, return_site TEXT, due_date TEXT, erp_status TEXT);
    CREATE TABLE wms_receipts (receipt_id TEXT PRIMARY KEY, site_id TEXT, received_date TEXT,
                               rma_ref TEXT, part_number TEXT, serial_number TEXT, qty INTEGER,
                               condition TEXT);
    CREATE TABLE erp_transactions (txn_id TEXT PRIMARY KEY, txn_date TEXT, site_id TEXT, txn_type TEXT,
                                   part_number TEXT, serial_number TEXT, qty INTEGER, rma_ref TEXT,
                                   ref_doc TEXT, unit_cost REAL);
    CREATE TABLE wms_onhand_snapshot (site_id TEXT, part_number TEXT, snapshot_date TEXT, qty_on_hand INTEGER);
    CREATE INDEX ix_txn_rma ON erp_transactions(rma_ref);
    CREATE INDEX ix_txn_ref ON erp_transactions(ref_doc);
    CREATE INDEX ix_txn_site_part ON erp_transactions(site_id, part_number);
    CREATE INDEX ix_rcpt_rma ON wms_receipts(rma_ref);
    """)
    con.executemany("INSERT INTO sites VALUES (?,?,?,?,?)", SITES)
    con.executemany("INSERT INTO parts VALUES (?,?,?,?,?)", parts)
    con.executemany("INSERT INTO rmas VALUES (?,?,?,?,?,?,?,?)", rmas)
    con.executemany("INSERT INTO wms_receipts VALUES (?,?,?,?,?,?,?,?)", receipts)
    con.executemany("INSERT INTO erp_transactions VALUES (?,?,?,?,?,?,?,?,?,?)", txns)
    con.executemany("INSERT INTO wms_onhand_snapshot VALUES (?,?,?,?)",
                    [(s, p, d(CUTOFF), q) for (s, p), q in sorted(snapshot.items())])
    con.commit()
    con.close()


def write_truth(truth, rmas, part_cost, lost_shipment):
    rma_part = {r[0]: r[3] for r in rmas}

    def exposure(ids):
        return round(sum(part_cost[rma_part[i]] for i in ids), 2)

    lost_value = round(sum(part_cost[u[1]] for u in lost_shipment[4]), 2)
    out = {
        "dataset": {"seed": SEED, "start": d(START), "cutoff": d(CUTOFF)},
        "patterns": [
            {"id": "P1", "site": "MEM", "name": "Wrong part number keyed at receiving",
             "since": "2026-03-02", "rma_ids": truth["P1"], "value": exposure(truth["P1"])},
            {"id": "P2", "site": "SIN", "name": "Serial number not captured at receiving",
             "since": "2026-05-04", "rma_ids": truth["P2"], "value": exposure(truth["P2"])},
            {"id": "P3", "site": "SDF", "name": "Duplicate receipts inflate ERP inventory",
             "rma_ids": truth["P3"], "value": exposure(truth["P3"])},
            {"id": "P4", "site": "RNO", "name": "WMS-to-ERP interface outage; received units never posted",
             "window": ["2026-06-10", "2026-06-24"], "rma_ids": truth["P4"], "value": exposure(truth["P4"])},
            {"id": "P5", "site": "AMS->PEN", "name": "Transfer shipment lost in transit",
             **truth["P5"], "value": lost_value},
            {"id": "P6", "site": "ALL", "name": "True customer non-returns (bill, not a 3PL failure)",
             "rma_ids": truth["P6"], "value": exposure(truth["P6"])},
            {"id": "P7", "site": "AMS", "name": "Units missing from the warehouse; ERP still shows them on hand",
             "window": ["2026-07-01", "2026-08-31"], "rma_ids": truth["P7"], "value": exposure(truth["P7"])},
        ],
    }
    TRUTH_PATH.write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
