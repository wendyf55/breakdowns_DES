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
LLM_NAME_SIM = 0.65
DEFAULT_LLM_MAX_GROUP = 6
DEFAULT_LLM_MIN_SCORE = 4
DEFAULT_LLM_ACCEPT_SCORE = 10
SYNONYM_PATH = DATA_DIR / "name_synonyms.csv"
MATCH_DETAILS = {}
_SYNONYM_GROUPS = None


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

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _mk(y, mo, d):
    return f"{y}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else ""


def norm_date(s):
    """Normalize a date to YYYY-MM-DD (or bare YYYY, or "").

    Handles ISO / datetime (year-first), verbatim forms Specify stores in
    startDateVerbatim ("17 Nov 2001", "Nov 17, 2001", "11/17/2001"), and
    year-only. Numeric year-last is read as M/D/Y unless the first number is
    >12 (then D/M/Y). Both a BBM verbatim date and MO's ISO date collapse to
    the same string so the strict tier can compare them."""
    s = str(s or "").strip()
    if not s:
        return ""
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)      # YYYY-M-D
    if m:
        r = _mk(m.group(1), int(m.group(2)), int(m.group(3)))
        if r:
            return r
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", s)  # 17 Nov 2001
    if m and m.group(2)[:3].lower() in _MONTHS:
        r = _mk(m.group(3), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        if r:
            return r
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", s)  # Nov 17, 2001
    if m and m.group(1)[:3].lower() in _MONTHS:
        r = _mk(m.group(3), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        if r:
            return r
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", s)   # M/D/YYYY or D/M/YYYY
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        mo, d = (a, b) if a <= 12 else (b, a)
        r = _mk(m.group(3), mo, d)
        if r:
            return r
    m = re.search(r"\b(\d{4})\b", s)                              # year only
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

def _load_synonym_groups(path=SYNONYM_PATH):
    """Map normalized names to accepted-name groups from data/name_synonyms.csv."""
    groups = {}
    if not path.exists():
        return groups
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") not in {"Accepted", "Synonym"}:
                continue
            accepted = norm_name(row.get("accepted_name"))
            if not accepted:
                continue
            group = f"{row.get('source', '')}:{accepted}"
            for field in ("query_name", "name", "accepted_name"):
                name = norm_name(row.get(field))
                if name:
                    groups.setdefault(name, set()).add(group)
    return groups


def synonym_groups(name):
    global _SYNONYM_GROUPS
    if _SYNONYM_GROUPS is None:
        _SYNONYM_GROUPS = _load_synonym_groups()
    return _SYNONYM_GROUPS.get(norm_name(name), set())


def synonym_match(left, right):
    if not left or not right or norm_name(left) == norm_name(right):
        return False, ""
    shared = synonym_groups(left) & synonym_groups(right)
    if not shared:
        return False, ""
    return True, "; ".join(sorted(shared))


def _year(d):
    return d[:4] if d else ""

def _toks(s):
    return {t for t in re.findall(r"[a-z]{3,}", str(s or "").lower())}

def _overlap(a, b, n=1):
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return False
    return len(ta & tb) >= n or ta <= tb or tb <= ta


def _row_fields(row):
    return dict(zip(("id", *COMPARE_FIELDS), row))


def _detail_key(bbm_id, plat_id, how):
    return (bbm_id, plat_id, how)


def match_detail(bbm_id, plat_id, how):
    """Audit metadata for a pair returned by the most recent resolve() call."""
    return MATCH_DETAILS.get(_detail_key(bbm_id, plat_id, how), {})


def _record_details(row, prefix):
    fields = _row_fields(row)
    return {f"{prefix}_{k}": fields.get(k, "") for k in COMPARE_FIELDS}


def _group_detail(group, label):
    cands = group["candidates"]
    return {
        "candidate_group_size": len(cands),
        "candidate_group_ids": "; ".join(c[0] for c in cands),
        "llm_reason": group.get("reason", "") if label == LLM else "",
        "review_required": label == LLM,
        "accepted": label != LLM,
    }


def _store_detail(bbm_row, plat_row, group, label, meta, guardrail=""):
    detail = _group_detail(group, label)
    detail.update(_record_details(bbm_row, "bbm"))
    detail.update(_record_details(plat_row, "platform"))
    syn_match, syn_evidence = synonym_match(bbm_row[2], plat_row[2])
    detail["synonym_match"] = syn_match
    detail["synonym_evidence"] = syn_evidence
    if label == LLM:
        accepted, reason = _llm_acceptance(bbm_row, plat_row, meta)
        detail["accepted"] = accepted
        detail["review_required"] = not accepted
        if reason:
            guardrail = f"{guardrail};{reason}" if guardrail else reason
    detail["guardrail"] = guardrail
    MATCH_DETAILS[_detail_key(bbm_row[0], plat_row[0], label)] = detail


def _compatible_dates(bbm_row, plat_row):
    bd, pd = bbm_row[6], plat_row[6]
    if len(bd) == 10 and len(pd) == 10 and bd != pd:
        return False, "incompatible_exact_dates"
    if _year(bd) and _year(pd) and _year(bd) != _year(pd):
        return False, "incompatible_years"
    return True, ""


def _explicit_ref_signal(bbm_id, plat_id, meta):
    b, m = meta[bbm_id], meta[plat_id]
    bbm_cites_platform = m.get("native") in b.get("cited", set())
    plat_refs = {P.norm_catalog(r) for r in m.get("ubc_ref", set())}
    our_refs = {P.norm_catalog(x) for x in (b.get("catalog"), b.get("altcatalog")) if x}
    platform_cites_bbm = bool(plat_refs & our_refs)
    platform_conflicts = bool(plat_refs) and not platform_cites_bbm
    bbm_conflicts = bool(b.get("cited")) and not bbm_cites_platform
    return bbm_cites_platform, platform_cites_bbm, bbm_conflicts, platform_conflicts


def _pair_evidence_score(bbm_row, plat_row, meta):
    compatible, reason = _compatible_dates(bbm_row, plat_row)
    if not compatible:
        return -999, [reason]
    evidence, score = [], 0
    if bbm_row[3] and bbm_row[3] == plat_row[3]:
        evidence.append("same_genus"); score += 2
    sim = edit_sim(bbm_row[2], plat_row[2])
    if bbm_row[2] and bbm_row[2] == plat_row[2]:
        evidence.append("same_name"); score += 3
    elif sim >= LLM_NAME_SIM:
        evidence.append("similar_name"); score += 2
    syn_match, _syn_evidence = synonym_match(bbm_row[2], plat_row[2])
    if syn_match:
        evidence.append("synonym_match"); score += 2
    if len(bbm_row[6]) == 10 and bbm_row[6] == plat_row[6]:
        evidence.append("same_exact_date"); score += 3
    elif _year(bbm_row[6]) and _year(bbm_row[6]) == _year(plat_row[6]):
        evidence.append("same_year"); score += 1
    if _overlap(bbm_row[7], plat_row[7], 2):
        evidence.append("locality_overlap"); score += 2
    elif _overlap(bbm_row[7], plat_row[7], 1):
        evidence.append("weak_locality_overlap"); score += 1
    if _overlap(bbm_row[5], plat_row[5], 1):
        evidence.append("collector_overlap"); score += 1
    bbm_ref, plat_ref, bbm_conflict, plat_conflict = _explicit_ref_signal(bbm_row[0], plat_row[0], meta)
    if bbm_ref or plat_ref:
        evidence.append("explicit_cross_ref"); score += 4
    if bbm_conflict:
        evidence.append("bbm_cites_different_platform_record")
    if plat_conflict:
        evidence.append("platform_cites_different_catalog")
    return score, evidence


def _llm_pair_guardrail(bbm_row, plat_row, meta):
    compatible, reason = _compatible_dates(bbm_row, plat_row)
    if not compatible:
        return False, reason
    _bbm_ref, _plat_ref, bbm_conflict, plat_conflict = _explicit_ref_signal(
        bbm_row[0], plat_row[0], meta
    )
    if bbm_conflict or plat_conflict:
        return False, "conflicting_explicit_refs"
    score, evidence = _pair_evidence_score(bbm_row, plat_row, meta)
    has_date = "same_exact_date" in evidence or "same_year" in evidence
    has_place_or_collector = any(e in evidence for e in (
        "locality_overlap", "weak_locality_overlap", "collector_overlap"
    ))
    has_name = any(e in evidence for e in ("same_name", "similar_name", "synonym_match"))
    if "explicit_cross_ref" in evidence:
        return True, f"passed_guardrails:{','.join(evidence)}"
    if score >= _llm_min_score() and has_date and has_place_or_collector and has_name:
        return True, f"passed_guardrails:{','.join(evidence)}"
    return False, "missing_minimum_evidence"


def _llm_acceptance(bbm_row, plat_row, meta):
    """Allow an LLM pair into accepted output only with strong rule-like evidence."""
    score, evidence = _pair_evidence_score(bbm_row, plat_row, meta)
    has_exact_date = "same_exact_date" in evidence
    has_name = "same_name" in evidence or "synonym_match" in evidence
    has_context = any(e in evidence for e in (
        "locality_overlap", "collector_overlap", "explicit_cross_ref"
    ))
    explicit_link = "explicit_cross_ref" in evidence and has_name
    strong_attributes = score >= _llm_accept_score() and has_exact_date and has_name and has_context
    if explicit_link or strong_attributes:
        return True, f"accepted_strict_evidence:{','.join(evidence)}"
    return False, "review_required"


# ── predicates ──────────────────────────────────────────────────────────────

def strict_pred(c, o, context):
    return bool(
        c.sci_name and c.sci_name == o.sci_name
        and c.date and c.date == o.date and len(c.date) == 10
        and (_overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1))
    )

def similar_pred(c, o, context):
    if _year(c.date) and _year(o.date) and _year(c.date) != _year(o.date):
        return False
    has_date = bool(
        (len(c.date) == 10 and c.date == o.date)
        or (_year(c.date) and _year(c.date) == _year(o.date))
    )
    if not has_date:
        return False
    syn_match, _syn_evidence = synonym_match(c.sci_name, o.sci_name)
    same_genus = bool(c.genus and c.genus == o.genus)
    name_ok = edit_sim(c.sci_name, o.sci_name) >= NAME_SIM or syn_match
    context_ok = _overlap(c.locality, o.locality, 2) or _overlap(c.collector, o.collector, 1)
    return (same_genus or syn_match) and name_ok and context_ok

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
            b.setdefault(f"genus:{genus}", []).append(row)
        for group in synonym_groups(row[2]):
            b.setdefault(f"synonym:{group}", []).append(row)
    return b


def _dedupe_pairs(pairs):
    rank = {STRICT: 0, SIMILAR: 1, LLM: 2}
    best = {}
    for bbm_id, plat_id, how in pairs:
        key = (bbm_id, plat_id)
        if key not in best or rank.get(how, 99) < rank.get(best[key], 99):
            best[key] = how
    return [(bbm_id, plat_id, how) for (bbm_id, plat_id), how in best.items()]


def _dedupe_dups(dups):
    rank = {STRICT: 0, SIMILAR: 1, LLM: 2}
    best = {}
    for a, b, platform, how in dups:
        left, right = sorted((a, b))
        key = (left, right, platform)
        if key not in best or rank.get(how, 99) < rank.get(best[key], 99):
            best[key] = how
    return [(a, b, platform, how) for (a, b, platform), how in best.items()]

def _cross_platform_pairs(group, meta, label):
    bbm = [c for c in group["candidates"] if meta[c[0]]["platform"] == "BBM"]
    plat = [c for c in group["candidates"] if meta[c[0]]["platform"] != "BBM"]
    out = []
    for b in bbm:
        for m in plat:
            if label == LLM:
                ok, guardrail = _llm_pair_guardrail(b, m, meta)
                if not ok:
                    logger.info("Rejected LLM pair %s <-> %s: %s", b[0], m[0], guardrail)
                    continue
            else:
                guardrail = ""
            _store_detail(b, m, group, label, meta, guardrail=guardrail)
            out.append((b[0], m[0], label))
    return out


def _same_platform_pairs(group, meta, label):
    """Records in one matched cluster that share a platform are duplicate
    records for a single specimen (category 06). Candidate-level: attribute
    matching alone is weak evidence, so these are flagged for review, not
    asserted."""
    ids = [c[0] for c in group["candidates"]]
    out = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if meta[a]["platform"] == meta[b]["platform"]:
                out.append((a, b, meta[a]["platform"], label))
    return out


def _llm_max_group():
    return int(os.getenv("LLM_MAX_GROUP_SIZE", DEFAULT_LLM_MAX_GROUP))


def _llm_min_score():
    return int(os.getenv("LLM_MIN_PREFILTER_SCORE", DEFAULT_LLM_MIN_SCORE))


def _llm_accept_score():
    return int(os.getenv("LLM_ACCEPT_SCORE", DEFAULT_LLM_ACCEPT_SCORE))


def _bounded_llm_groups(cands, meta):
    """Build small LLM candidate sets instead of sending a whole genus block."""
    max_group = max(2, _llm_max_group())
    min_score = _llm_min_score()
    bbm = [c for c in cands if meta[c[0]]["platform"] == "BBM"]
    plat = [c for c in cands if meta[c[0]]["platform"] != "BBM"]
    groups = []
    for b in bbm:
        scored = []
        for p in plat:
            score, evidence = _pair_evidence_score(b, p, meta)
            if score >= min_score:
                scored.append((score, len(evidence), p))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2][0]))
        if scored:
            groups.append([b] + [p for _score, _n, p in scored[:max_group - 1]])
    return groups

def resolve(bbm_rows, plat_rows, meta, use_llm=True, force_llm=False):
    """Return (cross_platform_pairs, same_platform_duplicate_pairs).

    A matched cluster with a BBM and a platform record is a cross-platform
    match (the quadrant scoring); two records in one cluster that share a
    platform are duplicate records for one specimen (category 06).

    force_llm=True routes bounded candidate sets through the LLM and ignores
    the rule-based matcher — an LLM-only pass for a rule-based-vs-LLM comparison
    on the same source rows (needs LLM_MODEL). use_llm alone keeps the rule-based
    matcher and sends only bounded leftovers to the LLM."""
    MATCH_DETAILS.clear()
    ecfg = _eval_config()
    blocks = _blocks(bbm_rows + plat_rows)
    if force_llm:
        if not os.getenv("LLM_MODEL"):
            raise RuntimeError("--force-llm needs LLM_MODEL set in .env "
                               "(start Ollama, then set LLM_MODEL)")
        pairs, dups = _llm_only(blocks, meta, ecfg)
        return _dedupe_pairs(pairs), _dedupe_dups(dups)
    ev = RuleBasedEvaluator()
    pairs, dups, leftover_blocks = [], [], []
    for genus, cands in blocks.items():
        if len(cands) < 2:
            continue
        groups = ev.evaluate(cands, {}, COLS, _TableCfg(), ecfg)
        leftovers = []
        for g in groups:
            if g["classification"] == NO_MATCH:
                leftovers.extend(g["candidates"])
            else:
                pairs.extend(_cross_platform_pairs(g, meta, g["classification"]))
                dups.extend(_same_platform_pairs(g, meta, g["classification"]))
        if use_llm and len(leftovers) >= 2:
            leftover_blocks.extend(_bounded_llm_groups(leftovers, meta))
    if use_llm and leftover_blocks and os.getenv("LLM_MODEL"):
        lpairs, ldups = _llm_pass(leftover_blocks, meta, ecfg)
        pairs.extend(lpairs)
        dups.extend(ldups)
    return _dedupe_pairs(pairs), _dedupe_dups(dups)

def _llm_pass(leftover_blocks, meta, ecfg):
    _prompts.DOMAIN_HINTS["specimen"] = harm.LLM_DOMAIN_HINT
    ev = LLMEvaluator()
    pairs, dups = [], []
    for cands in leftover_blocks:
        logger.info("LLM candidate group size: %d", len(cands))
        try:
            groups = ev.evaluate(cands, {}, COLS, _TableCfg(), ecfg)
        except Exception:
            logger.exception("LLM pass failed for a block; skipping")
            continue
        for g in groups:
            if g["classification"] == LLM_MATCH_KEY:
                pairs.extend(_cross_platform_pairs(g, meta, LLM))
                dups.extend(_same_platform_pairs(g, meta, LLM))
    return pairs, dups


def _llm_only(blocks, meta, ecfg):
    """LLM decides bounded multi-platform candidate groups; rule-based ignored."""
    _prompts.DOMAIN_HINTS["specimen"] = harm.LLM_DOMAIN_HINT
    ev = LLMEvaluator()
    pairs, dups = [], []
    for genus, cands in blocks.items():
        plats = {meta[c[0]]["platform"] for c in cands}
        if len(cands) < 2 or "BBM" not in plats or len(plats) < 2:
            continue
        for group in _bounded_llm_groups(cands, meta):
            logger.info("LLM-only candidate group size: %d", len(group))
            try:
                groups = ev.evaluate(group, {}, COLS, _TableCfg(), ecfg)
            except Exception:
                logger.exception("LLM block failed for genus %s; skipping", genus)
                continue
            for g in groups:
                if g["classification"] == LLM_MATCH_KEY:
                    pairs.extend(_cross_platform_pairs(g, meta, LLM))
                    dups.extend(_same_platform_pairs(g, meta, LLM))
    return pairs, dups


# ── quadrant scoring ────────────────────────────────────────────────────────

def quadrant(bbm_id, plat_id, meta):
    b, m = meta[bbm_id], meta[plat_id]
    bbm_cites = m["native"] in b["cited"]
    plat_refs = {P.norm_catalog(r) for r in m["ubc_ref"]}
    our_refs = {P.norm_catalog(x) for x in (b["catalog"], b["altcatalog"]) if x}
    plat_cites = bool(plat_refs & our_refs)
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
    parser.add_argument("--force-llm", action="store_true",
                        help="LLM-only: route bounded candidate sets through the LLM")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    platform = P.PLATFORMS[args.platform]
    records_path = args.records or str(DATA_DIR / f"{args.platform}_records.csv")

    bbm_rows, bbm_meta = load_bbm(args.bbm, platform)
    plat_rows, plat_meta = load_platform(records_path)
    meta = {**bbm_meta, **plat_meta}
    logger.info("Loaded %d BBM + %d %s records", len(bbm_rows), len(plat_rows), platform.label)

    pairs, dups = resolve(bbm_rows, plat_rows, meta,
                          use_llm=not args.no_llm, force_llm=args.force_llm)
    logger.info("Matched %d cross-platform pairs; %d same-platform duplicate pairs (cat 06)",
                len(pairs), len(dups))

    counts = {"bidirectional": 0, "unidirectional_ubc_to_platform": 0,
              "unidirectional_platform_to_ubc": 0, "absent": 0}
    rows, cat_lists = [], []
    for bbm_id, plat_id, how in pairs:
        q = quadrant(bbm_id, plat_id, meta)
        counts[q] += 1
        b, m = meta[bbm_id], meta[plat_id]
        name_mismatch = bool(b["sci_name"] and m["sci_name"] and b["sci_name"] != m["sci_name"])
        # category-02 "wrong id": the platform record cites a DIFFERENT UBC catalog
        plat_refs = {P.norm_catalog(r) for r in m["ubc_ref"]}
        our_refs = {P.norm_catalog(x) for x in (b["catalog"], b["altcatalog"]) if x}
        id_wrong = bool(plat_refs) and not (plat_refs & our_refs)
        cats = harm.classify_breakdowns(
            cross_ref=q, exists=True, coupling=platform.coupling,
            id_wrong=id_wrong,
            name_mismatch=name_mismatch, ambiguous=(how == LLM))
        score, just = harm.confidence(q, match_type=how)
        cat_lists.append(cats)
        detail = match_detail(bbm_id, plat_id, how)
        rows.append({
            "bbm": bbm_id,
            "platform_record": plat_id,
            "match_type": how,
            "quadrant": q,
            "confidence": score,
            "justification": just,
            "breakdown": ",".join(cats),
            "review_required": detail.get("review_required", how == LLM),
            "accepted": detail.get("accepted", how != LLM),
            "llm_reason": detail.get("llm_reason", ""),
            "guardrail": detail.get("guardrail", ""),
            "synonym_match": detail.get("synonym_match", False),
            "synonym_evidence": detail.get("synonym_evidence", ""),
            "candidate_group_size": detail.get("candidate_group_size", ""),
            "candidate_group_ids": detail.get("candidate_group_ids", ""),
            "bbm_sci_name": detail.get("bbm_sci_name", ""),
            "bbm_genus": detail.get("bbm_genus", ""),
            "bbm_species": detail.get("bbm_species", ""),
            "bbm_collector": detail.get("bbm_collector", ""),
            "bbm_date": detail.get("bbm_date", ""),
            "bbm_locality": detail.get("bbm_locality", ""),
            "platform_sci_name": detail.get("platform_sci_name", ""),
            "platform_genus": detail.get("platform_genus", ""),
            "platform_species": detail.get("platform_species", ""),
            "platform_collector": detail.get("platform_collector", ""),
            "platform_date": detail.get("platform_date", ""),
            "platform_locality": detail.get("platform_locality", ""),
        })

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{args.platform}_resolution.csv"
    if rows:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader(); w.writerows(rows)

    if dups:
        dup_rows = [{"platform": pl, "record_a": a, "record_b": b,
                     "match_type": how, "breakdown": "06"} for a, b, pl, how in dups]
        dpath = REPORTS_DIR / f"{args.platform}_duplicates.csv"
        with open(dpath, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(dup_rows[0].keys()), lineterminator="\n")
            w.writeheader(); w.writerows(dup_rows)
        cat_lists.extend([["06"]] * len(dups))
        logger.info("Saved %d duplicate pairs → %s", len(dups), dpath)

    logger.info("─" * 50)
    for k, v in counts.items():
        logger.info("  %-32s %d", k, v)
    for code, n in harm.summarize(cat_lists).items():
        logger.info("  breakdown %s (%s): %d", code, harm.CATEGORIES[code], n)
    logger.info("Saved matched pairs → %s", out)


if __name__ == "__main__":
    main()
