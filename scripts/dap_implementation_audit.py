"""Audit whether 2025 DAP requested actions appear in current data.

This script compares the normalized DAP ground truth against the latest local
CSV extracts. It does not fetch live data. Re-run the fetch scripts first when
the live platforms may have changed.

Output:
  reports/dap_implementation_audit.csv
  reports/dap_implementation_summary.csv

Statuses:
  implemented                    the requested value is present now
  still_unimplemented            the requested value is still absent
  possibly_implemented_unprefixed a bare value is present but not a recognized ref
  changed_elsewhere              a different value is present now
  cannot_assess_current_extract  the needed field is not in the current CSVs
"""

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

from config import DATA_DIR, REPORTS_DIR
from platforms import MushroomObserver, norm_catalog

DAP_PATH = DATA_DIR / "dap_ground_truth.csv"
BBM_PATH = DATA_DIR / "bbm_records.csv"
MO_PATH = DATA_DIR / "mo_records.csv"
GENBANK_LINKAGE_PATH = REPORTS_DIR / "genbank_linkage.csv"
OUT_PATH = REPORTS_DIR / "dap_implementation_audit.csv"
SUMMARY_PATH = REPORTS_DIR / "dap_implementation_summary.csv"

ACC_RE = re.compile(r"\b([A-Z]{1,3}_?\d{5,9})(?:\.\d+)?\b")
F_RE = re.compile(r"\bF0*(\d+)\b", re.I)
MO_RE = re.compile(r"\b(?:MO\s*#?|MUOB\s*)\s*0*(\d+)\b", re.I)


def _read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _bool(value):
    return str(value).strip().lower() == "true"


def _clean(value):
    return str(value or "").strip()


def _norm_acc(value):
    value = _clean(value).upper()
    return value.split(".", 1)[0]


def _accessions(value):
    return [_norm_acc(m.group(1)) for m in ACC_RE.finditer(_clean(value).upper())]


def _target_f(value):
    m = F_RE.search(_clean(value))
    return norm_catalog(f"F{m.group(1)}") if m else ""


def _target_mo(value):
    m = MO_RE.search(_clean(value))
    return m.group(1) if m else ""


def _has_add_mo(value):
    text = _clean(value).lower()
    return "add mo" in text or 'add prefix "mo #"' in text or "add prefix mo #" in text


def _has_add_gb(value):
    text = _clean(value).lower()
    return "add gb" in text


def _has_correct_or_add_f(value):
    text = _clean(value).lower()
    return "correct to f" in text or "add f" in text or re.fullmatch(r"f0*\d+", text or "")


def _has_change_mo(value):
    return "change to mo" in _clean(value).lower()


def load_bbm(path=BBM_PATH):
    mo = MushroomObserver()
    out = {}
    for row in _read_csv(path):
        fnum = norm_catalog(row.get("catalognumber"))
        blob = " ".join(str(v) for v in row.values() if v)
        out[fnum] = {
            "row": row,
            "blob": blob,
            "mo_refs": mo.extract_refs(blob),
            "accessions": set(_accessions(blob)),
        }
    return out


def load_mo(path=MO_PATH):
    out = {}
    for row in _read_csv(path):
        mid = row.get("id", "").split(":", 1)[-1]
        out[mid] = {
            "row": row,
            "ubc_refs": {norm_catalog(x) for x in (row.get("ubc_ref") or "").split("; ") if x},
        }
    return out


def load_genbank(path=GENBANK_LINKAGE_PATH):
    by_acc = {}
    for row in _read_csv(path):
        acc = _norm_acc(row.get("accession"))
        if acc:
            by_acc[acc] = row
    return by_acc


def _base(dap_row, action_scope, requested_action):
    return {
        "mo_id": dap_row.get("mo_id", ""),
        "ubc_F": dap_row.get("ubc_F", ""),
        "genbank": dap_row.get("genbank", ""),
        "action_scope": action_scope,
        "requested_action": requested_action,
        "status": "",
        "current_evidence": "",
        "breakdown": "",
    }


def _finish(row, status, evidence, breakdown="07"):
    row["status"] = status
    row["current_evidence"] = evidence
    row["breakdown"] = "" if status == "implemented" else breakdown
    return row


def assess_add_mo_to_ubc(dap, bbm):
    row = _base(dap, "UBC/BBM", dap.get("to_ubc", ""))
    fnum = norm_catalog(dap.get("ubc_F"))
    mid = dap.get("mo_id", "")
    current = bbm.get(fnum)
    if not fnum or current is None:
        return _finish(row, "cannot_assess_current_extract", "UBC F# missing from BBM extract", "07")
    if mid in current["mo_refs"]:
        return _finish(row, "implemented", f"BBM {fnum} contains recognized MO ref {mid}")
    if mid and re.search(rf"\b0*{re.escape(mid)}\b", current["blob"]):
        return _finish(row, "possibly_implemented_unprefixed",
                       f"BBM {fnum} contains bare {mid}, but no recognized MO prefix", "02,07")
    return _finish(row, "still_unimplemented", f"BBM {fnum} does not contain MO ref {mid}")


def assess_add_gb_to_ubc(dap, bbm):
    row = _base(dap, "UBC/BBM", dap.get("to_ubc", ""))
    fnum = norm_catalog(dap.get("ubc_F"))
    accs = _accessions(dap.get("genbank", ""))
    current = bbm.get(fnum)
    if not accs:
        return _finish(row, "cannot_assess_current_extract", "DAP row has no parsed GenBank accession", "07")
    if not fnum or current is None:
        return _finish(row, "cannot_assess_current_extract", "UBC F# missing from BBM extract", "07")
    present = [acc for acc in accs if acc in current["accessions"]]
    if set(present) == set(accs):
        return _finish(row, "implemented", f"BBM {fnum} contains accession(s) {'; '.join(present)}")
    return _finish(row, "still_unimplemented",
                   f"BBM {fnum} missing accession(s) {'; '.join(sorted(set(accs) - set(present)))}")


def assess_change_mo_on_ubc(dap, bbm):
    row = _base(dap, "UBC/BBM", dap.get("to_ubc", ""))
    fnum = norm_catalog(dap.get("ubc_F"))
    target = _target_mo(dap.get("to_ubc", "")) or dap.get("mo_id", "")
    current = bbm.get(fnum)
    if not fnum or current is None:
        return _finish(row, "cannot_assess_current_extract", "UBC F# missing from BBM extract", "02,07")
    if target in current["mo_refs"]:
        return _finish(row, "implemented", f"BBM {fnum} contains requested MO ref {target}")
    if current["mo_refs"]:
        return _finish(row, "changed_elsewhere",
                       f"BBM {fnum} has MO refs {'; '.join(sorted(current['mo_refs']))}, not {target}", "02,07")
    return _finish(row, "still_unimplemented", f"BBM {fnum} does not contain requested MO ref {target}", "02,07")


def assess_correct_f_on_mo(dap, mo):
    action = dap.get("to_mo", "")
    row = _base(dap, "Mushroom Observer", action)
    mid = dap.get("mo_id", "")
    target = _target_f(action) or norm_catalog(dap.get("ubc_F"))
    current = mo.get(mid)
    if current is None:
        return _finish(row, "cannot_assess_current_extract", f"MO {mid} missing from MO extract", "07")
    refs = current["ubc_refs"]
    if target in refs:
        return _finish(row, "implemented", f"MO {mid} cites {target}")
    if refs:
        return _finish(row, "changed_elsewhere", f"MO {mid} cites {'; '.join(sorted(refs))}, not {target}", "02,07")
    return _finish(row, "still_unimplemented", f"MO {mid} does not cite {target}")


def assess_mo_genbank_action(dap):
    row = _base(dap, "Mushroom Observer", dap.get("to_mo", ""))
    return _finish(
        row,
        "cannot_assess_current_extract",
        "Current mo_records.csv keeps UBC refs but not GenBank refs/comments needed for this action",
        "07",
    )


def assess_genbank_add_f(dap, gb):
    row = _base(dap, "GenBank", dap.get("to_gb", ""))
    accs = _accessions(dap.get("genbank", ""))
    if not accs:
        return _finish(row, "cannot_assess_current_extract", "DAP row has no parsed GenBank accession", "07")
    statuses = []
    for acc in accs:
        rec = gb.get(acc)
        if rec is None:
            statuses.append((acc, "missing"))
        elif str(rec.get("voucher_cites_F")).lower() == "true":
            statuses.append((acc, "implemented"))
        else:
            statuses.append((acc, "missing_F"))
    if all(status == "implemented" for _acc, status in statuses):
        return _finish(row, "implemented", "; ".join(f"{acc}: voucher cites F#" for acc, _ in statuses))
    return _finish(row, "still_unimplemented", "; ".join(f"{acc}: {status}" for acc, status in statuses))


def assess_genbank_add_mo(dap):
    row = _base(dap, "GenBank", dap.get("to_gb", ""))
    return _finish(
        row,
        "cannot_assess_current_extract",
        "Current genbank_records.csv keeps UBC voucher refs but not MO refs/comments needed for this action",
        "07",
    )


def build_audit():
    bbm = load_bbm()
    mo = load_mo()
    gb = load_genbank()
    rows = []
    for dap in _read_csv(DAP_PATH):
        to_ubc = dap.get("to_ubc", "")
        to_mo = dap.get("to_mo", "")
        to_gb = dap.get("to_gb", "")
        if _has_change_mo(to_ubc) or _bool(dap.get("gt_cat02_wrong_id")):
            rows.append(assess_change_mo_on_ubc(dap, bbm))
        elif _has_add_mo(to_ubc) or _bool(dap.get("gt_cat01_ubc_missing_mo")):
            rows.append(assess_add_mo_to_ubc(dap, bbm))
        if _has_add_gb(to_ubc):
            rows.append(assess_add_gb_to_ubc(dap, bbm))
        if to_mo:
            if _has_correct_or_add_f(to_mo):
                rows.append(assess_correct_f_on_mo(dap, mo))
            elif _has_add_gb(to_mo) or "nr_" in to_mo.lower():
                rows.append(assess_mo_genbank_action(dap))
            else:
                rows.append(_finish(
                    _base(dap, "Mushroom Observer", to_mo),
                    "cannot_assess_current_extract",
                    "Unsupported or editorial MO action; inspect source row manually",
                    "07",
                ))
        if to_gb:
            if "add f" in to_gb.lower():
                rows.append(assess_genbank_add_f(dap, gb))
            if "add mo" in to_gb.lower():
                rows.append(assess_genbank_add_mo(dap))
    return rows


def write_csv(path, rows):
    Path(path).parent.mkdir(exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [
        "mo_id", "ubc_F", "genbank", "action_scope", "requested_action",
        "status", "current_evidence", "breakdown",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows):
    by_scope_status = Counter((r["action_scope"], r["status"]) for r in rows)
    by_status = Counter(r["status"] for r in rows)
    summary = []
    for status, count in sorted(by_status.items()):
        summary.append({"scope": "ALL", "status": status, "count": count})
    for (scope, status), count in sorted(by_scope_status.items()):
        summary.append({"scope": scope, "status": status, "count": count})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(OUT_PATH))
    parser.add_argument("--summary", default=str(SUMMARY_PATH))
    args = parser.parse_args()
    rows = build_audit()
    write_csv(args.output, rows)
    summary = summarize(rows)
    write_csv(args.summary, summary)
    print(f"Wrote {len(rows)} action rows -> {args.output}")
    print(f"Wrote summary -> {args.summary}")
    for row in summary:
        if row["scope"] == "ALL":
            print(f"  {row['status']}: {row['count']}")


if __name__ == "__main__":
    main()
