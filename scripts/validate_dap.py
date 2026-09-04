"""Validate the resolution pipeline against the 2025 DAP ground truth.

The DAP audit (Vivian) hand-linked Mushroom Observer records to their UBC
catalog (`F#`), GenBank accession, and the cross-reference actions each record
still needed. `data/dap_ground_truth.csv` is the Observatory-Hill slice of that
sheet (see dap_ground_truth builder). This script runs `resolve.py` and scores
its MO->UBC matching against that gold standard — the C2 experiment the paper
is missing.

What it measures (matching, the clean metric — see the temporal caveat below):
  recall     of the gold (mo_id -> ubc_F) links, did resolve recover the pair?
  wrong      resolve linked the MO record to a *different* UBC catalog
  by tier    recovered split across strict / similar / llm (is the LLM tier
             pulling its weight over rule-based alone?)

Scope: to stay tractable and fair, MO is filtered to the GT ids and BBM to the
genera those MO records fall in (cross-genus matches can't happen under genus
blocking anyway). Run with --no-llm for the rule-based baseline; drop it (and
set LLM_MODEL in .env) for the full pass.

    python validate_dap.py --no-llm          # rule-based baseline
    python validate_dap.py                    # + LLM tier (needs LLM_MODEL)

Temporal caveat: DAP is a 2025 snapshot of what still needed fixing, while
bbm_records.csv is a 2026 refetch — so MO<->F# *matching* is stable ground
truth, but the "add MO #" direction labels may already be partly resolved.
"""

import argparse
import csv
import logging

from config import DATA_DIR, REPORTS_DIR
import resolve as R
from platforms import PLATFORMS, norm_catalog

logger = logging.getLogger(__name__)

GT_PATH = DATA_DIR / "dap_ground_truth.csv"


def load_gt(path=GT_PATH):
    """{mo_id: ubc_F} for gold-matched rows, plus the full label rows."""
    gold, rows = {}, []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
            if r.get("ubc_F"):
                gold[r["mo_id"]] = norm_catalog(r["ubc_F"])
    return gold, rows


def _native(rid):
    return rid.split(":", 1)[1] if ":" in rid else rid


def scope(bbm_rows, mo_rows, meta, gt_ids, gold_cats, per_genus):
    """MO -> GT ids only; BBM -> genera present among those MO records.

    Every gold-catalog BBM record is always kept (so recall is exact); other
    same-genus BBM records are capped at `per_genus` to bound the O(n^2) genus
    blocks. Recall is unaffected; the wrong-link count is measured against a
    capped decoy pool — raise --per-genus for a stricter precision estimate."""
    from collections import defaultdict
    mo_keep = [row for row in mo_rows if _native(row[0]) in gt_ids]
    genera = {row[3] for row in mo_keep if row[3]}
    gold_rows, bucket = [], defaultdict(list)
    for row in bbm_rows:
        if row[3] not in genera:
            continue
        if norm_catalog(meta[row[0]]["catalog"]) in gold_cats:
            gold_rows.append(row)
        else:
            bucket[row[3]].append(row)
    bbm_keep = list(gold_rows)
    for g, rows in bucket.items():
        bbm_keep.extend(rows[:per_genus])
    return bbm_keep, mo_keep


def validate(use_llm, per_genus, bbm_path, force_llm=False):
    gold, gt_rows = load_gt()
    mo = PLATFORMS["mo"]
    bbm_rows, bmeta = R.load_bbm(bbm_path, mo)
    mo_rows, pmeta = R.load_platform(str(DATA_DIR / "mo_records.csv"))
    meta = {**bmeta, **pmeta}

    gt_ids = set(gold)
    gold_cats = set(gold.values())
    bbm_s, mo_s = scope(bbm_rows, mo_rows, meta, gt_ids, gold_cats, per_genus)
    present = {_native(row[0]) for row in mo_s}
    logger.info("GT gold matches: %d | present in mo_records.csv: %d | BBM in-genera: %d",
                len(gold), len(gt_ids & present), len(bbm_s))

    pairs, _dups = R.resolve(bbm_s, mo_s, meta, use_llm=use_llm, force_llm=force_llm)

    # best link found per MO id: {mo_id: (matched_F, tier)}
    linked = {}
    for bbm_id, plat_id, how in pairs:
        mid = _native(plat_id)
        if mid in gold:
            f = norm_catalog(meta[bbm_id]["catalog"])
            # keep a correct link if we find one; else remember any link
            if mid not in linked or f == gold[mid]:
                linked[mid] = (f, how)

    rows, recovered, wrong, missing_absent = [], 0, 0, 0
    tier = {"strict": 0, "similar": 0, "llm": 0}
    for mid, gf in gold.items():
        got = linked.get(mid)
        if got is None:
            status = "absent" if mid in present else "not_in_corpus"
            missing_absent += (mid in present)
            rows.append({"mo_id": mid, "gold_F": gf, "matched_F": "",
                         "tier": "", "result": status})
            continue
        mf, how = got
        ok = (mf == gf)
        recovered += ok
        wrong += (not ok)
        tier[how] = tier.get(how, 0) + ok
        rows.append({"mo_id": mid, "gold_F": gf, "matched_F": mf,
                     "tier": how, "result": "correct" if ok else "wrong_F"})

    n = len(gold)
    n_present = len(gt_ids & present)
    REPORTS_DIR.mkdir(exist_ok=True)
    slug = "llm" if force_llm else ("rules+llm" if use_llm else "rules")
    out = REPORTS_DIR / f"dap_validation_{slug}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    logger.info("=" * 56)
    mode = "LLM-only" if force_llm else ("rule+LLM-leftover" if use_llm else "rule-based")
    logger.info("DAP matching validation (Observatory Hill, %s)", mode)
    logger.info("  gold MO->F# links            : %d", n)
    logger.info("  recovered correctly          : %d  (%.1f%% of all, %.1f%% of present)",
                recovered, 100 * recovered / n if n else 0,
                100 * recovered / n_present if n_present else 0)
    logger.info("    strict / similar / llm     : %d / %d / %d",
                tier.get("strict", 0), tier.get("similar", 0), tier.get("llm", 0))
    logger.info("  linked to WRONG F#           : %d", wrong)
    logger.info("  gold present but unmatched   : %d", missing_absent)
    logger.info("  gold MO id not in corpus     : %d", n - n_present)
    logger.info("Saved per-record → %s", out)


def main():
    ap = argparse.ArgumentParser(description="Validate resolve.py against DAP ground truth")
    ap.add_argument("--no-llm", action="store_true", help="rule-based baseline only")
    ap.add_argument("--force-llm", action="store_true",
                    help="LLM-only pass (ignore rule-based) for a clean rules-vs-LLM comparison")
    ap.add_argument("--per-genus", type=int, default=150,
                    help="cap of non-gold BBM decoys per genus (recall is exact regardless)")
    ap.add_argument("--bbm", default=str(DATA_DIR / "bbm_records.csv"),
                    help="BBM CSV (point at a pre-filtered subset on low-memory hosts)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    validate(use_llm=not args.no_llm, per_genus=args.per_genus, bbm_path=args.bbm,
             force_llm=args.force_llm)


if __name__ == "__main__":
    main()
