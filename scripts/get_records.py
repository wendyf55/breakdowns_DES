"""Generic discovery fetch: pull a platform's records that correspond to BBM
holdings and write a flat CSV. Driven by the Platform `fetch_ours()` +
`to_common()` + `their_refs_to_us()` contract, so one class serves MO,
MyCoPortal, GBIF, GenBank.

    python get_records.py --platform mo
    python get_records.py --platform mycoportal --limit 100
"""

import argparse
import logging

from base_get_records import BaseGetRecords
from platforms import PLATFORMS

logger = logging.getLogger(__name__)


class PlatformRecords(BaseGetRecords):
    """Fetch one platform's records via its Platform object → flat CSV."""

    FILTER_LOCALITY_FIELD = None
    FILTER_COLLECTOR_FIELD = None
    CSV_COLUMNS = ["platform", "id", "sci_name", "collector", "date",
                   "locality", "cites_ubc", "ubc_ref", "url"]

    def __init__(self, platform, limit=None):
        super().__init__(limit)
        self.platform = platform
        self.OUTPUT_NAME = f"{platform.name}_records.csv"

    def build_records(self):
        logger.info("Fetching %s records …", self.platform.label)
        recs = self.platform.fetch_ours()
        logger.info("  %d records", len(recs))
        if self.limit:
            recs = recs[:self.limit]
        out = []
        for r in recs:
            common = self.platform.to_common(r)
            refs = self.platform.their_refs_to_us(r)
            out.append({
                "platform": self.platform.name,
                "id": common.get("id"),
                "sci_name": common.get("sci_name"),
                "collector": common.get("collector"),
                "date": common.get("date"),
                "locality": common.get("locality"),
                "cites_ubc": bool(refs),
                "ubc_ref": "; ".join(sorted(refs)),
                "url": self.platform.record_url(r),
            })
        return out


def main():
    parser = argparse.ArgumentParser(description="Fetch a platform's records (discovery)")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    PlatformRecords(PLATFORMS[args.platform], limit=args.limit).run()


if __name__ == "__main__":
    main()
