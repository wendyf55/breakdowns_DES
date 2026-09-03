"""Cross-platform specimen resolution (README Goal 2 / §2).

Uses the vendored evaluator subsystem (scripts/evaluators/, copied from the
orchestration framework) to decide whether a BBM record and an MO observation
are the same physical specimen — the
search → classify pattern, minus the (Specify-only) merge. Records from both
platforms are shaped into the evaluators' RecordObject contract and clustered:

  RuleBasedEvaluator  — confident matches (name + date + locality/collector)
  LLMEvaluator        — adjudicates the ambiguous middle (optional; needs LLM_*)

A cluster with ≥1 BBM and ≥1 MO record is a cross-platform match. Matched pairs
are crossed with the recorded cross-references (BBM's "MO #", MO's "UBC F#") to
score the four harmonization quadrants:
  bidirectional / unidirectional UBC→MO / unidirectional MO→UBC / absent.

    python resolve.py                 # rule-based (+ LLM if LLM_MODEL set)
    python resolve.py --no-llm
"""

import argparse
import csv
import logging
import os
import re
import sys
from dataclasses import dataclass

from config import DATA_DIR, REPORTS_DIR

logger = logging.getLogger(__name__)

# ── vendored evaluator subsystem (scripts/evaluators/, copied from orchestration) ──
from evaluators import (
    RuleBasedEvaluator, LLMEvaluator, LLM_MATCH_KEY,
    MatchRule, EvaluationConfig, prompts as _prompts,
)

# ── record shape (the common cross-platform comparable fields) ──────────────
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
    return " ".join(toks[:2])                      # genus species only

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


# ── predicates (operate on RecordObject attrs = COMPARE_FIELDS) ──────────────

def _toks(s):
    """Meaningful (>=3 char) lowercase word tokens of a string."""
    return {t for t in re.findall(r"[a-z]{3,}", str(s or "").lower())}

def _overlap(a, b, n=1):
    """True if a and b share >= n tokens, or one's tokens subset the other's.

    Cross-platform localities/collectors are formatted differently
    ("Observatory Hill" vs "Observatory Hill, Victoria, BC"; "Oluna Ceska" vs
    "Oluna & Adolf Ceska"), so exact equality never fires — token overlap does.
    """
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    return len(ta & tb) >= n or ta <= tb or tb <= ta

def strict_pred(c, o, context):
    """Same name + same exact date + locality/collector token overlap."""
    return bool(
        c.sci_name and c.sci_name == o.sci_name
        and c.date and c.date == o.date and len(c.date) == 10
        and (_overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1))
    )

def similar_pred(c, o, context):
    """Same genus + same year + close name + locality/collector overlap."""
    if not (c.genus and c.genus == o.genus):
        return False
    if _year(c.date) and _year(o.date) and _year(c.date) != _year(o.date):
        return False
    name_ok = edit_sim(c.sci_name, o.sci_name) >= NAME_SIM
    return name_ok and (_overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1))


def _eval_config():
    return EvaluationConfig(
        match_rules=(MatchRule(STRICT, strict_pred, reviewable=False),
                     MatchRule(SIMILAR, similar_pred, reviewable=True)),
        unmatched_key=NO_MATCH,
    )


# ── record loading ──────────────────────────────────────────────────────────

def _row(rid, platform, sci_name, collector, date, locality):
    g, sp = genus_species(sci_name)
    return [rid, platform, norm_name(sci_name), g, sp,
            norm_text(collector), norm_date(date), norm_text(locality)]

MO_CITE_RE = re.compile(r"(?i)\bMO\s*#\s*0*(\d+)")

def load_bbm(path):
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
                "cited_mo": set(MO_CITE_RE.findall(blob)),
            }
    return rows, meta

def load_mo(path):
    rows, meta = [], {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = "MO:" + str(r.get("mo_id"))
            rows.append(_row(rid, "MO", r.get("consensus_name"), r.get("owner"),
                             r.get("date"), r.get("location_name")))
            meta[rid] = {
                "platform": "MO",
                "mo_id": str(r.get("mo_id")),
                "cites_ubc": str(r.get("cites_ubc")).strip().lower() in ("true", "1"),
                "ubc_catalog": (r.get("ubc_catalog_cited") or "").strip(),
            }
    return rows, meta


# ── resolution ──────────────────────────────────────────────────────────────

def _blocks(rows):
    """Group records by genus so the evaluator only compares plausible pairs."""
    b = {}
    for row in rows:
        genus = row[3]
        if genus:
            b.setdefault(genus, []).append(row)
    return b

def _cross_platform_pairs(group, meta, label):
    ids = [c[0] for c in group["candidates"]]
    bbm = [i for i in ids if meta[i]["platform"] == "BBM"]
    mo = [i for i in ids if meta[i]["platform"] == "MO"]
    return [(b, m, label) for b in bbm for m in mo]

def resolve(bbm_rows, mo_rows, meta, use_llm=True):
    ev = RuleBasedEvaluator()
    ecfg = _eval_config()
    pairs, leftover_blocks = [], []

    for genus, cands in _blocks(bbm_rows + mo_rows).items():
        if len(cands) < 2:
            continue
        groups = ev.evaluate(cands, {}, COLS, _TableCfg(), ecfg)
        leftovers = []
        for g in groups:
            if g["classification"] == NO_MATCH:
                leftovers.extend(g["candidates"])
            else:
                pairs.extend(_cross_platform_pairs(g, meta, g["classification"]))
        # keep unmatched records that could still cross-match for the LLM pass
        plats = {meta[c[0]]["platform"] for c in leftovers}
        if use_llm and "BBM" in plats and "MO" in plats and len(leftovers) >= 2:
            leftover_blocks.append(leftovers)

    if use_llm and leftover_blocks and _LLM_OK and os.getenv("LLM_MODEL"):
        pairs.extend(_llm_pass(leftover_blocks, meta, ecfg))
    return pairs

def _llm_pass(leftover_blocks, meta, ecfg):
    _prompts.DOMAIN_HINTS["specimen"] = (
        "These are specimen records from a museum database (BBM/UBC) and an "
        "independent observation site (MO). Two records are the SAME specimen "
        "when they share scientific name (allow synonyms and minor spelling), "
        "collection date, and locality or collector — even across platforms."
    )
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

def quadrant(bbm_id, mo_id, meta):
    b, m = meta[bbm_id], meta[mo_id]
    bbm_cites_mo = m["mo_id"] in b["cited_mo"]
    mo_cites_bbm = m["cites_ubc"] and m["ubc_catalog"] in (b["catalog"], b["altcatalog"])
    if bbm_cites_mo and mo_cites_bbm:
        return "bidirectional"
    if bbm_cites_mo:
        return "unidirectional_UBC_to_MO"
    if mo_cites_bbm:
        return "unidirectional_MO_to_UBC"
    return "absent"


def main():
    parser = argparse.ArgumentParser(description="Cross-platform BBM↔MO specimen resolution")
    parser.add_argument("--bbm", default=str(DATA_DIR / "bbm_records.csv"))
    parser.add_argument("--mo", default=str(DATA_DIR / "mo_records.csv"))
    parser.add_argument("--no-llm", action="store_true", help="skip the LLM adjudication pass")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    bbm_rows, bbm_meta = load_bbm(args.bbm)
    mo_rows, mo_meta = load_mo(args.mo)
    meta = {**bbm_meta, **mo_meta}
    logger.info("Loaded %d BBM + %d MO records", len(bbm_rows), len(mo_rows))

    pairs = resolve(bbm_rows, mo_rows, meta, use_llm=not args.no_llm)
    logger.info("Matched %d cross-platform pairs", len(pairs))

    counts = {"bidirectional": 0, "unidirectional_UBC_to_MO": 0,
              "unidirectional_MO_to_UBC": 0, "absent": 0}
    rows = []
    for bbm_id, mo_id, how in pairs:
        q = quadrant(bbm_id, mo_id, meta)
        counts[q] += 1
        rows.append({"bbm": bbm_id, "mo": mo_id, "match_type": how, "quadrant": q,
                     "mo_url": f"https://mushroomobserver.org/{meta[mo_id]['mo_id']}"})

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / "specimen_resolution.csv"
    if rows:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    logger.info("─" * 50)
    for k, v in counts.items():
        logger.info("  %-28s %d", k, v)
    logger.info("Saved matched pairs → %s", out)


if __name__ == "__main__":
    main()
