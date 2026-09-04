"""Cross-platform specimen resolution (README Goal 2 / §2).

Uses the vendored evaluator subsystem (scripts/evaluators/, copied from the
orchestration framework) to decide whether a BBM record and an external record
are the same physical specimen — the search → classify pattern, minus the merge.
Records from both sides are shaped into the evaluators' RecordObject contract and
clustered (rule-based + optional LLM); a cluster with a BBM record and a platform
record is a match.

Platform-parameterized via `platforms.py`: BBM's cited ids come from
`platform.extract_refs`, the platform corpus is a generic `<platform>_records.csv`
(written by get_records.py), and matched pairs are scored into the four
harmonization quadrants:
  bidirectional / unidirectional UBC→platform / unidirectional platform→UBC / absent.

    python resolve.py                    # platform=mo
    python resolve.py --platform mo --no-llm
"""

import argparse
import csv
import logging
import os
import re
import sys
from dataclasses import dataclass

from config import DATA_DIR, REPORTS_DIR
import platforms as P
import harmonization as harm
from evaluators import (
    RuleBasedEvaluator, LLMEvaluator, LLM_MATCH_KEY,
    MatchRule, EvaluationConfig, prompts as _prompts,
)

logger = logging.getLogger(__name__)

# ── record shape ────────────────────────────────────────────────────────────
COMPARE_FIELDS = ["platform", "sci_name", "genus", "species", "collector", "date", "locality"]
COLS = {"base_cols": COMPARE_FIELDS, "related_specs": {}}
STRICT, SIMILAR, LLM, NO_MATCH = "strict", "similar", "llm", "no_match"
NAME_SIM = 0.85


@dataclass(frozen=True)
class _TableCfg:
    table: str = "specimen"
    match_fields: tuple = ("sci_name", "date", "locality")


# ── normalization ───────────────────────────────────────────────────────────

def norm_name(s):
    toks = re.sub(r"[^a-z ]", " ", str(s or "").lower()).split()
    return " ".join(toks[:2])

def genus_species(name):
    toks = norm_name(name).split()
    return (toks[0] if toks else "", toks[1] if len(toks) > 1 else "")

def norm_date(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(s or ""))
    if m and m.group(2) != "0" and m.group(3) != "0":
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})", str(s or ""))
    return m.group(1) if m else ""

def norm_text(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()

def edit_sim(a, b):
    a, b = str(a or "").lower(), str(b or "").lower()
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))

def _year(d):
    return d[:4] if d else ""

def _toks(s):
    return {t for t in re.findall(r"[a-z]{3,}", str(s or "").lower())}

def _overlap(a, b, n=1):
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    return len(ta & tb) >= n or ta <= tb or tb <= ta


# ── predicates ──────────────────────────────────────────────────────────────

def strict_pred(c, o, context):
    return bool(
        c.sci_name and c.sci_name == o.sci_name
        and c.date and c.date == o.date and len(c.date) == 10
        and (_overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1))
    )

def similar_pred(c, o, context):
    if not (c.genus and c.genus == o.genus):
        return False
    if _year(c.date) and _year(o.date) and _year(c.date) != _year(o.date):
        return False
    return (edit_sim(c.sci_name, o.sci_name) >= NAME_SIM
            and (_overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1)))

def _eval_config():
    return EvaluationConfig(
        match_rules=(MatchRule(STRICT, strict_pred, reviewable=False),
                     MatchRule(SIMILAR, similar_pred, reviewable=True)),
        unmatched_key=NO_MATCH,
    )


# ── record loading ──────────────────────────────────────────────────────────

def _row(rid, platform_label, sci_name, collector, date, locality):
    g, sp = genus_species(sci_name)
    return [rid, platform_label, norm_name(sci_name), g, sp,
            norm_text(collector), norm_date(date), norm_text(locality)]

def load_bbm(path, platform):
    """BBM rows + meta. Cited platform ids come from platform.extract_refs."""
    extract = getattr(platform, "extract_refs", lambda t: set())
    rows, meta = [], {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = "BBM:" + (r.get("catalognumber") or r.get("id") or "?")
            rows.append(_row(rid, "BBM", r.get("taxonname"), r.get("collectors"),
                             r.get("startdate"), r.get("localityname")))
            blob = " ".join(str(v) for v in r.values() if v)
            meta[rid] = {
                "platform": "BBM",
                "catalog": (r.get("catalognumber") or "").strip(),
                "altcatalog": (r.get("altcatalognumber") or "").strip(),
                "cited": extract(blob),
                "sci_name": norm_name(r.get("taxonname")),
            }
    return rows, meta

def load_platform(path):
    """Read a generic <platform>_records.csv (from get_records.py)."""
    rows, meta = [], {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = r.get("id") or "?"
            native = rid.split(":", 1)[1] if ":" in rid else rid
            rows.append(_row(rid, r.get("platform") or "platform", r.get("sci_name"),
                             r.get("collector"), r.get("date"), r.get("locality")))
            refs = {x for x in (r.get("ubc_ref") or "").split("; ") if x}
            meta[rid] = {"platform": r.get("platform") or "platform",
                         "native": native, "ubc_ref": refs,
                         "sci_name": norm_name(r.get("sci_name"))}
    return rows, meta


# ── resolution ──────────────────────────────────────────────────────────────

def _blocks(rows):
    b = {}
    for row in rows:
        genus = row[3]
        if genus:
            b.setdefault(genus, []).append(row)
    return b

def _cross_platform_pairs(group, meta, label):
    ids = [c[0] for c in group["candidates"]]
    bbm = [i for i in ids if meta[i]["platform"] == "BBM"]
    plat = [i for i in ids if meta[i]["platform"] != "BBM"]
    return [(b, m, label) for b in bbm for m in plat]

def resolve(bbm_rows, plat_rows, meta, use_llm=True):
    ev = RuleBasedEvaluator()
    ecfg = _eval_config()
    pairs, leftover_blocks = [], []
    for genus, cands in _blocks(bbm_rows + plat_rows).items():
        if len(cands) < 2:
            continue
        groups = ev.evaluate(cands, {}, COLS, _TableCfg(), ecfg)
        leftovers = []
        for g in groups:
            if g["classification"] == NO_MATCH:
                leftovers.extend(g["candidates"])
            else:
                pairs.extend(_cross_platform_pairs(g, meta, g["classification"]))
        plats = {meta[c[0]]["platform"] for c in leftovers}
        if use_llm and "BBM" in plats and len(plats) > 1 and len(leftovers) >= 2:
            leftover_blocks.append(leftovers)
    if use_llm and leftover_blocks and os.getenv("LLM_MODEL"):
        pairs.extend(_llm_pass(leftover_blocks, meta, ecfg))
    return pairs

def _llm_pass(leftover_blocks, meta, ecfg):
    _prompts.DOMAIN_HINTS["specimen"] = harm.LLM_DOMAIN_HINT
    ev = LLMEvaluator()
    out = []
    for cands in leftover_blocks:
        try:
            groups = ev.evaluate(cands, {}, COLS, _TableCfg(), ecfg)
        except Exception:
            logger.exception("LLM pass failed for a block; skipping")
            continue
        for g in groups:
            if g["classification"] == LLM_MATCH_KEY:
                out.extend(_cross_platform_pairs(g, meta, LLM))
    return out


# ── quadrant scoring ────────────────────────────────────────────────────────

def quadrant(bbm_id, plat_id, meta):
    b, m = meta[bbm_id], meta[plat_id]
    bbm_cites = m["native"] in b["cited"]
    plat_cites = bool(m["ubc_ref"] & {b["catalog"], b["altcatalog"]})
    if bbm_cites and plat_cites:
        return "bidirectional"
    if bbm_cites:
        return "unidirectional_ubc_to_platform"
    if plat_cites:
        return "unidirectional_platform_to_ubc"
    return "absent"


def main():
    parser = argparse.ArgumentParser(description="Cross-platform BBM↔platform specimen resolution")
    parser.add_argument("--platform", default="mo", choices=sorted(P.PLATFORMS))
    parser.add_argument("--bbm", default=str(DATA_DIR / "bbm_records.csv"))
    parser.add_argument("--records", default=None, help="platform CSV (default data/<platform>_records.csv)")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    platform = P.PLATFORMS[args.platform]
    records_path = args.records or str(DATA_DIR / f"{args.platform}_records.csv")

    bbm_rows, bbm_meta = load_bbm(args.bbm, platform)
    plat_rows, plat_meta = load_platform(records_path)
    meta = {**bbm_meta, **plat_meta}
    logger.info("Loaded %d BBM + %d %s records", len(bbm_rows), len(plat_rows), platform.label)

    pairs = resolve(bbm_rows, plat_rows, meta, use_llm=not args.no_llm)
    logger.info("Matched %d cross-platform pairs", len(pairs))

    counts = {"bidirectional": 0, "unidirectional_ubc_to_platform": 0,
              "unidirectional_platform_to_ubc": 0, "absent": 0}
    rows, cat_lists = [], []
    for bbm_id, plat_id, how in pairs:
        q = quadrant(bbm_id, plat_id, meta)
        counts[q] += 1
        b, m = meta[bbm_id], meta[plat_id]
        name_mismatch = bool(b["sci_name"] and m["sci_name"] and b["sci_name"] != m["sci_name"])
        cats = harm.classify_breakdowns(
            cross_ref=q, exists=True, coupling=platform.coupling,
            ref_in_free_text=(platform.coupling == "independent"),
            name_mismatch=name_mismatch, ambiguous=(how == LLM))
        score, just = harm.confidence(q, match_type=how)
        cat_lists.append(cats)
        rows.append({"bbm": bbm_id, "platform_record": plat_id, "match_type": how,
                     "quadrant": q, "confidence": score, "justification": just,
                     "breakdown": ",".join(cats)})

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{args.platform}_resolution.csv"
    if rows:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    logger.info("─" * 50)
    for k, v in counts.items():
        logger.info("  %-32s %d", k, v)
    for code, n in harm.summarize(cat_lists).items():
        logger.info("  breakdown %s (%s): %d", code, harm.CATEGORIES[code], n)
    logger.info("Saved matched pairs → %s", out)


if __name__ == "__main__":
    main()
