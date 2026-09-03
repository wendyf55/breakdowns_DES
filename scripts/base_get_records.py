# base_get_records.py is script with the abstract class BaseGetRecords
#base of fetching, organizing records, and saving the csvs to reports/

"""Abstract base for the record-fetching scripts.

``BaseGetRecords`` holds the parts every source shares — CLI argument
parsing, the configurable collector/locality filter, and CSV output — and
defines the pipeline as a template method:

    build_records()  ->  filter_records()  ->  save_csv()

Each source subclasses it and implements ``build_records()``.  The actual
fetching lives in the subclass, not here, because the sources have nothing
in common at the transport level: BBM is a Specify 7 REST login + paginated
table_rows join, Mushroom Observer is a public JSON API.  What they do share
— "produce a list of flat record dicts, filter them, write a CSV to data/" —
is what this base owns.

Subclass contract:
    OUTPUT_NAME             filename written under DATA_DIR (e.g. "bbm_records.csv")
    CSV_COLUMNS             ordered output columns
    FILTER_LOCALITY_FIELD   column matched against FILTER_LOCALITY (or None)
    FILTER_COLLECTOR_FIELD  column matched against FILTER_COLLECTORS (or None)
    build_records()         return list[dict], one per record
"""

import argparse
import csv
import logging

from config import FILTER_COLLECTORS, FILTER_LOCALITY, DATA_DIR

logger = logging.getLogger(__name__)


class BaseGetRecords:
    # ── Subclass contract (override these) ─────────────────────────
    OUTPUT_NAME = None
    CSV_COLUMNS = []
    FILTER_LOCALITY_FIELD = None
    FILTER_COLLECTOR_FIELD = None

    def __init__(self, limit=None):
        self.limit = limit

    # ── Template pipeline ──────────────────────────────────────────
    def run(self):
        """build -> filter -> save. Returns the saved records."""
        records = self.build_records()
        records = self.filter_records(records)
        if not records:
            logger.warning("No records matched — nothing saved")
            return []
        self.save_csv(records)
        return records

    def build_records(self):
        """Fetch, organize, and return a list of flat dicts. Subclass implements."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement build_records()"
        )

    # ── Shared helpers ─────────────────────────────────────────────
    @staticmethod
    def matches_any(value, patterns):
        """True if value contains any pattern (case-insensitive), or patterns is empty."""
        if not patterns:
            return True
        if not value:
            return False
        v = str(value).lower()
        return any(p.lower() in v for p in patterns)

    def filter_records(self, records):
        """Apply the .env locality/collector filters to the configured columns."""
        before = len(records)
        out = []
        for r in records:
            if self.FILTER_LOCALITY_FIELD and not self.matches_any(
                r.get(self.FILTER_LOCALITY_FIELD), FILTER_LOCALITY
            ):
                continue
            if self.FILTER_COLLECTOR_FIELD and not self.matches_any(
                r.get(self.FILTER_COLLECTOR_FIELD), FILTER_COLLECTORS
            ):
                continue
            out.append(r)
        logger.info("Filtered %d → %d records", before, len(out))
        return out

    def save_csv(self, records):
        DATA_DIR.mkdir(exist_ok=True)
        path = DATA_DIR / self.OUTPUT_NAME
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=self.CSV_COLUMNS, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(records)
        logger.info("Saved %d records → %s", len(records), path)

    # ── CLI ────────────────────────────────────────────────────────
    @staticmethod
    def parse_args(description):
        parser = argparse.ArgumentParser(description=description)
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Fetch only this many records (probe mode)",
        )
        return parser.parse_args()
