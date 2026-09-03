"""Backward-compatible shim.

The MO audit now lives in ``link_audit.py`` as the ``MushroomObserver``
provider (one engine, many platforms). This module keeps the existing
notebook (``reports/mo_crossref_audit.ipynb``) and the
``python audit_mo_links.py`` entry point working by re-exposing the
MO-bound helpers.

New work should use ``link_audit.py`` directly:
    python link_audit.py --platform mo
    python link_audit.py --platform mycoportal
"""

import sys

import link_audit
from link_audit import INPUT, MushroomObserver, scan, audit as _audit

MO = MushroomObserver()

# notebook-facing helpers, bound to the MO provider
extract_muob_ids = MO.extract_refs


def scan_bbm_csv(path=INPUT):
    return scan(MO, str(path))


def audit_links(input_path=INPUT, limit=None):
    res = _audit(MO, input_path=input_path, limit=limit)
    c = res["counts"]
    res["counts"] = {
        "bidirectional": c["bidirectional"],
        "unidirectional_bbm_to_mo": c["unidirectional"],
        "dangling": c["dangling"],
    }
    return res


def main():
    sys.argv = [sys.argv[0], "--platform", "mo"] + sys.argv[1:]
    link_audit.main()


if __name__ == "__main__":
    main()
