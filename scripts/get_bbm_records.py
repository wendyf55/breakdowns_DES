#inherits from base_get_records
#fetches records from specify

"""Fetch BBM specimen records from Specify 7, save all text fields to CSV.

Queries each relevant Specify table via table_rows (flat, paginated), joins
them in Python, and writes a single CSV with every text field exposed so
compare_records.py can scan for cross-reference patterns (e.g. MUOB numbers).

Usage:
    python get_bbm_records.py             # full fetch
    python get_bbm_records.py --limit 20  # probe: first 20 COs only
"""

#inherits from base_get_records
#fetches records from specify

"""Fetch BBM specimen records from Specify 7, save all text fields to CSV.

Queries each relevant Specify table via table_rows (flat, paginated), joins
them in Python, and writes a single CSV with every text field exposed so
compare_records.py can scan for cross-reference patterns (e.g. MUOB numbers).

Usage:
    python get_bbm_records.py             # full fetch
    python get_bbm_records.py --limit 20  # probe: first 20 COs only
"""

import logging
import sys

import requests

from config import BASE_URL, USERNAME, PASSWORD, COLLECTION_ID
from base_get_records import BaseGetRecords

logger = logging.getLogger(__name__)

# ── Specify endpoints / paging ─────────────────────────────────────

LOGIN_PATH = "context/login/"
TABLE_ROWS = "table_rows/specify_rows"
PAGE_SIZE = 500

# ── Fields per table ───────────────────────────────────────────────
# table_rows field names are all-lowercase; FK columns end in _id. Every
# free-text CO field is pulled so compare_records / audit_mo_links can scan
# them for cross-reference patterns; the joined fields (taxon name, collector,
# date, locality) feed resolve.py's cross-platform matching and the Ceska /
# Observatory-Hill filters.

CO_FIELDS = [
    "id", "catalognumber", "altcatalognumber", "guid", "fieldnumber",
    "remarks", "text1", "text2", "text3", "text4", "text5",
    "text6", "text7", "text8", "description",
    "catalogeddate", "collectingevent_id",           # FK → CE id
]
CE_FIELDS = ["id", "startdate", "locality_id"]        # FK → locality id
LOC_FIELDS = ["id", "localityname"]
DET_FIELDS = ["id", "collectionobject_id", "taxon_id", "iscurrent"]  # FK → CO, taxon
TAXON_FIELDS = ["id", "fullname"]
COLLECTOR_FIELDS = ["id", "collectingevent_id", "agent_id"]  # FK → CE, agent
AGENT_FIELDS = ["id", "lastname", "firstname"]


class BBMRecords(BaseGetRecords):
    """Fetch fungal collection objects from Specify 7 and flatten to CSV.

    Grabs each table once (paginated) and joins CO → CE → locality,
    CO → current determination → taxon, and CE → collector → agent in Python.
    Taxon is the flat fullname only (no rank ladder); locality is the name.
    Keeps every CO free-text field for cross-reference scanning and adds the
    matching fields (taxonname / collectors / startdate / localityname).
    """

    OUTPUT_NAME = "bbm_records.csv"
    # Optional pilot filters (blank .env = full collection). AND semantics:
    # for the Ceska-OR-Observatory-Hill union, filter on one and union in
    # analysis, or leave blank and let resolve.py match against the MO corpus.
    FILTER_LOCALITY_FIELD = "localityname"
    FILTER_COLLECTOR_FIELD = "collectors"

    CSV_COLUMNS = [
        "id", "catalognumber", "altcatalognumber", "guid", "fieldnumber",
        "co_remarks", "co_text1", "co_text2", "co_text3", "co_text4", "co_text5",
        "co_text6", "co_text7", "co_text8", "co_description", "catalogeddate",
        "startdate", "localityname", "taxonname", "collectors",
    ]

    # ── Auth ───────────────────────────────────────────────────────

    def login(self):
        """Two-step CSRF login → authenticated requests.Session."""
        session = requests.Session()
        url = f"{BASE_URL.rstrip('/')}/{LOGIN_PATH}"
        r = session.get(url, timeout=30)
        r.raise_for_status()
        csrf = session.cookies.get("csrftoken")
        if not csrf:
            raise RuntimeError("No CSRF token from Specify login page")
        r = session.put(
            url,
            json={"username": USERNAME, "password": PASSWORD,
                  "collection": int(COLLECTION_ID)},
            headers={"X-CSRFToken": csrf, "Referer": BASE_URL},
            timeout=30,
        )
        r.raise_for_status()
        if not session.cookies.get("sessionid"):
            raise RuntimeError("Login accepted but no session cookie set")
        logger.info("Authenticated as %s", USERNAME)
        return session

    # ── Fetch ──────────────────────────────────────────────────────

    def fetch_rows(self, session, table, fields, *, domainfilter=False, limit=None):
        """Paginate table_rows/specify_rows, yield one dict per row."""
        field_spec = ",".join(fields)
        base = BASE_URL.rstrip("/")
        offset = 0
        yielded = 0
        while True:
            page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - yielded)
            url = (f"{base}/{TABLE_ROWS}/{table}/"
                   f"?fields={field_spec}&limit={page}&offset={offset}")
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
                    logger.info("  %s: %d rows", table, yielded)
                    return
            if len(rows) < page:
                break
            offset += len(rows)
        logger.info("  %s: %d rows", table, yielded)

    @staticmethod
    def index_by(rows, key):
        return {row[key]: row for row in rows}

    # ── Build ──────────────────────────────────────────────────────

    def build_records(self):
        session = self.login()

        logger.info("Fetching collection objects …")
        cos = self.index_by(
            self.fetch_rows(session, "collectionobject", CO_FIELDS,
                            domainfilter=True, limit=self.limit),
            "id",
        )
        logger.info("  %d collection objects", len(cos))
        if not cos:
            logger.warning("No collection objects found — check COLLECTION_ID and domainfilter")
            return []

        ce_ids = {co["collectingevent_id"] for co in cos.values() if co["collectingevent_id"]}
        co_ids = set(cos)

        logger.info("Fetching collecting events …")
        ces = {}
        for row in self.fetch_rows(session, "collectingevent", CE_FIELDS, domainfilter=True):
            if row["id"] in ce_ids:
                ces[row["id"]] = row
                if len(ces) == len(ce_ids):
                    break
        logger.info("  %d collecting events", len(ces))

        loc_ids = {ce["locality_id"] for ce in ces.values() if ce["locality_id"]}
        logger.info("Fetching localities …")
        locs = {}
        for row in self.fetch_rows(session, "locality", LOC_FIELDS, domainfilter=True):
            if row["id"] in loc_ids:
                locs[row["id"]] = row
                if len(locs) == len(loc_ids):
                    break
        logger.info("  %d localities", len(locs))

        logger.info("Fetching determinations …")
        dets = {}
        for row in self.fetch_rows(session, "determination", DET_FIELDS, domainfilter=True):
            co_id = row["collectionobject_id"]
            if co_id in co_ids and row.get("iscurrent"):
                dets[co_id] = row
                if len(dets) == len(co_ids):
                    break
        logger.info("  %d current determinations", len(dets))

        taxon_ids = {d["taxon_id"] for d in dets.values() if d["taxon_id"]}
        logger.info("Fetching taxon names …")
        taxons = {}
        for row in self.fetch_rows(session, "taxon", TAXON_FIELDS, domainfilter=True):
            if row["id"] in taxon_ids:
                taxons[row["id"]] = row
                if len(taxons) == len(taxon_ids):
                    break
        logger.info("  %d taxons", len(taxons))

        logger.info("Fetching collectors …")
        ce_agent_ids = {}
        agent_ids = set()
        for row in self.fetch_rows(session, "collector", COLLECTOR_FIELDS, domainfilter=True):
            ce_id = row["collectingevent_id"]
            if ce_id in ce_ids:
                ce_agent_ids.setdefault(ce_id, []).append(row["agent_id"])
                if row["agent_id"]:
                    agent_ids.add(row["agent_id"])

        logger.info("Fetching agent names …")
        agents = {}
        for row in self.fetch_rows(session, "agent", AGENT_FIELDS, domainfilter=True):
            if row["id"] in agent_ids:
                agents[row["id"]] = row
                if len(agents) == len(agent_ids):
                    break
        logger.info("  %d agents", len(agents))

        logger.info("Joining …")

        def collector_str(ce_id):
            names = []
            for aid in ce_agent_ids.get(ce_id, []):
                a = agents.get(aid, {})
                name = " ".join(filter(None, [a.get("firstname"), a.get("lastname")]))
                if name:
                    names.append(name)
            return "; ".join(names)

        records = []
        for co in cos.values():
            ce = ces.get(co.get("collectingevent_id"), {})
            loc = locs.get(ce.get("locality_id"), {})
            det = dets.get(co["id"], {})
            taxon = taxons.get(det.get("taxon_id"), {})
            records.append({
                "id":               co["id"],
                "catalognumber":    co.get("catalognumber"),
                "altcatalognumber": co.get("altcatalognumber"),
                "guid":             co.get("guid"),
                "fieldnumber":      co.get("fieldnumber"),
                "co_remarks":       co.get("remarks"),
                "co_text1":         co.get("text1"),
                "co_text2":         co.get("text2"),
                "co_text3":         co.get("text3"),
                "co_text4":         co.get("text4"),
                "co_text5":         co.get("text5"),
                "co_text6":         co.get("text6"),
                "co_text7":         co.get("text7"),
                "co_text8":         co.get("text8"),
                "co_description":   co.get("description"),
                "catalogeddate":    co.get("catalogeddate"),
                "startdate":        ce.get("startdate"),
                "localityname":     loc.get("localityname"),
                "taxonname":        taxon.get("fullname"),
                "collectors":       collector_str(co.get("collectingevent_id")),
            })
        return records


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    if not all([BASE_URL, USERNAME, PASSWORD, COLLECTION_ID]):
        logger.error("Set SPECIFY_BASE_URL, SPECIFY_USERNAME, SPECIFY_PASSWORD, "
                     "SPECIFY_COLLECTION_ID in .env")
        sys.exit(1)
    args = BaseGetRecords.parse_args("Fetch BBM collection objects from Specify 7")
    BBMRecords(limit=args.limit).run()


if __name__ == "__main__":
    main()
