"""GUID-discovery audit — offline coverage / reconciliation for a harvested platform.

Complements link_audit's live per-reference audit: this reads the saved discovery
CSV and reconciles BBM GUIDs against the platform's harvested occurrenceIDs in
BOTH directions, tagged with the harmonization framework:

  present      BBM GUID found on the platform (harvested)          — no breakdown
  present_dup  found on >1 platform record                         — 06 duplicate
  harvest_gap  BBM GUID absent from the platform                   — 03 absence
  no_guid      BBM row carries no GUID (can't harvest-match)       — 02 integrity
  orphan       platform record whose GUID isn't in BBM             — 03 (investigate)

MP-first, generic over any HarvestedPlatform (GBIF works by swapping --platform).

    python guid_discovery.py --platform mycoportal
    python guid_discovery.py --platform gbif --limit 100
"""

import argparse
import csv
import logging
import re

from config import DATA_DIR, REPORTS_DIR
import harmonization as harm
from link_audit import load_bbm_records
from platforms import PLATFORMS, HarvestedPlatform

logger = logging.getLogger(__name__)

UUID = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")


def our_guid(row, bbm_guids):
    """The BBM GUID a platform discovery row carries. Prefer a clean `our_guid`
    column; otherwise recover the uppercase occurrenceID from the mashed
    `ubc_ref` (the token that is actually one of our GUIDs)."""
    g = (row.get("our_guid") or "").strip().upper()
    if g:
        return g
    for m in UUID.finditer(row.get("ubc_ref") or ""):
        u = m.group(0).upper()
        if u in bbm_guids:
            return u
    return ""


def audit(platform, bbm_path, disc_path, limit=None):
    """Reconcile BBM GUIDs against the platform's discovery CSV (both directions)."""
    bbm = load_bbm_records(bbm_path)
    if limit:
        bbm = bbm[:limit]
    bbm_guids = {b.guid.upper() for b in bbm if b.guid}

    # platform GUID -> [rows]; the "" bucket is orphans (no BBM GUID on the row)
    by_guid = {}
    with open(disc_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_guid.setdefault(our_guid(row, bbm_guids), []).append(row)

    counts = {"present": 0, "present_dup": 0, "harvest_gap": 0, "no_guid": 0}
    rows, cat_lists = [], []
    for b in bbm:
        g = b.guid.upper()
        if not g:
            status, cats = "no_guid", ["02"]                # can't harvest-match
        elif g in by_guid:
            n = len(by_guid[g])
            status = "present" if n == 1 else "present_dup"
            cats = harm.classify_breakdowns(cross_ref="bidirectional",
                                            exists=True, coupling="harvested")
            if n > 1:
                cats.append("06")
        else:
            status = "harvest_gap"
            cats = harm.classify_breakdowns(cross_ref="dangling",
                                            exists=False, coupling="harvested")
        counts[status] += 1
        cat_lists.append(cats)
        rows.append({"bbm": b.id, "guid": b.guid, "status": status,
                     "breakdown": ",".join(cats)})

    orphans = by_guid.get("", [])
    return {"n_bbm": len(bbm), "counts": counts, "n_orphan": len(orphans),
            "rows": rows, "cat_lists": cat_lists}


def main():
    parser = argparse.ArgumentParser(description="Offline BBM-platform GUID reconciliation")
    parser.add_argument("--platform", required=True,
                        choices=sorted(n for n, p in PLATFORMS.items()
                                       if isinstance(p, HarvestedPlatform)))
    parser.add_argument("--bbm", default=str(DATA_DIR / "bbm_records.csv"))
    parser.add_argument("--input", default=None,
                        help="platform discovery CSV (default data/<platform>_records.csv)")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    platform = PLATFORMS[args.platform]
    disc_path = args.input or str(DATA_DIR / f"{args.platform}_records.csv")
    res = audit(platform, args.bbm, disc_path, limit=args.limit)

    c, n = res["counts"], res["n_bbm"]
    present = c["present"] + c["present_dup"]
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{args.platform}_guid_discovery.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(res["rows"][0].keys()))
        w.writeheader()
        w.writerows(res["rows"])

    logger.info("%s <-> BBM GUID reconciliation (%d BBM rows)", platform.label, n)
    logger.info("-" * 50)
    logger.info("  present (harvested)   : %d  (%.1f%% coverage)", present, 100 * present / n if n else 0)
    logger.info("    of which duplicates : %d", c["present_dup"])
    logger.info("  harvest gap - 03      : %d", c["harvest_gap"])
    logger.info("  no GUID - 02          : %d", c["no_guid"])
    logger.info("  %s orphans - 03       : %d  (on platform, not in BBM)",
                platform.label, res["n_orphan"])
    for code, k in harm.summarize(res["cat_lists"]).items():
        logger.info("  breakdown %s (%s): %d", code, harm.CATEGORIES[code], k)
    logger.info("Saved -> %s", out)


if __name__ == "__main__":
    main()
