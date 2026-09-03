"""BBM → external-platform cross-reference audit.

Platform-agnostic engine over the `Platform` tree in `platforms.py`: it asks a
platform to establish BBM↔platform correspondences (independent platforms via
the id BBM stored; harvested platforms via our GUID), then classifies each:

  bidirectional  — the correspondence resolves AND the platform record cites us
  unidirectional — resolves but doesn't cite us back
  dangling       — we reference/expect it, but it isn't there (unresolved id, or
                   a harvest gap for downstream platforms)

    python link_audit.py --platform mo
    python link_audit.py --platform mycoportal
    python link_audit.py --platform mycoportal --probe <guid>   # confirm API shape
"""

import argparse
import csv
import logging

from config import DATA_DIR, REPORTS_DIR
# Re-exported so get_mo_records / audit_mo_links / the notebook import from here.
from platforms import (  # noqa: F401
    Platform, IndependentPlatform, HarvestedPlatform,
    MushroomObserver, MyCoPortal, PLATFORMS, fetch_json, BbmRecord, Correspondence,
)

logger = logging.getLogger(__name__)

INPUT = DATA_DIR / "bbm_records.csv"
OUR_ID_COLUMNS = ["catalognumber", "altcatalognumber", "guid"]


# ── BBM loading ─────────────────────────────────────────────────────────────

def load_bbm_records(path=INPUT):
    """Read bbm_records.csv → [BbmRecord]."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            blob = " ".join(str(v) for v in row.values() if v)
            out.append(BbmRecord(
                id=row.get("catalognumber") or row.get("id") or "?",
                catalog=(row.get("catalognumber") or "").strip(),
                altcatalog=(row.get("altcatalognumber") or "").strip(),
                guid=(row.get("guid") or "").strip(),
                text=blob,
            ))
    return out


def scan(platform, path=INPUT):
    """Scan BBM text for a platform's reference ids → (ref_map, n_rows, n_with_ref).

    Uses `ref_patterns` for independent platforms, `legacy_ref_patterns` for
    harvested ones (their in-record annotation is not a queryable id — the real
    link is the GUID — but the count is still worth reporting).
    """
    patterns = getattr(platform, "ref_patterns", None) or getattr(platform, "legacy_ref_patterns", [])
    ref_map, n_rows, n_with_ref = {}, 0, 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n_rows += 1
            blob = " ".join(str(v) for v in row.values() if v)
            ids = set()
            for pat in patterns:
                for m in pat.finditer(blob):
                    ids.add(m.group(1).strip())
            if ids:
                n_with_ref += 1
            our = {row.get(c, "").strip() for c in OUR_ID_COLUMNS if row.get(c)}
            for pid in ids:
                ref_map.setdefault(pid, set()).update(our)
    return ref_map, n_rows, n_with_ref


# ── engine ──────────────────────────────────────────────────────────────────

def classify(exists, cited_back):
    if not exists:
        return "dangling"
    return "bidirectional" if cited_back else "unidirectional"


def audit(platform, input_path=INPUT, limit=None):
    """Establish correspondences via the platform, classify each (grouped by ref)."""
    bbm_records = load_bbm_records(input_path)
    corr = platform.establish_correspondences(bbm_records)

    # group correspondences by the id/guid that established them (one platform
    # record per ref; our_ids is the union across BBM records sharing that ref)
    by_ref = {}
    for c in corr:
        e = by_ref.setdefault(c.ref, {"record": None, "our_ids": set(),
                                      "matched_by": c.matched_by, "bbm_ids": set()})
        e["our_ids"] |= c.bbm.our_ids
        e["bbm_ids"].add(c.bbm.id)
        if c.record is not None:
            e["record"] = c.record

    if limit:
        for ref in list(by_ref)[limit:]:
            del by_ref[ref]

    counts = {"bidirectional": 0, "unidirectional": 0, "dangling": 0}
    ref_map, found, rows = {}, {}, []
    n_with_ref = set()
    for ref, e in by_ref.items():
        rec = e["record"]
        ref_map[ref] = e["our_ids"]
        n_with_ref |= e["bbm_ids"]
        if rec is not None:
            found[ref] = rec
        cited = rec is not None and platform.cites_us(rec, e["our_ids"])
        cls = classify(rec is not None, cited)
        counts[cls] += 1
        rows.append({
            "ref": ref,
            "matched_by": e["matched_by"],
            "our_ids": "; ".join(sorted(e["our_ids"])),
            "exists": rec is not None,
            "cites_us_back": cited,
            "classification": cls,
            "url": platform.record_url(rec) if rec else "",
            "name": platform.display_name(rec) if rec else "",
        })

    return {"n_rows": len(bbm_records), "n_with_ref": len(n_with_ref),
            "n_ids": len(by_ref), "ref_map": ref_map, "found": found,
            "rows": rows, "counts": counts}


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BBM → platform cross-reference audit")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--input", default=str(INPUT))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--probe", metavar="ID",
                        help="fetch one id/guid, print the raw API shape, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    platform = PLATFORMS[args.platform]

    if args.probe is not None:
        platform.probe(args.probe)
        return

    res = audit(platform, input_path=args.input, limit=args.limit)
    logger.info("%s (%s coupling): %d BBM rows; %d correspondences (%d resolved)",
                platform.label, platform.coupling, res["n_rows"], res["n_ids"],
                sum(1 for r in res["rows"] if r["exists"]))
    rows, counts = res["rows"], res["counts"]
    if not rows:
        logger.warning("No correspondences for %s", platform.label)
        return

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{platform.name}_link_audit.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    on = counts["bidirectional"] + counts["unidirectional"]
    logger.info("─" * 50)
    logger.info("  resolve on %-12s : %d", platform.label, on)
    logger.info("    bidirectional         : %d", counts["bidirectional"])
    logger.info("    unidirectional        : %d", counts["unidirectional"])
    logger.info("  dangling / not present   : %d", counts["dangling"])
    logger.info("Saved → %s", out)


if __name__ == "__main__":
    main()
