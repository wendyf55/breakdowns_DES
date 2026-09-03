#takes any two csvs in data/ and compares them
# comparison is unique because it allows you to search multiple columns in multiple ways for comparing
# similar to the orchestration pipeline, we get a seed list of candidates
# and move them through workflows
# to start, the only thing I want in compare records is this:
# seearches all columns of specify database for some kind of lookup:
# for MO, we will use "beginning with MUOB", and has x number of digits after that
# once found, we create a new column in the dataframe that's 'MUOB Numbers" and has all of those numbers
# in it. Then we do the same thing on the specify dataset, and compare the two "MUOB Numbers" columns
3# and counts the number of numbers that are present in both, present in specify, present in MO,
# and columns that have neither. 
# this is ONE method of comparison we'll use for just MO and specify count matches method, but later
# we'll have to abstract this method and try lots of different things

"""Fetch BBM specimen records from Specify 7, save all text fields to CSV.

Queries each relevant Specify table via table_rows (flat, paginated), joins
them in Python, and writes a single CSV with every text field exposed so
compare_records.py can scan for cross-reference patterns (e.g. MUOB numbers).

Usage:
    python get_bbm_records.py             # full fetch
    python get_bbm_records.py --limit 20  # probe: first 20 COs only
"""

import argparse
import csv
import logging
import sys

import requests

from config import (
    BASE_URL, USERNAME, PASSWORD, COLLECTION_ID,
    FILTER_COLLECTORS, FILTER_LOCALITY, DATA_DIR,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

LOGIN_PATH = "context/login/"
TABLE_ROWS = "table_rows/specify_rows"
PAGE_SIZE = 500
OUTPUT = DATA_DIR / "bbm_records.csv"

# ── Fields per table ───────────────────────────────────────────────
# Each list is the camelCase Specify field names passed to table_rows.
# table_rows returns rows as positional lists matching this order.

CO_FIELDS = [
    "id", "catalogNumber", "altCatalogNumber", "guid", "fieldNumber",
    "remarks", "text1", "text2", "text3", "description",
    "catalogedDate", "collectingEvent",  # FK → CE id
]

CE_FIELDS = [
    "id", "remarks", "text1", "text2",
    "stationFieldNumber", "startDate", "locality",  # FK → locality id
]

LOC_FIELDS = ["id", "localityName", "remarks", "text1", "text2"]

DET_FIELDS = [
    "id", "collectionObject",  # FK → CO id
    "remarks", "text1", "text2", "taxon", "isCurrent",
]

TAXON_FIELDS = ["id", "fullName"]

COLLECTOR_FIELDS = ["id", "collectingEvent", "agent"]  # FK → CE, agent ids

AGENT_FIELDS = ["id", "lastName", "firstName"]

# ── Output CSV columns ────────────────────────────────────────────
# Prefixed to avoid name collisions across tables.

CSV_COLUMNS = [
    "id", "catalogNumber", "altCatalogNumber", "guid", "fieldNumber",
    "co_remarks", "co_text1", "co_text2", "co_text3", "co_description",
    "catalogedDate",
    "ce_remarks", "ce_text1", "ce_text2", "ce_stationFieldNumber", "startDate",
    "localityName", "loc_remarks", "loc_text1", "loc_text2",
    "taxonName", "det_remarks", "det_text1", "det_text2",
    "collectors",
]


# ── Auth ───────────────────────────────────────────────────────────

def login(base_url, username, password, collection_id):
    """Two-step CSRF login → authenticated requests.Session."""
    session = requests.Session()
    url = f"{base_url.rstrip('/')}/{LOGIN_PATH}"

    r = session.get(url, timeout=30)
    r.raise_for_status()

    csrf = session.cookies.get("csrftoken")
    if not csrf:
        raise RuntimeError("No CSRF token from Specify login page")

    r = session.put(
        url,
        json={
            "username": username,
            "password": password,
            "collection": int(collection_id),
        },
        headers={"X-CSRFToken": csrf, "Referer": base_url},
        timeout=30,
    )
    r.raise_for_status()
    if not session.cookies.get("sessionid"):
        raise RuntimeError("Login accepted but no session cookie set")

    logger.info("Authenticated as %s", username)
    return session


# ── Fetch ──────────────────────────────────────────────────────────

def fetch_rows(session, base_url, table, fields, *, domainfilter=False, limit=None):
    """Paginate table_rows/specify_rows, yield one dict per row.

    Each row is a positional list matching `fields`; returned as a dict
    keyed by field name.  `limit` caps the total rows yielded (for probing).
    """
    field_spec = ",".join(fields)
    base = base_url.rstrip("/")
    offset = 0
    yielded = 0

    while True:
        page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - yielded)
        url = (
            f"{base}/{TABLE_ROWS}/{table}/"
            f"?fields={field_spec}&limit={page}&offset={offset}"
        )
        if domainfilter:
            url += "&domainfilter=true"

        r = session.get(url, timeout=120)
        r.raise_for_status()
        rows = r.json()

        if not rows:
            break

        for row in rows:
            yield dict(zip(fields, row))
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        if len(rows) < page:
            break
        offset += len(rows)

    logger.info("  %s: %d rows", table, yielded)


def index_by(rows, key):
    """Consume an iterator of dicts, return {row[key]: row}."""
    return {row[key]: row for row in rows}


# ── Filter ─────────────────────────────────────────────────────────

def matches_any(value, patterns):
    """True if value contains any pattern (case-insensitive), or patterns is empty."""
    if not patterns:
        return True
    if not value:
        return False
    v = str(value).lower()
    return any(p.lower() in v for p in patterns)


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch BBM records from Specify 7")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Fetch only this many collection objects (probe mode)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    if not all([BASE_URL, USERNAME, PASSWORD, COLLECTION_ID]):
        logger.error(
            "Set SPECIFY_BASE_URL, SPECIFY_USERNAME, SPECIFY_PASSWORD, "
            "SPECIFY_COLLECTION_ID in .env"
        )
        sys.exit(1)

    session = login(BASE_URL, USERNAME, PASSWORD, COLLECTION_ID)

    # 1. Collection objects — domainfilter scopes to the login discipline
    logger.info("Fetching collection objects …")
    cos = index_by(
        fetch_rows(session, BASE_URL, "collectionobject", CO_FIELDS,
                   domainfilter=True, limit=args.limit),
        "id",
    )
    logger.info("  %d collection objects", len(cos))
    if not cos:
        logger.warning("No collection objects found — check COLLECTION_ID and domainfilter")
        sys.exit(0)

    # IDs we need from related tables
    ce_ids = {co["collectingEvent"] for co in cos.values() if co["collectingEvent"]}
    co_ids = set(cos)

    # 2. Collecting events (no domainfilter — filter to our CE ids in Python)
    logger.info("Fetching collecting events …")
    ces = {}
    for row in fetch_rows(session, BASE_URL, "collectingevent", CE_FIELDS):
        if row["id"] in ce_ids:
            ces[row["id"]] = row
            if len(ces) == len(ce_ids):
                break  # found them all
    logger.info("  %d collecting events", len(ces))

    # 3. Localities — domainfilter works (locality has discipline FK)
    loc_ids = {ce["locality"] for ce in ces.values() if ce["locality"]}
    logger.info("Fetching localities …")
    locs = {}
    for row in fetch_rows(session, BASE_URL, "locality", LOC_FIELDS, domainfilter=True):
        if row["id"] in loc_ids:
            locs[row["id"]] = row
    logger.info("  %d localities", len(locs))

    # 4. Determinations (filter to our CO ids, keep current only)
    logger.info("Fetching determinations …")
    dets = {}
    for row in fetch_rows(session, BASE_URL, "determination", DET_FIELDS):
        co_id = row["collectionObject"]
        if co_id in co_ids and row.get("isCurrent"):
            dets[co_id] = row
    logger.info("  %d current determinations", len(dets))

    # 5. Taxon names — domainfilter works (taxon scoped via tree → discipline)
    taxon_ids = {d["taxon"] for d in dets.values() if d["taxon"]}
    logger.info("Fetching taxon names …")
    taxons = {}
    for row in fetch_rows(session, BASE_URL, "taxon", TAXON_FIELDS, domainfilter=True):
        if row["id"] in taxon_ids:
            taxons[row["id"]] = row
    logger.info("  %d taxons", len(taxons))

    # 6. Collectors → agents (for collector name + filtering)
    logger.info("Fetching collectors …")
    ce_agent_ids = {}  # ce_id → [agent_id, ...]
    agent_ids = set()
    for row in fetch_rows(session, BASE_URL, "collector", COLLECTOR_FIELDS):
        ce_id = row["collectingEvent"]
        if ce_id in ce_ids:
            ce_agent_ids.setdefault(ce_id, []).append(row["agent"])
            if row["agent"]:
                agent_ids.add(row["agent"])

    logger.info("Fetching agent names …")
    agents = {}
    for row in fetch_rows(session, BASE_URL, "agent", AGENT_FIELDS):
        if row["id"] in agent_ids:
            agents[row["id"]] = row
    logger.info("  %d agents", len(agents))

    # ── Join into flat records ─────────────────────────────────────
    logger.info("Joining …")

    def collector_str(ce_id):
        ids = ce_agent_ids.get(ce_id, [])
        names = []
        for aid in ids:
            a = agents.get(aid, {})
            name = " ".join(filter(None, [a.get("firstName"), a.get("lastName")]))
            if name:
                names.append(name)
        return "; ".join(names)

    records = []
    for co in cos.values():
        ce = ces.get(co.get("collectingEvent"), {})
        loc = locs.get(ce.get("locality"), {})
        det = dets.get(co["id"], {})
        taxon = taxons.get(det.get("taxon"), {})

        records.append({
            "id":                    co["id"],
            "catalogNumber":         co.get("catalogNumber"),
            "altCatalogNumber":      co.get("altCatalogNumber"),
            "guid":                  co.get("guid"),
            "fieldNumber":           co.get("fieldNumber"),
            "co_remarks":            co.get("remarks"),
            "co_text1":              co.get("text1"),
            "co_text2":              co.get("text2"),
            "co_text3":              co.get("text3"),
            "co_description":        co.get("description"),
            "catalogedDate":         co.get("catalogedDate"),
            "ce_remarks":            ce.get("remarks"),
            "ce_text1":              ce.get("text1"),
            "ce_text2":              ce.get("text2"),
            "ce_stationFieldNumber": ce.get("stationFieldNumber"),
            "startDate":             ce.get("startDate"),
            "localityName":          loc.get("localityName"),
            "loc_remarks":           loc.get("remarks"),
            "loc_text1":             loc.get("text1"),
            "loc_text2":             loc.get("text2"),
            "taxonName":             taxon.get("fullName"),
            "det_remarks":           det.get("remarks"),
            "det_text1":             det.get("text1"),
            "det_text2":             det.get("text2"),
            "collectors":            collector_str(co.get("collectingEvent")),
        })

    # ── Filter ─────────────────────────────────────────────────────
    before = len(records)
    records = [
        r for r in records
        if matches_any(r.get("localityName"), FILTER_LOCALITY)
        and matches_any(r.get("collectors"), FILTER_COLLECTORS)
    ]
    logger.info("Filtered %d → %d records", before, len(records))

    if not records:
        logger.warning("No records matched filters — nothing saved")
        return

    # ── Save ───────────────────────────────────────────────────────
    DATA_DIR.mkdir(exist_ok=True)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d records → %s", len(records), OUTPUT)


if __name__ == "__main__":
    main()