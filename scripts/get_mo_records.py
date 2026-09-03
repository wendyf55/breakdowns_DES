#inherits from base_get_records
# fetches from mushroom observer

"""Fetch Mushroom Observer observations for the Ceska account and/or the
Observatory Hill location (README §2 — the MO→BBM / discovery direction).

Unlike get_bbm_records (our own collection) this is *seeded* on MO: it takes
the union of observations by MO user MO_USER (the Ceskas) and observations in
the MO_LOCATION bounding box (Observatory Hill), fetches each at detail=high,
and flags whether the record cites a UBC herbarium number back (notes like
"Herbarium Specimen: UBC F22976"). That "cites UBC" flag is what lets us score
the MO→UBC unidirectional links the BBM→MO audit can't see.

    python get_mo_records.py            # full union
    python get_mo_records.py --limit 50 # probe
"""

import logging
import re
import sys
import time

from config import MO_USER, MO_LOCATION
from base_get_records import BaseGetRecords
from link_audit import fetch_json   # shared HTTP helper

logger = logging.getLogger(__name__)

MO_OBS = "https://mushroomobserver.org/api2/observations"
BATCH = 100
# UBC herbarium citation inside an MO note, e.g. "Herbarium Specimen: UBC F22976"
UBC_REF_RE = re.compile(r"(?i)\bUBC\s*F?\s*0*(\d+)")


class MORecords(BaseGetRecords):
    OUTPUT_NAME = "mo_records.csv"
    # Filtering is the MO query itself (user / location), not a column filter.
    FILTER_LOCALITY_FIELD = None
    FILTER_COLLECTOR_FIELD = None

    CSV_COLUMNS = [
        "mo_id", "date", "location_name", "consensus_name", "owner",
        "specimen_available", "cites_ubc", "ubc_catalog_cited", "notes", "mo_url",
    ]

    # ── seed queries ───────────────────────────────────────────────

    def _fetch_all(self, params):
        """Fetch every page of an MO observations query; return all result items."""
        out, page = [], 1
        while True:
            data = fetch_json(MO_OBS, {**params, "format": "json", "page": page})
            out += data.get("results", []) or []
            n_pages = data.get("number_of_pages", 1) or 1
            if page >= n_pages:
                return out
            page += 1
            time.sleep(1.0)

    def _obs_ids(self, **params):
        """Return the list of observation ids matching a seed query (detail=none)."""
        return [str(x) for x in self._fetch_all({**params, "detail": "none"})]

    def build_records(self):
        ids = set()
        if MO_USER:
            u = self._obs_ids(user=MO_USER)
            logger.info("Ceska user %s: %d observations", MO_USER, len(u))
            ids.update(u)
        if MO_LOCATION:
            loc = self._obs_ids(location=MO_LOCATION)
            logger.info("location %s: %d observations", MO_LOCATION, len(loc))
            ids.update(loc)
        logger.info("union (Ceska OR Observatory Hill): %d distinct observations", len(ids))
        if not ids:
            logger.warning("No MO ids — set MO_USER / MO_LOCATION in .env")
            return []

        ids_sorted = sorted(ids, key=int)
        if self.limit:
            ids_sorted = ids_sorted[:self.limit]

        # ── fetch details in batches ───────────────────────────────
        records = []
        for i in range(0, len(ids_sorted), BATCH):
            batch = ids_sorted[i:i + BATCH]
            for obs in self._fetch_all({"id": ",".join(batch), "detail": "high"}):
                if isinstance(obs, dict):
                    records.append(self._row(obs))
            logger.info("  fetched %d/%d", min(i + BATCH, len(ids_sorted)), len(ids_sorted))
            time.sleep(1.0)
        return records

    # ── row assembly ───────────────────────────────────────────────

    @staticmethod
    def _row(obs):
        notes = obs.get("notes") or ""
        m = UBC_REF_RE.search(notes)
        return {
            "mo_id": obs.get("id"),
            "date": obs.get("date"),
            "location_name": (obs.get("location") or {}).get("name"),
            "consensus_name": (obs.get("consensus") or {}).get("name"),
            "owner": (obs.get("owner") or {}).get("legal_name"),
            "specimen_available": obs.get("specimen_available"),
            "cites_ubc": bool(m),
            "ubc_catalog_cited": f"F{m.group(1)}" if m else "",
            "notes": notes[:250],
            "mo_url": f"https://mushroomobserver.org/{obs.get('id')}",
        }


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    if not (MO_USER or MO_LOCATION):
        logger.error("Set MO_USER and/or MO_LOCATION in .env")
        sys.exit(1)
    args = BaseGetRecords.parse_args("Fetch MO observations (Ceska / Observatory Hill)")
    MORecords(limit=args.limit).run()


if __name__ == "__main__":
    main()
