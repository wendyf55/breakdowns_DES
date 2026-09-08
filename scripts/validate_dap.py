"""Validate the resolution pipeline against the 2025 DAP ground truth.

The DAP audit (Vivian) hand-linked Mushroom Observer records to their UBC
catalog (`F#`), GenBank accession, and the cross-reference actions each record
still needed. `data/dap_ground_truth.csv` is the Observatory-Hill slice of that
sheet, generated from the raw DAP exports by `build_dap_ground_truth.py`. This
script runs `resolve.py` and scores its MO->UBC matching against that gold
standard — the C2 experiment the paper is missing.

What it measures (matching, the clean metric — see the temporal caveat below):
  recall     of the gold (mo_id -> ubc_F) links, did resolve recover the pair?
  wrong      resolve linked the MO record to a *different* UBC catalog
  by tier    recovered split across strict / similar / llm (is the LLM tier
             pulling its weight over rule-based alone?)
  diagnostics side-by-side gold/matched/MO fields for wrong links, plus first
             deterministic failure reason for unmatched gold records

Scope: to stay tractable and fair, MO is filtered to the GT ids and BBM to the
genera those MO records fall in (cross-genus matches can't happen under genus
blocking anyway). Run with --no-llm for the rule-based baseline; drop it (and
set LLM_MODEL in .env) for the full pass.

    python validate_dap.py --no-llm          # rule-based baseline
    python validate_dap.py                    # + bounded LLM tier (needs LLM_MODEL)
    python validate_dap.py --only-mo-ids 166513,210193 --label review_subset

Temporal caveat: DAP is a 2025 snapshot of what still needed fixing, while
bbm_records.csv is a 2026 refetch — so MO<->F# *matching* is stable ground
truth, but the "add MO #" direction labels may already be partly resolved.
"""

import argparse
import csv
import logging
import os
from collections import Counter

from config import DATA_DIR, REPORTS_DIR
import resolve as R
from platforms import PLATFORMS, norm_catalog

logger = logging.getLogger(__name__)

GT_PATH = DATA_DIR / "dap_ground_truth.csv"
COMPARE_KEYS = ("platform", "sci_name", "genus", "species", "collector", "date", "locality")


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


def _fields(row, prefix, id_key=None):
    id_key = id_key or f"{prefix}_id"
    if row is None:
        return {id_key: "", **{f"{prefix}_{k}": "" for k in COMPARE_KEYS}}
    values = dict(zip(("id", *COMPARE_KEYS), row))
    return {id_key: values["id"], **{f"{prefix}_{k}": values[k] for k in COMPARE_KEYS}}


def _catalog_lookup(bbm_rows, meta):
    by_cat = {}
    for row in bbm_rows:
        cat = norm_catalog(meta[row[0]]["catalog"])
        if cat:
            by_cat.setdefault(cat, []).append(row)
    return by_cat


def _mo_lookup(mo_rows):
    return {_native(row[0]): row for row in mo_rows}


def _date_conflict(a, b):
    ad, bd = a[6], b[6]
    if len(ad) == 10 and len(bd) == 10 and ad != bd:
        return "date_conflict_exact"
    if R._year(ad) and R._year(bd) and R._year(ad) != R._year(bd):
        return "date_conflict_year"
    return ""


def _evidence(row, mo_row):
    name_sim = R.edit_sim(row[2], mo_row[2])
    same_name = bool(row[2] and row[2] == mo_row[2])
    similar_name = name_sim >= R.NAME_SIM
    synonym_name, synonym_evidence = R.synonym_match(row[2], mo_row[2])
    same_genus = bool(row[3] and row[3] == mo_row[3])
    exact_date = bool(len(row[6]) == 10 and row[6] == mo_row[6])
    same_year = bool(R._year(row[6]) and R._year(row[6]) == R._year(mo_row[6]))
    locality_overlap = R._overlap(row[7], mo_row[7], 2)
    collector_overlap = R._overlap(row[5], mo_row[5], 1)
    strict_like = same_name and exact_date and (locality_overlap or collector_overlap)
    similar_like = (
        (same_genus or synonym_name) and not _date_conflict(row, mo_row)
        and (similar_name or synonym_name) and (exact_date or same_year)
        and (locality_overlap or collector_overlap)
    )
    score = (
        3 * same_name + 2 * (similar_name and not same_name) + 2 * same_genus
        + 2 * synonym_name + 3 * exact_date + same_year
        + 2 * locality_overlap + collector_overlap
    )
    return {
        "score": score,
        "name_sim": round(name_sim, 3),
        "same_genus": same_genus,
        "same_name": same_name,
        "similar_name": similar_name,
        "synonym_name": synonym_name,
        "synonym_evidence": synonym_evidence,
        "exact_date": exact_date,
        "same_year": same_year,
        "locality_overlap": locality_overlap,
        "collector_overlap": collector_overlap,
        "strict_like": strict_like,
        "similar_like": similar_like,
    }


def _best_gold_row(gold_rows, mo_row):
    if not gold_rows:
        return None
    if mo_row is None:
        return gold_rows[0]
    return max(gold_rows, key=lambda row: _evidence(row, mo_row)["score"])


def _diagnose_unmatched(mid, gf, mo_by_id, bbm_by_cat):
    mo_row = mo_by_id.get(mid)
    gold_rows = bbm_by_cat.get(gf, [])
    gold_row = _best_gold_row(gold_rows, mo_row)
    if mo_row is None:
        return "mo_not_in_corpus", "DAP MO id is not present in data/mo_records.csv", gold_row, None
    if not gold_rows:
        return "gold_bbm_not_in_extract", "Gold UBC F# is not present in the BBM CSV extract", None, mo_row
    if not any(row[3] and row[3] == mo_row[3] for row in gold_rows):
        if any(R.synonym_match(row[2], mo_row[2])[0] for row in gold_rows):
            return (
                "synonym_match_without_rule_context",
                "Gold BBM and MO names share a synonym group, but date/locality/collector evidence did not pass",
                gold_row,
                mo_row,
            )
        return "genus_mismatch_blocks_match", "Gold BBM genus and MO genus differ or one is missing", gold_row, mo_row
    conflict = _date_conflict(gold_row, mo_row)
    if conflict:
        return conflict, "Gold BBM date and MO date are incompatible", gold_row, mo_row
    ev = _evidence(gold_row, mo_row)
    if not (ev["similar_name"] or ev["synonym_name"]):
        return "name_below_rule_threshold", f"Best gold name similarity is {ev['name_sim']}", gold_row, mo_row
    if not (ev["locality_overlap"] or ev["collector_overlap"]):
        return "no_locality_or_collector_overlap", "Name/genus passed, but locality and collector evidence did not overlap", gold_row, mo_row
    if ev["strict_like"] or ev["similar_like"]:
        return "candidate_should_match_rules", "Gold pair appears rule-matchable; inspect grouping/tie behavior", gold_row, mo_row
    return "weak_combined_evidence", f"Best gold evidence score is {ev['score']}", gold_row, mo_row


def _diagnose_wrong(matched_bbm, gold_bbm, mo_row):
    if gold_bbm is None or mo_row is None:
        return "wrong_f_link", "Matched a different F#; gold or MO diagnostic row is missing"
    matched_ev = _evidence(matched_bbm, mo_row)
    gold_ev = _evidence(gold_bbm, mo_row)
    if matched_bbm[2] == gold_bbm[2] and matched_bbm[7] == gold_bbm[7]:
        reason = "ambiguous_same_name_locality"
    elif matched_ev["score"] > gold_ev["score"]:
        reason = "decoy_scores_above_gold"
    else:
        reason = "wrong_f_link"
    detail = (
        f"matched_score={matched_ev['score']}; gold_score={gold_ev['score']}; "
        f"matched_date={matched_bbm[6]}; gold_date={gold_bbm[6]}; mo_date={mo_row[6]}"
    )
    return reason, detail


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


def validate(use_llm, per_genus, bbm_path, force_llm=False, only_mo_ids=None, label=None):
    if (use_llm or force_llm) and not os.getenv("LLM_MODEL"):
        raise RuntimeError(
            "LLM validation requested but LLM_MODEL is not set. "
            "Set LLM_MODEL in .env, or run with --no-llm / RUN_LLM_VALIDATION=False."
        )

    gold, gt_rows = load_gt()
    if only_mo_ids:
        wanted = {_native(str(mid)) for mid in only_mo_ids}
        gold = {mid: fnum for mid, fnum in gold.items() if mid in wanted}
    mo = PLATFORMS["mo"]
    bbm_rows, bmeta = R.load_bbm(bbm_path, mo)
    mo_rows, pmeta = R.load_platform(str(DATA_DIR / "mo_records.csv"))
    meta = {**bmeta, **pmeta}
    bbm_by_cat = _catalog_lookup(bbm_rows, meta)
    mo_by_id = _mo_lookup(mo_rows)
    rows_by_id = {row[0]: row for row in (*bbm_rows, *mo_rows)}

    gt_ids = set(gold)
    gold_cats = set(gold.values())
    bbm_s, mo_s = scope(bbm_rows, mo_rows, meta, gt_ids, gold_cats, per_genus)
    present = {_native(row[0]) for row in mo_s}
    logger.info("GT gold matches: %d | present in mo_records.csv: %d | BBM in-genera: %d",
                len(gold), len(gt_ids & present), len(bbm_s))

    pairs, _dups = R.resolve(bbm_s, mo_s, meta, use_llm=use_llm, force_llm=force_llm)

    # best link found per MO id: {mo_id: (matched_F, tier, bbm_id, plat_id)}
    linked = {}
    for bbm_id, plat_id, how in pairs:
        mid = _native(plat_id)
        if mid in gold:
            f = norm_catalog(meta[bbm_id]["catalog"])
            # keep a correct link if we find one; else remember any link
            if mid not in linked or f == gold[mid]:
                linked[mid] = (f, how, bbm_id, plat_id)

    rows, recovered, wrong, missing_absent, review_required = [], 0, 0, 0, 0
    tier = {"strict": 0, "similar": 0, "llm": 0}
    synonym_matches = synonym_correct = synonym_wrong = 0
    for mid, gf in gold.items():
        got = linked.get(mid)
        if got is None:
            status = "absent" if mid in present else "not_in_corpus"
            missing_absent += (mid in present)
            reason, reason_detail, gold_row, mo_row = _diagnose_unmatched(
                mid, gf, mo_by_id, bbm_by_cat
            )
            row = {"mo_id": mid, "gold_F": gf, "matched_F": "",
                   "tier": "", "result": status, "diagnostic_reason": reason,
                   "diagnostic_detail": reason_detail, "review_required": "",
                   "accepted": "", "llm_reason": "", "guardrail": "",
                   "synonym_match": "", "synonym_evidence": "",
                   "candidate_group_size": "", "candidate_group_ids": ""}
            row.update(_fields(gold_row, "gold_bbm"))
            row.update(_fields(None, "matched_bbm"))
            row.update(_fields(mo_row, "mo", id_key="mo_record_id"))
            rows.append(row)
            continue
        mf, how, bbm_id, plat_id = got
        ok = (mf == gf)
        detail = R.match_detail(bbm_id, plat_id, how)
        matched_bbm = rows_by_id.get(bbm_id)
        mo_row = rows_by_id.get(plat_id) or mo_by_id.get(mid)
        gold_row = _best_gold_row(bbm_by_cat.get(gf, []), mo_row)
        reason, reason_detail = ("", "") if ok else _diagnose_wrong(matched_bbm, gold_row, mo_row)
        recovered += ok
        wrong += (not ok)
        tier[how] = tier.get(how, 0) + ok
        review_required += bool(detail.get("review_required", how == R.LLM))
        has_synonym = bool(detail.get("synonym_match"))
        synonym_matches += has_synonym
        synonym_correct += bool(ok and has_synonym)
        synonym_wrong += bool((not ok) and has_synonym)
        row = {"mo_id": mid, "gold_F": gf, "matched_F": mf,
               "tier": how, "result": "correct" if ok else "wrong_F",
               "diagnostic_reason": reason, "diagnostic_detail": reason_detail,
               "review_required": detail.get("review_required", how == R.LLM),
               "accepted": detail.get("accepted", how != R.LLM),
               "llm_reason": detail.get("llm_reason", ""),
               "guardrail": detail.get("guardrail", ""),
               "synonym_match": detail.get("synonym_match", False),
               "synonym_evidence": detail.get("synonym_evidence", ""),
               "candidate_group_size": detail.get("candidate_group_size", ""),
               "candidate_group_ids": detail.get("candidate_group_ids", "")}
        row.update(_fields(gold_row, "gold_bbm"))
        row.update(_fields(matched_bbm, "matched_bbm"))
        row.update(_fields(mo_row, "mo", id_key="mo_record_id"))
        rows.append(row)

    n = len(gold)
    n_present = len(gt_ids & present)
    REPORTS_DIR.mkdir(exist_ok=True)
    slug = label or ("llm" if force_llm else ("rules+llm" if use_llm else "rules"))
    out = REPORTS_DIR / f"dap_validation_{slug}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    wrong_out = REPORTS_DIR / f"dap_validation_{slug}_wrong_links.csv"
    unmatched_out = REPORTS_DIR / f"dap_validation_{slug}_unmatched_reasons.csv"
    unmatched_counts_out = REPORTS_DIR / f"dap_validation_{slug}_unmatched_reason_counts.csv"
    wrong_rows = [r for r in rows if r["result"] == "wrong_F"]
    unmatched_rows = [r for r in rows if r["result"] in {"absent", "not_in_corpus"}]
    for path, subset in ((wrong_out, wrong_rows), (unmatched_out, unmatched_rows)):
        if subset:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
                w.writeheader(); w.writerows(subset)
    if unmatched_rows:
        counts = Counter(r["diagnostic_reason"] for r in unmatched_rows)
        with open(unmatched_counts_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["diagnostic_reason", "n"], lineterminator="\n")
            w.writeheader()
            for reason, n_reason in counts.most_common():
                w.writerow({"diagnostic_reason": reason, "n": n_reason})

    logger.info("=" * 56)
    mode = "LLM-only" if force_llm else ("rule+LLM-leftover" if use_llm else "rule-based")
    if only_mo_ids:
        mode = f"{mode} subset"
    logger.info("DAP matching validation (Observatory Hill, %s)", mode)
    logger.info("  gold MO->F# links            : %d", n)
    logger.info("  recovered correctly          : %d  (%.1f%% of all, %.1f%% of present)",
                recovered, 100 * recovered / n if n else 0,
                100 * recovered / n_present if n_present else 0)
    logger.info("    strict / similar / llm     : %d / %d / %d",
                tier.get("strict", 0), tier.get("similar", 0), tier.get("llm", 0))
    logger.info("  linked to WRONG F#           : %d", wrong)
    precision = recovered / (recovered + wrong) if (recovered + wrong) else 0
    wrong_rate = wrong / (recovered + wrong) if (recovered + wrong) else 0
    logger.info("  precision among linked       : %.1f%%", 100 * precision)
    logger.info("  wrong-link rate among linked : %.1f%%", 100 * wrong_rate)
    logger.info("  review-required links        : %d", review_required)
    logger.info("  gold present but unmatched   : %d", missing_absent)
    logger.info("  gold MO id not in corpus     : %d", n - n_present)
    logger.info("Saved per-record → %s", out)
    logger.info("Saved wrong-link diagnostics → %s", wrong_out)
    logger.info("Saved unmatched diagnostics → %s", unmatched_out)
    logger.info("Saved unmatched reason counts → %s", unmatched_counts_out)
    summary = {
        "mode": mode,
        "slug": slug,
        "gold_links": n,
        "present_in_corpus": n_present,
        "linked": recovered + wrong,
        "correct": recovered,
        "wrong": wrong,
        "unmatched_present": missing_absent,
        "not_in_corpus": n - n_present,
        "recall_all": recovered / n if n else 0,
        "recall_present": recovered / n_present if n_present else 0,
        "precision_linked": precision,
        "wrong_link_rate": wrong_rate,
        "accuracy_on_gold": recovered / n if n else 0,
        "strict_correct": tier.get("strict", 0),
        "similar_correct": tier.get("similar", 0),
        "llm_correct": tier.get("llm", 0),
        "review_required_links": review_required,
        "synonym_matches": synonym_matches,
        "synonym_correct": synonym_correct,
        "synonym_wrong": synonym_wrong,
        "output": str(out),
        "wrong_links_output": str(wrong_out),
        "unmatched_output": str(unmatched_out),
    }
    return summary


def main():
    ap = argparse.ArgumentParser(description="Validate resolve.py against DAP ground truth")
    ap.add_argument("--no-llm", action="store_true", help="rule-based baseline only")
    ap.add_argument("--force-llm", action="store_true",
                    help="LLM-only pass over bounded candidates for a clean rules-vs-LLM comparison")
    ap.add_argument("--per-genus", type=int, default=150,
                    help="cap of non-gold BBM decoys per genus (recall is exact regardless)")
    ap.add_argument("--bbm", default=str(DATA_DIR / "bbm_records.csv"),
                    help="BBM CSV (point at a pre-filtered subset on low-memory hosts)")
    ap.add_argument("--only-mo-ids", default="",
                    help="comma-separated MO ids for a small validation/review subset")
    ap.add_argument("--label", default=None,
                    help="override output slug, e.g. rules+llm_review")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    only_mo_ids = [x.strip() for x in args.only_mo_ids.split(",") if x.strip()]
    summary = validate(use_llm=not args.no_llm, per_genus=args.per_genus, bbm_path=args.bbm,
                       force_llm=args.force_llm, only_mo_ids=only_mo_ids, label=args.label)
    csv_summary = dict(summary)
    for col in ("recall_all", "recall_present", "precision_linked",
                "wrong_link_rate", "accuracy_on_gold"):
        csv_summary[col] = round(100 * csv_summary[col], 1)
    summary_path = REPORTS_DIR / "dap_validation_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_summary.keys()), lineterminator="\n")
        w.writeheader()
        w.writerow(csv_summary)
    logger.info("Saved validation summary -> %s", summary_path)


if __name__ == "__main__":
    main()
