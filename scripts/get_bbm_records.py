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

# ── Fields ─────────────────────────────────────────────────────────
# Direct columns of the collectionobject table (table_rows names are
# all-lowercase). Every free-text field is pulled so compare_records.py can
# scan them for cross-reference patterns (e.g. MUOB numbers).

CO_FIELDS = [
    "id", "catalognumber", "altcatalognumber", "guid", "fieldnumber",
    "remarks", "text1", "text2", "text3", "text4", "text5",
    "text6", "text7", "text8", "description",
    "catalogeddate",
]


class BBMRecords(BaseGetRecords):
    """Fetch fungal collection objects from Specify 7 and flatten to CSV.

    First increment: the collectionobject table only — no collecting-event,
    locality, determination, taxon, or collector joins yet.  That is enough
    to scan CO text fields for cross-reference IDs (MUOB numbers); the
    related tables get layered back in as the pipeline needs them.
    """

    OUTPUT_NAME = "bbm_records.csv"
    # Locality/collector filters need the CE/locality/collector joins, which
    # aren't fetched yet — inert until those tables are added back.
    FILTER_LOCALITY_FIELD = None
    FILTER_COLLECTOR_FIELD = None

    CSV_COLUMNS = [
        "id", "catalognumber", "altcatalognumber", "guid", "fieldnumber",
        "co_remarks", "co_text1", "co_text2", "co_text3", "co_text4", "co_text5",
        "co_text6", "co_text7", "co_text8", "co_description",
        "catalogeddate",
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
            json={
                "username": USERNAME,
                "password": PASSWORD,
                "collection": int(COLLECTION_ID),
            },
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
        """Paginate table_rows/specify_rows, yield one dict per row.

        Each row arrives as a positional list matching `fields`; it is yielded
        as a dict keyed by field name.  `limit` caps the total rows yielded.
        """
        field_spec = ",".join(fields)
        base = BASE_URL.rstrip("/")
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
                    logger.info("  %s: %d rows", table, yielded)
                    return

            if len(rows) < page:
                break
            offset += len(rows)

        logger.info("  %s: %d rows", table, yielded)

    # ── Build ──────────────────────────────────────────────────────

    def build_records(self):
        session = self.login()

        logger.info("Fetching collection objects …")
        cos = list(self.fetch_rows(
            session, "collectionobject", CO_FIELDS,
            domainfilter=True, limit=self.limit,
        ))
        logger.info("  %d collection objects", len(cos))
        if not cos:
            logger.warning("No collection objects found — check COLLECTION_ID and domainfilter")
            return []

        records = []
        for co in cos:
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
            })
        return records


# ── Main ───────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    if not all([BASE_URL, USERNAME, PASSWORD, COLLECTION_ID]):
        logger.error(
            "Set SPECIFY_BASE_URL, SPECIFY_USERNAME, SPECIFY_PASSWORD, "
            "SPECIFY_COLLECTION_ID in .env"
        )
        sys.exit(1)

    args = BaseGetRecords.parse_args("Fetch BBM collection objects from Specify 7")
    BBMRecords(limit=args.limit).run()


if __name__ == "__main__":
    main()
