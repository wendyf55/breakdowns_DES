"""Fetch and cache accepted-name / synonym relationships for resolver work.

This is the first layer for category 05 name drift. It is intentionally separate
from `resolve.py`: fetch synonym data, inspect/validate it, then decide when to
use it as matching evidence. The default sources are fungal-relevant and mirror
the useful parts of the MDS synonym pipeline:

  Index Fungorum  nomenclatural backbone for fungi
  Mushroom Observer community taxonomy used by MO records
  GBIF             aggregator backbone used by downstream discovery/search

Output: data/name_synonyms.csv

    python scripts/name_synonyms.py
    python scripts/name_synonyms.py --subset dap-name-drift --all --dry-run
    python scripts/name_synonyms.py --subset dap-name-drift --all
    python scripts/name_synonyms.py --subset paper-name-drift --all
    python scripts/name_synonyms.py --names "Amanita muscaria,Boletus edulis"
    python scripts/name_synonyms.py --all --sources indexfungorum,mo,gbif

By default this is a small, resumable smoke run: 25 names, Index Fungorum + MO
only. Use `--all` and opt into GBIF when you deliberately want the slow pass.
Existing `(query_name, source)` pairs in the output are skipped unless
`--refresh` is set.
"""

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, REPORTS_DIR
from resolve import norm_name

OUTPUT = DATA_DIR / "name_synonyms.csv"
SOURCES = ("indexfungorum", "mo", "gbif")
DEFAULT_SOURCES = ("indexfungorum", "mo")
DEFAULT_LIMIT = 25
DEFAULT_CONTROL_LIMIT = 50
REQUEST_TIMEOUT = 15
HEADERS = {"User-Agent": "breakdowns-DES/0.1"}
DAP_NAME_DRIFT_REASONS = {"genus_mismatch_blocks_match", "name_below_rule_threshold"}
EXCLUDED_SPECIES_TOKENS = {
    "aff",
    "cf",
    "group",
    "nr",
    "sect",
    "section",
    "sp",
    "species",
    "spp",
}

IF_TAGS = {
    "name": "NAME_x0020_OF_x0020_FUNGUS",
    "record_id": "RECORD_x0020_NUMBER",
    "current_key": "CURRENT_x0020_NAME_x0020_RECORD_x0020_NUMBER",
    "rank": "INFRASPECIFIC_x0020_RANK",
}


def _urlopen(url, params=None, timeout=None):
    timeout = REQUEST_TIMEOUT if timeout is None else timeout
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _fetch_json(url, params=None, timeout=None):
    try:
        return json.loads(_urlopen(url, params=params, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        print(f"request failed ({exc}): {url}")
        return {}


def _fetch_xml(url, params=None, timeout=None):
    try:
        return ET.fromstring(_urlopen(url, params=params, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        print(f"request failed ({exc}): {url}")
        return ET.Element("empty")


def _split_name(name):
    parts = norm_name(name).split()
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def _row(query, source, status, name, accepted_name, source_id="", source_link=""):
    genus, species = _split_name(name)
    return {
        "query_name": norm_name(query),
        "source": source,
        "status": status,
        "name": norm_name(name),
        "accepted_name": norm_name(accepted_name),
        "genus": genus,
        "species": species,
        "source_id": str(source_id or ""),
        "source_link": source_link or "",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def gbif_synonyms(query):
    match = _fetch_json(
        "https://api.gbif.org/v1/species/match",
        {"name": query, "strict": "true"},
    )
    if not match or match.get("matchType") == "NONE":
        return []
    accepted_key = match.get("acceptedUsageKey") or match.get("usageKey")
    if not accepted_key:
        return []
    accepted = _fetch_json(f"https://api.gbif.org/v1/species/{accepted_key}")
    accepted_name = norm_name(accepted.get("canonicalName") or match.get("species") or query)
    rows = [_row(query, "gbif", "Accepted", accepted_name, accepted_name,
                 accepted_key, f"https://www.gbif.org/species/{accepted_key}")]
    syns = _fetch_json(
        f"https://api.gbif.org/v1/species/{accepted_key}/synonyms",
        {"limit": 500},
    ).get("results", [])
    seen = {accepted_name}
    for item in syns:
        if item.get("rank") != "SPECIES":
            continue
        name = norm_name(item.get("canonicalName") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        key = item.get("key", "")
        rows.append(_row(query, "gbif", "Synonym", name, accepted_name,
                         key, f"https://www.gbif.org/species/{key}" if key else ""))
    return rows


def mo_synonyms(query):
    data = _fetch_json(
        "https://mushroomobserver.org/api2/names",
        {
            "name": query,
            "include_synonyms": "true",
            "detail": "high",
            "format": "json",
        },
    )
    candidates = []
    for result in data.get("results", []) or []:
        candidates.append(result)
        candidates.extend(result.get("synonyms", []) or [])
    if not candidates:
        return []
    accepted = next((c for c in candidates if not c.get("deprecated")), None)
    accepted_name = norm_name((accepted or candidates[0]).get("name") or query)
    rows, seen = [], set()
    for item in candidates:
        name = norm_name(item.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        status = "Synonym" if item.get("deprecated") else "Accepted"
        mid = item.get("id", "")
        rows.append(_row(query, "mo", status, name, accepted_name, mid,
                         f"https://mushroomobserver.org/names/{mid}" if mid else ""))
    return rows


def indexfungorum_synonyms(query):
    root = _fetch_xml(
        "http://www.indexfungorum.org/ixfwebservice/fungus.asmx/NameSearch",
        {"SearchText": query, "AnywhereInText": "false", "MaxNumber": "50"},
    )
    record = None
    q = norm_name(query)
    for item in root.findall("IndexFungorum"):
        if norm_name(item.findtext(IF_TAGS["name"]) or "") == q:
            record = item
            break
    if record is None:
        return []
    current_key = (record.findtext(IF_TAGS["current_key"]) or "").strip()
    if not current_key:
        return []
    names = _fetch_xml(
        "http://www.indexfungorum.org/ixfwebservice/fungus.asmx/NamesByCurrentKey",
        {"CurrentKey": current_key},
    )
    accepted_name = ""
    raw_rows = []
    for item in names.findall("IndexFungorum"):
        name = norm_name(item.findtext(IF_TAGS["name"]) or "")
        if not name:
            continue
        rank = (item.findtext(IF_TAGS["rank"]) or "").strip()
        if rank and rank != "sp.":
            continue
        rid = (item.findtext(IF_TAGS["record_id"]) or "").strip()
        status = "Accepted" if rid == current_key else "Synonym"
        if status == "Accepted":
            accepted_name = name
        raw_rows.append((status, name, rid))
    accepted_name = accepted_name or q
    rows, seen = [], set()
    for status, name, rid in raw_rows:
        if name in seen:
            continue
        seen.add(name)
        rows.append(_row(query, "indexfungorum", status, name, accepted_name, rid,
                         f"https://www.indexfungorum.org/Names/NamesRecord.asp?RecordID={rid}" if rid else ""))
    return rows


FETCHERS = {
    "gbif": gbif_synonyms,
    "mo": mo_synonyms,
    "indexfungorum": indexfungorum_synonyms,
}


def collect_names(limit=None):
    names = set()
    for path, field in (
        (DATA_DIR / "bbm_records.csv", "taxonname"),
        (DATA_DIR / "mo_records.csv", "sci_name"),
    ):
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = norm_name(row.get(field) or "")
                genus, species = _split_name(name)
                if genus and species and species != "sp":
                    names.add(name)
    out = sorted(names)
    return out[:limit] if limit else out


def _valid_query_name(name):
    name = norm_name(name)
    genus, species = _split_name(name)
    if (
        not genus
        or not species
        or species in EXCLUDED_SPECIES_TOKENS
        or not genus.isalpha()
        or not species.isalpha()
    ):
        return ""
    return name


def _add_names(names, row, fields):
    for field in fields:
        name = _valid_query_name(row.get(field) or "")
        if name:
            names.add(name)


def _rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def collect_dap_unmatched_names(*, name_drift_only=False):
    """Names from DAP gold links the rule matcher missed.

    These are the highest-value synonym queries because DAP gives us a gold
    MO->UBC link to validate against.
    """
    rows = _rows(REPORTS_DIR / "dap_validation_rules_unmatched_reasons.csv")
    names = set()
    for row in rows:
        if name_drift_only and row.get("diagnostic_reason") not in DAP_NAME_DRIFT_REASONS:
            continue
        _add_names(names, row, ("gold_bbm_sci_name", "mo_sci_name"))
    return sorted(names)


def collect_dap_correct_control_names(limit=DEFAULT_CONTROL_LIMIT):
    """Names from already-correct DAP links, used as a regression/control set."""
    rows = _rows(REPORTS_DIR / "dap_validation_rules.csv")
    names = set()
    for row in rows:
        if row.get("result") != "correct":
            continue
        _add_names(names, row, (
            "gold_bbm_sci_name",
            "matched_bbm_sci_name",
            "mo_sci_name",
        ))
        if limit and len(names) >= limit:
            break
    out = sorted(names)
    return out[:limit] if limit else out


def collect_mo_name_drift_names():
    """Names from current MO resolver pairs tagged with category 05."""
    rows = _rows(REPORTS_DIR / "mo_resolution.csv")
    names = set()
    for row in rows:
        cats = set(c for c in (row.get("breakdown") or "").split(",") if c)
        if "05" not in cats:
            continue
        _add_names(names, row, ("bbm_sci_name", "platform_sci_name"))
    return sorted(names)


def collect_subset_names(subset, *, control_limit=DEFAULT_CONTROL_LIMIT, limit=None):
    if subset == "all":
        names = collect_names(limit=None)
    elif subset == "dap-unmatched":
        names = collect_dap_unmatched_names(name_drift_only=False)
    elif subset == "dap-name-drift":
        names = collect_dap_unmatched_names(name_drift_only=True)
    elif subset == "mo-name-drift":
        names = collect_mo_name_drift_names()
    elif subset == "dap-correct-control":
        names = collect_dap_correct_control_names(limit=control_limit)
    elif subset == "paper-name-drift":
        names = sorted(set(
            collect_dap_unmatched_names(name_drift_only=True)
            + collect_dap_correct_control_names(limit=control_limit)
        ))
    else:
        raise ValueError(f"unknown subset: {subset}")
    return names[:limit] if limit else names


def existing_keys(path):
    keys = set()
    if not Path(path).exists():
        return keys
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add((row.get("query_name", ""), row.get("source", "")))
    return keys


def write_rows(rows, output, append=True):
    output = Path(output)
    output.parent.mkdir(exist_ok=True)
    fieldnames = [
        "query_name", "source", "status", "name", "accepted_name",
        "genus", "species", "source_id", "source_link", "fetched_at_utc",
    ]
    exists = output.exists() and append
    with open(output, "a" if append else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def main():
    global REQUEST_TIMEOUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", default="", help="comma-separated names to fetch")
    parser.add_argument("--subset", default="all",
                        choices=[
                            "all",
                            "dap-unmatched",
                            "dap-name-drift",
                            "mo-name-drift",
                            "dap-correct-control",
                            "paper-name-drift",
                        ],
                        help="which generated-report subset to query when --names is not set")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                        help="comma-separated sources: indexfungorum,mo,gbif")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"limit auto-collected names (default {DEFAULT_LIMIT}; ignored with --all)")
    parser.add_argument("--all", action="store_true",
                        help="fetch every auto-collected name; this can take hours")
    parser.add_argument("--control-limit", type=int, default=DEFAULT_CONTROL_LIMIT,
                        help=f"correct DAP names to include in control subsets (default {DEFAULT_CONTROL_LIMIT})")
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--refresh", action="store_true",
                        help="refetch even when query/source already exists in output")
    parser.add_argument("--dry-run", action="store_true",
                        help="print selected names and pending query/source count without fetching")
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT,
                        help=f"per-request timeout in seconds (default {REQUEST_TIMEOUT})")
    args = parser.parse_args()
    REQUEST_TIMEOUT = args.timeout

    names = [norm_name(n) for n in args.names.split(",") if norm_name(n)]
    if not names:
        names = collect_subset_names(
            args.subset,
            control_limit=args.control_limit,
            limit=None if args.all else args.limit,
        )
    sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    done = set() if args.refresh else existing_keys(args.output)

    total = 0
    requests_total = sum(
        1 for name in names for source in sources
        if source in FETCHERS and (args.refresh or (name, source) not in done)
    )
    if not args.all and not args.names:
        print(
            f"Smoke run: subset={args.subset}, {len(names)} names x {len(sources)} source(s). "
            "Use --all for the full corpus."
        )
    elif not args.names:
        print(f"Subset run: subset={args.subset}, {len(names)} names x {len(sources)} source(s).")
    print(f"Output: {args.output}")
    print(f"Pending query/source pairs: {requests_total}")
    if args.dry_run:
        print("Selected names:")
        for name in names:
            print(f"  {name}")
        return
    completed = 0
    try:
        for name in names:
            for source in sources:
                if source not in FETCHERS:
                    print(f"unknown source: {source}")
                    continue
                if (name, source) in done:
                    continue
                completed += 1
                print(f"[{completed}/{requests_total}] {source}: {name}", flush=True)
                rows = FETCHERS[source](name)
                write_rows(rows or [_row(name, source, "Not found", "", "")], args.output)
                total += len(rows)
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\nInterrupted. Partial output is saved; rerun the same command to resume.")
        raise SystemExit(130)
    print(f"Wrote/updated {args.output} ({total} synonym/accepted rows fetched)")


if __name__ == "__main__":
    main()
