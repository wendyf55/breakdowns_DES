"""Harmonization framework — the conceptual layer.

Encodes the 7 breakdown categories, the 0–5 confidence rubric, and the report
shape from the portable specimen-harmonization spec (Kholmatova 2026; the
`specimen-harmonization-universal/` skill). link_audit and resolve import from
here so their machine outputs carry the same labels a human/LLM audit would.
"""

# ── the seven breakdown categories ──────────────────────────────────────────
CATEGORIES = {
    "01": "missing identifier cross-references",
    "02": "identifier integrity (wrong / hanging / wrong-field)",
    "03": "absence from repositories",
    "04": "poor harmonization confidence",
    "05": "nomenclature breakdown",
    "06": "duplicate records",
    "07": "harmonization decay",
}


def classify_breakdowns(*, cross_ref, exists, coupling,
                        id_wrong=False, id_wrong_field=False, id_hanging=False,
                        name_mismatch=False, ambiguous=False):
    """Map our audit/resolve signals to breakdown category codes (a list).

    A record can trigger several at once — we classify every problem, not just
    the first (per the spec).

    Category 02 (identifier integrity) is driven by the three sub-cases the paper
    names (§5.1.3), tested per record — NOT by platform coupling:
      id_wrong        the cross-reference resolves to a *different* specimen (the
                      platform record cites another catalog number) — a mis-assigned
                      or typo'd id.
      id_wrong_field  the id sits in a free-text column, not a structured
                      cross-reference field, so it does not propagate downstream.
      id_hanging      the id carries no recognizable prefix/attribution, so it is
                      not readable as a cross-reference without insider knowledge.
                      (A prefixed id in a platform's recognized reference column —
                      see Platform.reference_fields — is not hanging.)
    """
    cats = []
    if not exists:
        # a correspondence we expected didn't resolve
        if coupling == "harvested":
            cats.append("03")          # never published downstream (harvest gap)
        else:
            cats += ["02", "07"]       # dead / decayed id (points to nothing)
        return cats
    if cross_ref in ("unidirectional", "unidirectional_ubc_to_platform",
                     "unidirectional_platform_to_ubc", "absent"):
        cats.append("01")              # a link that should exist doesn't
    if id_wrong or id_wrong_field or id_hanging:
        cats.append("02")              # identifier integrity (wrong / wrong-field / hanging)
    if name_mismatch:
        cats.append("05")
    if ambiguous:
        cats.append("04")
    return cats


# ── 0–5 confidence rubric ───────────────────────────────────────────────────
# No image evidence is available to this pipeline, so a match can only reach 5
# via an explicit cross-referenced identifier; attribute-only matches cap ~3.

def confidence(cross_ref, match_type=None, *, dangling=False):
    """Return (score, justification). score is None when there's no confirmed match."""
    if dangling:
        return (None, "referenced record does not resolve — no confirmed match")
    if cross_ref == "bidirectional":
        return (5.0, "explicit, attributed identifier links both records")
    if cross_ref and cross_ref.startswith("unidirectional"):
        return (4.0, "one-directional cross-reference (id present on only one side)")
    # absent / matched by attributes only, no image
    if match_type == "strict":
        return (3.0, "name + exact date + locality/collector match; no cross-reference or image")
    if match_type == "similar":
        return (2.5, "similar name + date/locality; no cross-reference or image")
    return (2.0, "attribute match adjudicated by LLM; ambiguous — defer to a curator")


# ── Step-6 report ───────────────────────────────────────────────────────────

def report(*, specimen, direction, platforms_checked, records, cross_ref,
           score, justification, categories, action):
    """One specimen's harmonization report, in the spec's Step-6 shape."""
    return {
        "specimen": specimen,
        "direction": direction,
        "platforms_checked": ", ".join(platforms_checked),
        "records_found": records,
        "cross_reference_status": cross_ref,
        "confidence": "N/A" if score is None else f"{score} — {justification}",
        "breakdown_categories": "; ".join(f"{c} {CATEGORIES[c]}" for c in categories) or "none",
        "recommended_action": action,
    }


def summarize(category_lists):
    """Batch summary: count of specimens per breakdown category."""
    counts = {c: 0 for c in CATEGORIES}
    for cats in category_lists:
        for c in set(cats):
            counts[c] += 1
    return {c: n for c, n in counts.items() if n}


# ── LLM tier prompt (drives resolve's ambiguous-middle adjudication) ────────
LLM_DOMAIN_HINT = (
    "You are harmonizing specimen records across biodiversity platforms (a museum "
    "collection database and an independent platform). Decide which records refer to "
    "the SAME physical specimen. This is a records/metadata task, NOT taxonomic "
    "identification — never resolve which name is 'correct'. Rank evidence: an "
    "identifier already cross-referenced between two records is strongest; name + "
    "collection date + locality together is only WEAK evidence, because multiple "
    "distinct specimens can share all three (especially from one collecting event); a "
    "taxonomic name alone is never sufficient — names drift over time and are not "
    "unique. Do NOT force a choice between equally plausible candidates: if the "
    "evidence is ambiguous, leave them unmatched (defer to a human) rather than "
    "guessing — a wrong link is worse than a missed one."
)
