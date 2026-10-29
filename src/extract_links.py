"""Question 1: which UBC (GBIF-published) records point to a record on another platform?

Reads the verbatim table of a GBIF Darwin Core Archive (zip) and finds every
reference to an external platform in any field. Writes one row per link and a
record-level summary using the link tiers in README.md.

Tiers here are provisional: whether a link is broken or incorrect is only known
after fetching the target (question 2).

    python3 src/extract_links.py data/raw/gbif/<download>.zip
"""
import collections
import csv
import io
import re
import sys
import zipfile
from pathlib import Path

OUT_LINKS = Path("outputs/q1_links.csv")
OUT_SUMMARY = Path("outputs/q1_summary.csv")
OUT_DOMAINS = Path("outputs/q1_other_domains.csv")

# Darwin Core fields meant for cross-references. A link anywhere else is "wrong field".
PROPER_FIELDS = {
    "associatedReferences", "associatedSequences", "associatedOccurrences",
    "otherCatalogNumbers", "relatedResourceID", "references", "associatedMedia",
}
# The record's own identifiers / boilerplate: never a link to another platform.
SKIP_FIELDS = {
    "gbifID", "occurrenceID", "catalogNumber", "institutionCode", "collectionCode",
    "datasetID", "datasetName", "institutionID", "collectionID", "license",
    "rightsHolder", "accessRights", "bibliographicCitation", "ownerInstitutionCode",
}
IGNORE_DOMAINS = {"creativecommons.org", "gbif.org", "www.gbif.org", "api.gbif.org"}

# Per platform: url = attributed by URL, prefixed = attributed by name + id,
# bare = recognizable id with no attribution (hanging), mention = name only, no id.
ACC = r"[A-Z]{1,2}\d{5,6}(?:\.\d+)?|[A-Z]{2}_?\d{6,9}(?:\.\d+)?|[A-Z]{4}\d{8,10}(?:\.\d+)?"
PLATFORMS = {
    "mushroom_observer": dict(
        url=r"mushroomobserver\.org/(?:observations?/|obs/|observer/show_observation/)?(\d+)",
        # "MO" alone must be uppercase and followed by #/no./obs/: so "Kansas City, MO 64110" is not a link
        prefixed=r"(?:mushroom\s*observer\s*(?:obs(?:ervation)?\.?\s*)?(?:#|no\.?|number)?|(?-i:\bMO\b)\.?\s*(?:#|no\.?|obs(?:ervation)?\.?\s*#?|:))\s*:?\s*(\d{2,7})\b",
        mention=r"mushroom\s*observer|mushroomobserver",
    ),
    "inaturalist": dict(
        url=r"inaturalist\.(?:org|ca)/observations/(\d+)",
        prefixed=r"\binat(?:uralist)?\s*(?:obs(?:ervation)?\.?\s*)?(?:#|no\.?|id)?\s*:?\s*(\d{4,})\b",
        mention=r"inaturalist|\binat\b",
    ),
    "genbank": dict(
        url=r"ncbi\.nlm\.nih\.gov/(?:nuccore|nucleotide)/(" + ACC + r")",
        prefixed=r"(?:genbank|gb|accession)\s*(?:no\.?|#|acc\.?)?\s*:?\s*(" + ACC + r")\b",
        bare=r"\b(" + ACC + r")\b",  # only used in associatedSequences (see bare_ok)
        mention=r"genbank",
    ),
    "bold": dict(
        url=r"boldsystems\.org\S*?([A-Z]{3,6}\d{3,6}-\d{2})",
        prefixed=r"\bBOLD\s*(?:id|process\s*id)?\s*:?\s*([A-Z]{3,6}\d{3,6}-\d{2})\b",
        bare=r"\b([A-Z]{4,6}\d{3,6}-\d{2})\b",
        mention=r"\bBOLD\s*systems\b|boldsystems",
    ),
    "mycoportal": dict(  # harvests UBC records; kept for reference, not a link partner
        url=r"mycoportal\.org/portal/collections/individual/index\.php\?occid=(\d+)",
        mention=r"mycoportal",
    ),
}
PATTERNS = {p: {k: re.compile(v, re.I if k != "bare" else 0) for k, v in d.items()}
            for p, d in PLATFORMS.items()}
URL_RX = re.compile(r"https?://([^/\s\"'<>;,|]+)", re.I)

TIER_ORDER = ["1_explicit", "2_hanging_or_wrong_field", "3_mention_only"]


def bare_ok(platform, field):
    """Bare ids are only trusted where the field itself says what they are."""
    return field == "associatedSequences" and platform in ("genbank", "bold")


def find_links(field, value):
    """Yield (platform, target_id, attribution) for one field value."""
    found = set()
    for platform, rx in PATTERNS.items():
        ids = {}
        for kind in ("url", "prefixed"):
            if kind in rx:
                for m in rx[kind].finditer(value):
                    ids.setdefault(m.group(1).upper(), kind)
        if "bare" in rx and (bare_ok(platform, field) or "genbank" in value.lower() or "bold" in value.lower()):
            for m in rx["bare"].finditer(value):
                ids.setdefault(m.group(1).upper(), "bare")
        for tid, kind in ids.items():
            found.add((platform, tid, kind))
        if not ids and rx["mention"].search(value):
            found.add((platform, "", "mention"))
    return found


def tier(field, attribution):
    if attribution == "mention":
        return "3_mention_only"
    if field in PROPER_FIELDS and (attribution in ("url", "prefixed") or field == "associatedSequences"):
        return "1_explicit"
    return "2_hanging_or_wrong_field"


def open_verbatim(path):
    z = zipfile.ZipFile(path)
    name = next(n for n in ("verbatim.txt", "occurrence.txt") if n in z.namelist())
    print(f"reading {name} from {path}", file=sys.stderr)
    return io.TextIOWrapper(z.open(name), encoding="utf-8", newline="")


def main(zip_path):
    csv.field_size_limit(sys.maxsize)
    reader = csv.DictReader(open_verbatim(zip_path), delimiter="\t", quoting=csv.QUOTE_NONE)
    scan = [f for f in reader.fieldnames if f not in SKIP_FIELDS]

    OUT_LINKS.parent.mkdir(exist_ok=True)
    links_out = csv.writer(open(OUT_LINKS, "w", newline=""))
    links_out.writerow(["gbifID", "catalogNumber", "collectionCode", "platform", "target_id",
                        "field", "attribution", "tier", "raw_value"])

    n_records = collections.Counter()           # collection -> records
    best = {}                                   # (gbifID, platform) -> best tier
    coll_of = {}
    other_domains = collections.Counter()

    for i, r in enumerate(reader, 1):
        gid, coll = r.get("gbifID", ""), r.get("collectionCode", "") or "(none)"
        n_records[coll] += 1
        for f in scan:
            v = r.get(f) or ""
            if not v:
                continue
            links = find_links(f, v)
            for platform, tid, attribution in links:
                t = tier(f, attribution)
                links_out.writerow([gid, r.get("catalogNumber", ""), coll, platform, tid,
                                    f, attribution, t, v[:300]])
                k = (gid, platform)
                if k not in best or TIER_ORDER.index(t) < TIER_ORDER.index(best[k]):
                    best[k] = t
                    coll_of[gid] = coll
            known = {p for p, _, _ in links}
            for m in URL_RX.finditer(v):
                d = m.group(1).lower()
                if d not in IGNORE_DOMAINS and not any(p.split("_")[0] in d for p in known):
                    other_domains[(f, d)] += 1
        if i % 200000 == 0:
            print(f"  {i:,} records", file=sys.stderr)

    summary = collections.Counter((coll_of[g], p, t) for (g, p), t in best.items())
    with open(OUT_SUMMARY, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["collectionCode", "records_in_collection", "platform", "best_tier", "records"])
        for (coll, p, t), n in sorted(summary.items()):
            w.writerow([coll, n_records[coll], p, t, n])
    with open(OUT_DOMAINS, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "domain", "values"])
        for (f, d), n in other_domains.most_common():
            w.writerow([f, d, n])

    print(f"\n{sum(n_records.values()):,} records in {len(n_records)} collections")
    for coll, n in n_records.most_common():
        print(f"  {coll:20} {n:>9,}")
    print("\nrecords with >=1 link, by platform x best tier:")
    by_pt = collections.Counter((p, t) for (_, p), t in best.items())
    for (p, t), n in sorted(by_pt.items()):
        print(f"  {p:18} {t:26} {n:>7,}")
    print(f"\nunrecognized URL domains: {len(other_domains)} (see {OUT_DOMAINS})")


if __name__ == "__main__":
    main(sys.argv[1])
