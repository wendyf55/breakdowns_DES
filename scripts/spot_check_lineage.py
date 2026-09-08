"""Deterministic spot checks for the unified lineage report.

The lineage report is a join over several outputs, so this script samples rows
from representative cases and checks that status/category/action fields are
internally consistent. It is not a substitute for curator review or live API
verification; it catches reporting/join mistakes.

Output: reports/lineage_spot_check.csv
"""

import argparse
import csv
from pathlib import Path

REPORTS_DIR = Path("reports")
LINEAGE_PATH = REPORTS_DIR / "specimen_lineage_report.csv"
OUT_PATH = REPORTS_DIR / "lineage_spot_check.csv"


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _has(row, column):
    return bool(str(row.get(column, "")).strip())


def _cats(row):
    return {c for c in row.get("breakdown_categories", "").split(",") if c}


def _ok(row, condition, expectation):
    return {
        "specimen_key": row.get("specimen_key", ""),
        "case": expectation,
        "result": "pass" if condition else "fail",
        "mo_status": row.get("mo_explicit_link_status", ""),
        "mycoportal_status": row.get("mycoportal_status", ""),
        "gbif_status": row.get("gbif_status", ""),
        "genbank_accessions": row.get("genbank_accessions", ""),
        "breakdown_categories": row.get("breakdown_categories", ""),
        "recommended_action": row.get("recommended_action", ""),
    }


def first_n(rows, predicate, n=2):
    out = []
    for row in rows:
        if predicate(row):
            out.append(row)
            if len(out) >= n:
                break
    return out


def check_rows(rows):
    checks = []
    cases = [
        ("bidirectional MO links need no action", lambda r: r["mo_explicit_link_status"] == "bidirectional"),
        ("UBC missing MO id is category 01", lambda r: r["mo_explicit_link_status"] == "unidirectional_mo_to_ubc"),
        ("MO missing UBC id is category 01", lambda r: r["mo_explicit_link_status"] == "unidirectional_ubc_to_mo"),
        ("wrong MO id is category 02", lambda r: r["mo_explicit_link_status"] == "wrong_id"),
        ("attribute-only MO candidate is review action", lambda r: r["mo_explicit_link_status"] == "none" and _has(r, "mo_resolution_records")),
        ("MyCoPortal harvest gap is category 03", lambda r: r["mycoportal_status"] == "harvest_gap"),
        ("GBIF harvest gap is category 03", lambda r: r["gbif_status"] == "harvest_gap"),
        ("GenBank accession missing on UBC is action", lambda r: _has(r, "genbank_accessions") and r.get("genbank_accession_count") != r.get("genbank_ubc_cites_accession_count")),
        ("MyCoPortal orphan is category 03", lambda r: r["specimen_key"].startswith("ORPHAN:mycoportal")),
        ("GBIF orphan is category 03", lambda r: r["specimen_key"].startswith("ORPHAN:gbif")),
        ("clean row has no recommended action", lambda r: r["recommended_action"] == "none" and not _cats(r)),
    ]
    for label, predicate in cases:
        matches = first_n(rows, predicate)
        if not matches:
            checks.append({
                "specimen_key": "",
                "case": label,
                "result": "missing_case",
                "mo_status": "",
                "mycoportal_status": "",
                "gbif_status": "",
                "genbank_accessions": "",
                "breakdown_categories": "",
                "recommended_action": "",
            })
            continue
        for row in matches:
            cats = _cats(row)
            action = row.get("recommended_action", "")
            if label == "bidirectional MO links need no action":
                condition = row["mo_ids_on_bbm"] and row["mo_records_citing_bbm"]
            elif "category 01" in label:
                condition = "01" in cats and action != "none"
            elif "category 02" in label:
                condition = "02" in cats and "wrong-id" in action
            elif "attribute-only" in label:
                condition = "review attribute-only MO candidate" in action
            elif "category 03" in label:
                condition = "03" in cats and action != "none"
            elif "GenBank accession" in label:
                condition = "add GenBank accession" in action
            elif "clean row" in label:
                condition = action == "none" and not cats
            else:
                condition = False
            checks.append(_ok(row, condition, label))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage", default=str(LINEAGE_PATH))
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()
    rows = _read_csv(args.lineage)
    checks = check_rows(rows)
    Path(args.output).parent.mkdir(exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(checks[0].keys()))
        writer.writeheader()
        writer.writerows(checks)
    passed = sum(row["result"] == "pass" for row in checks)
    failed = [row for row in checks if row["result"] != "pass"]
    print(f"Wrote {len(checks)} checks -> {args.output}")
    print(f"  pass: {passed}")
    print(f"  needs attention: {len(failed)}")
    for row in failed:
        print(f"  {row['result']}: {row['case']}")


if __name__ == "__main__":
    main()
