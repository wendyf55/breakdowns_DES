"""Build normalized DAP ground-truth CSVs from raw 2025 audit exports.

The raw DAP occurrence export keeps three annotation rows before the real CSV
header. This script preserves that source file as-is under data2_nongenerated/
and regenerates the compact files consumed by validate_dap.py and
genbank_audit.py:

    data/dap_ground_truth.csv
    data/genbank_ground_truth.csv

Usage:
    python scripts/build_dap_ground_truth.py
"""

import argparse
import csv
import re
from pathlib import Path

from config import DATA_DIR

RAW_DIR = Path("data2_nongenerated")
OCCURRENCES = RAW_DIR / "Occurences 25.07.30(25.04.csv"
OH_IDS = RAW_DIR / "DAP_ObservatoryHill_MO_Records_DAP2025.csv"
DAP_OUT = DATA_DIR / "dap_ground_truth.csv"
GENBANK_OUT = DATA_DIR / "genbank_ground_truth.csv"

ACC_RE = re.compile(r"\b[A-Z]{1,2}\d{5,8}(?:\.\d+)?\b")
MO_RE = re.compile(r"\b(?:MUOB\s*)?0*(\d+)\b", re.I)
F_RE = re.compile(r"\bF0*(\d+)\b", re.I)

# Column positions in the DAP occurrence export's real header row. Several
# headers are duplicated ("UBC"), so positional constants are less ambiguous
# than a header-name dict for these action fields.
CATALOG_NUMBER = 16
F_NUMBER = 54
GENBANK = 55
TO_UBC = 56
UBC_STATUS = 57
TO_MO = 59
TO_GB = 60


def _clean(value):
    return str(value or "").strip()


def _mo_id(value):
    m = MO_RE.search(_clean(value))
    return m.group(1) if m else ""


def _f_number(value):
    m = F_RE.search(_clean(value))
    return f"F{int(m.group(1))}" if m else ""


def _accessions(value):
    return ACC_RE.findall(_clean(value).upper())


def _read_id_list(path):
    ids = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if not row or row[0] == "catalogNumber":
                continue
            mid = _mo_id(row[0])
            if mid:
                ids.append(mid)
    return ids


def _occurrence_rows(path):
    with open(path, encoding="cp1252", newline="") as f:
        reader = csv.reader(f)
        for _ in range(3):
            next(reader)
        header = next(reader)
        for row in reader:
            if len(row) < TO_GB + 1:
                continue
            yield dict(zip(header, row)), row


def _rows_by_mo(path):
    out = {}
    for _named, row in _occurrence_rows(path):
        mid = _mo_id(row[CATALOG_NUMBER])
        if mid and mid not in out:
            # Duplicate MO rows occur in the DAP export. The first row is the
            # curated record used by the existing Observatory Hill GT slice.
            out[mid] = row
    return out


def _has_add_mo(action):
    s = _clean(action).lower()
    return "add mo" in s or 'add prefix "mo #"' in s or "add prefix mo #" in s


def _is_wrong_mo(action):
    return "change to mo" in _clean(action).lower()


def build_dap_ground_truth(rows_by_mo, oh_ids):
    rows = []
    for mid in oh_ids:
        row = rows_by_mo.get(mid)
        if row is None:
            rows.append({
                "mo_id": mid, "ubc_F": "", "genbank": "", "to_ubc": "",
                "to_mo": "", "to_gb": "", "ubc_col": "", "gt_match": "False",
                "gt_cat01_ubc_missing_mo": "False",
                "gt_cat02_wrong_field": "False",
                "gt_cat02_wrong_id": "False",
                "gt_gb_missing": "False",
            })
            continue
        ubc_f = _f_number(row[F_NUMBER])
        to_ubc = _clean(row[TO_UBC])
        to_gb = _clean(row[TO_GB])
        rows.append({
            "mo_id": mid,
            "ubc_F": ubc_f,
            "genbank": _clean(row[GENBANK]),
            "to_ubc": to_ubc,
            "to_mo": _clean(row[TO_MO]),
            "to_gb": to_gb,
            "ubc_col": _clean(row[UBC_STATUS]),
            "gt_match": str(bool(ubc_f)),
            "gt_cat01_ubc_missing_mo": str(bool(ubc_f) and _has_add_mo(to_ubc)),
            "gt_cat02_wrong_field": "False",
            "gt_cat02_wrong_id": str(_is_wrong_mo(to_ubc)),
            "gt_gb_missing": str(bool(ubc_f) and "add mo" in to_gb.lower()),
        })
    return rows


def build_genbank_ground_truth(occurrences):
    rows, seen = [], set()
    for row in occurrences:
        ubc_f = _f_number(row[F_NUMBER])
        if not ubc_f:
            continue
        for accession in _accessions(row[GENBANK]):
            if accession in seen:
                continue
            seen.add(accession)
            rows.append({
                "accession": accession,
                "ubc_F": ubc_f,
                "mo_id": _mo_id(row[CATALOG_NUMBER]),
                "to_gb": _clean(row[TO_GB]),
            })
    return rows


def _write_csv(path, rows, fieldnames):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--occurrences", default=str(OCCURRENCES))
    parser.add_argument("--oh-ids", default=str(OH_IDS))
    parser.add_argument("--dap-out", default=str(DAP_OUT))
    parser.add_argument("--genbank-out", default=str(GENBANK_OUT))
    args = parser.parse_args()

    rows_by_mo = _rows_by_mo(Path(args.occurrences))
    dap = build_dap_ground_truth(rows_by_mo, _read_id_list(Path(args.oh_ids)))
    occurrences = [row for _named, row in _occurrence_rows(Path(args.occurrences))]
    genbank = build_genbank_ground_truth(occurrences)

    _write_csv(Path(args.dap_out), dap, [
        "mo_id", "ubc_F", "genbank", "to_ubc", "to_mo", "to_gb",
        "ubc_col", "gt_match", "gt_cat01_ubc_missing_mo",
        "gt_cat02_wrong_field", "gt_cat02_wrong_id", "gt_gb_missing",
    ])
    _write_csv(
        Path(args.genbank_out), genbank,
        ["accession", "ubc_F", "mo_id", "to_gb"],
    )

    print(f"Wrote {len(dap)} rows -> {args.dap_out}")
    print(f"Wrote {len(genbank)} rows -> {args.genbank_out}")


if __name__ == "__main__":
    main()
