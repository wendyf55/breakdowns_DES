#inherits from base_get_records
# fetches from mushroom observer

"""Backward-compatible entry point. MO discovery now runs through the generic
`PlatformRecords` over the `MushroomObserver` platform (see get_records.py and
platforms.py). Output: data/mo_records.csv (Ceska ∪ Observatory Hill).

    python get_mo_records.py [--limit N]
"""

import argparse
import logging

from get_records import PlatformRecords
from platforms import MushroomObserver


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Fetch MO observations (Ceska / Observatory Hill)")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    PlatformRecords(MushroomObserver(), limit=args.limit).run()


if __name__ == "__main__":
    main()
