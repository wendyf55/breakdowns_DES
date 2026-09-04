"""Probe Specify to find where the collection date and collectors actually live.

`bbm_records.csv` comes back with startDate and collectors 100% empty while
locality/taxon are full — so the CO->CE->locality join works but CE.startDate
and the CE->collector->agent join do not. Run this once against Specify; use the
output to set get_bbm_records.py's CE_FIELDS / collector join correctly.

    python scripts/probe_ce_fields.py
"""

import logging
import requests

from get_bbm_records import BBMRecords

# candidate date fields on collectingevent (probed one at a time so an invalid
# name only skips that field instead of failing the whole request)
CE_DATE_CANDIDATES = ["startdate", "startdateverbatim", "verbatimdate",
                      "enddate", "startdateprecision", "text1", "text2", "remarks"]
COLLECTOR_FIELDS = ["id", "collectingevent_id", "agent_id", "ordernumber", "isprimary"]
AGENT_FIELDS = ["id", "lastname", "firstname"]
N = 500


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    b = BBMRecords(limit=None)
    session = b.login()

    def fetch(table, fields):
        return b.fetch_rows(session, table, fields, domainfilter=True, limit=N)

    print(f"\n=== collectingevent date fields (first {N} rows) ===")
    for f in CE_DATE_CANDIDATES:
        try:
            filled = seen = 0
            sample = None
            for row in fetch("collectingevent", ["id", f]):
                seen += 1
                v = row.get(f)
                if v not in (None, ""):
                    filled += 1
                    sample = sample or v
            print(f"  {f:20} filled {filled:4}/{seen:4}  sample={sample!r}")
        except requests.HTTPError as e:
            print(f"  {f:20} INVALID FIELD ({e})")

    print(f"\n=== collector rows (first 5; are there any? do agent_ids resolve?) ===")
    try:
        for i, row in enumerate(fetch("collector", COLLECTOR_FIELDS)):
            print("  ", row)
            if i >= 4:
                break
        else:
            print("  (no collector rows returned)")
    except requests.HTTPError as e:
        print("  collector fetch failed:", e)

    print(f"\n=== agent rows (first 5) ===")
    try:
        for i, row in enumerate(fetch("agent", AGENT_FIELDS)):
            print("  ", row)
            if i >= 4:
                break
    except requests.HTTPError as e:
        print("  agent fetch failed:", e)


if __name__ == "__main__":
    main()
