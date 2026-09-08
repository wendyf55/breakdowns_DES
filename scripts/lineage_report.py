"""Build a unified cross-platform lineage report.

This is a join layer over the existing audit outputs, not a new matcher. It
creates one row per BBM specimen and records the current trace across MO,
MyCoPortal, GBIF, and GenBank:

  BBM -> MO explicit references and MO -> BBM reverse references
  BBM -> MO attribute-resolution candidates from reports/mo_resolution.csv
  BBM -> MyCoPortal / GBIF harvested GUID coverage
  BBM -> GenBank linkage from reports/genbank_linkage.csv

Rows should be read as an action ledger: where the same specimen appears, which
links are explicit, which are inferred candidates, what breakdown categories are
triggered, and what a curator or follow-up script should do next.
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from config import DATA_DIR, REPORTS_DIR
from platforms import MushroomObserver, norm_catalog

UUID = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")


def _read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _native(rid):
    return rid.split(":", 1)[1] if ":" in str(rid) else str(rid)


def _join(values):
    return "; ".join(str(v) for v in sorted({v for v in values if v}))


def _cats(*parts):
    out = set()
    for part in parts:
        if not part:
            continue
        if isinstance(part, str):
            out.update(c for c in part.split(",") if c)
        else:
            out.update(c for c in part if c)
    return ",".join(sorted(out))


def _extract_platform_guid(row, bbm_guids):
    """Return the BBM GUID carried by a harvested-platform row, if any."""
    explicit = (row.get("our_guid") or "").strip().upper()
    if explicit:
        return explicit
    for m in UUID.finditer(row.get("ubc_ref") or ""):
        guid = m.group(0).upper()
        if guid in bbm_guids:
            return guid
    return ""


def load_bbm(path):
    rows = _read_csv(path)
    by_f, by_guid = {}, {}
    mo = MushroomObserver()
    for row in rows:
        fnum = norm_catalog(row.get("catalognumber"))
        row["_norm_f"] = fnum
        row["_guid_upper"] = (row.get("guid") or "").strip().upper()
        row["_mo_ids_on_bbm"] = sorted(mo.extract_refs(row.get("co_remarks") or ""))
        if fnum:
            by_f[fnum] = row
        if row["_guid_upper"]:
            by_guid[row["_guid_upper"]] = row
    return rows, by_f, by_guid


def mo_reverse_refs(path):
    """Return MO reverse-reference lookups by UBC F# and by MO id."""
    by_f = defaultdict(list)
    by_mo = {}
    for row in _read_csv(path):
        mid = _native(row.get("id", ""))
        refs = set()
        for ref in (row.get("ubc_ref") or "").split("; "):
            fnum = norm_catalog(ref)
            if fnum.startswith("F"):
                by_f[fnum].append(mid)
                refs.add(fnum)
        by_mo[mid] = refs
    return by_f, by_mo


def harvested_by_guid(platform, path, bbm_guids):
    by_guid = defaultdict(list)
    orphans = []
    for row in _read_csv(path):
        guid = _extract_platform_guid(row, bbm_guids)
        if guid:
            by_guid[guid].append(row)
        else:
            orphans.append(row)
    return by_guid, orphans


def mo_resolution_by_f(path):
    out = defaultdict(list)
    for row in _read_csv(path):
        fnum = norm_catalog(_native(row.get("bbm", "")))
        if fnum:
            out[fnum].append(row)
    return out


def genbank_by_f(path):
    out = defaultdict(list)
    for row in _read_csv(path):
        fnum = norm_catalog(row.get("ubc_F"))
        if fnum:
            out[fnum].append(row)
    return out


def explicit_mo_status(bbm_row, mo_back_by_f, mo_back_by_mo):
    fnum = bbm_row["_norm_f"]
    cited = set(bbm_row["_mo_ids_on_bbm"])
    back_ids = set(mo_back_by_f.get(fnum, []))
    cited_back_refs = set()
    for mid in cited:
        cited_back_refs.update(mo_back_by_mo.get(mid, set()))
    if cited and fnum in cited_back_refs:
        return "bidirectional"
    if cited and cited_back_refs:
        return "wrong_id"
    if cited:
        return "unidirectional_ubc_to_mo"
    if back_ids:
        return "unidirectional_mo_to_ubc"
    return "none"


def harvested_status(guid, rows):
    if not guid:
        return "no_guid"
    n = len(rows)
    if n == 0:
        return "harvest_gap"
    if n == 1:
        return "present"
    return "present_dup"


def genbank_summary(rows):
    if not rows:
        return "", "", "", "", []
    total = len(rows)
    accessions = _join(row.get("accession") for row in rows)
    ubc_links = sum(str(row.get("ubc_cites_accession")).lower() == "true" for row in rows)
    voucher_links = sum(str(row.get("voucher_cites_F")).lower() == "true" for row in rows)
    cats = ["01"] if ubc_links < total else []
    return accessions, str(total), str(ubc_links), str(voucher_links), cats


def recommended_action(cats, *, mo_status, mp_status, gbif_status, genbank_total,
                       genbank_ubc_links, has_resolution_candidates):
    cat_set = set(c for c in cats.split(",") if c)
    actions = []
    if "02" in cat_set:
        actions.append("curator review: check incorrect or compromised identifier")
    if mo_status == "unidirectional_mo_to_ubc":
        actions.append("add MO id to BBM if candidate is confirmed")
    elif mo_status == "unidirectional_ubc_to_mo":
        actions.append("add UBC catalog number to MO if candidate is confirmed")
    elif mo_status == "wrong_id":
        actions.append("review MO/BBM wrong-id cross-reference")
    elif has_resolution_candidates and mo_status == "none":
        actions.append("review attribute-only MO candidate")
    if mp_status == "harvest_gap":
        actions.append("check MyCoPortal harvest/publication")
    if gbif_status == "harvest_gap":
        actions.append("check GBIF harvest/publication")
    if genbank_total and int(genbank_ubc_links or 0) < int(genbank_total):
        actions.append("add GenBank accession to BBM voucher record")
    if not actions and not cat_set:
        return "none"
    return " | ".join(actions) if actions else "review breakdown categories"


def build_report(
    bbm_path=DATA_DIR / "bbm_records.csv",
    mo_path=DATA_DIR / "mo_records.csv",
    mycoportal_path=DATA_DIR / "mycoportal_records.csv",
    gbif_path=DATA_DIR / "gbif_records.csv",
    mo_resolution_path=REPORTS_DIR / "mo_resolution.csv",
    genbank_linkage_path=REPORTS_DIR / "genbank_linkage.csv",
):
    bbm_rows, _by_f, by_guid = load_bbm(bbm_path)
    bbm_guids = set(by_guid)
    mo_back, mo_back_by_mo = mo_reverse_refs(mo_path)
    mp_by_guid, mp_orphans = harvested_by_guid("mycoportal", mycoportal_path, bbm_guids)
    gbif_by_guid, gbif_orphans = harvested_by_guid("gbif", gbif_path, bbm_guids)
    mo_res = mo_resolution_by_f(mo_resolution_path)
    genbank = genbank_by_f(genbank_linkage_path)

    rows = []
    for b in bbm_rows:
        fnum = b["_norm_f"]
        guid = b["_guid_upper"]
        mp_rows = mp_by_guid.get(guid, [])
        gbif_rows = gbif_by_guid.get(guid, [])
        res_rows = mo_res.get(fnum, [])
        gb_rows = genbank.get(fnum, [])

        mo_status = explicit_mo_status(b, mo_back, mo_back_by_mo)
        mp_status = harvested_status(guid, mp_rows)
        gbif_status = harvested_status(guid, gbif_rows)
        gb_acc, gb_total, gb_ubc_links, gb_voucher_links, gb_cats = genbank_summary(gb_rows)

        res_cats = [row.get("breakdown", "") for row in res_rows]
        harvested_cats = []
        if mp_status == "harvest_gap":
            harvested_cats.append("03")
        elif mp_status == "present_dup":
            harvested_cats.append("06")
        elif mp_status == "no_guid":
            harvested_cats.append("02")
        if gbif_status == "harvest_gap":
            harvested_cats.append("03")
        elif gbif_status == "present_dup":
            harvested_cats.append("06")
        elif gbif_status == "no_guid":
            harvested_cats.append("02")
        if mo_status in {"unidirectional_mo_to_ubc", "unidirectional_ubc_to_mo"}:
            harvested_cats.append("01")
        elif mo_status == "wrong_id":
            harvested_cats.append("02")

        cats = _cats(harvested_cats, gb_cats, *res_cats)
        rows.append({
            "specimen_key": f"BBM:{fnum}" if fnum else f"BBM_ID:{b.get('id', '')}",
            "bbm_catalog": b.get("catalognumber", ""),
            "bbm_altcatalog": b.get("altcatalognumber", ""),
            "bbm_guid": b.get("guid", ""),
            "bbm_taxon": b.get("taxonname", ""),
            "bbm_collector": b.get("collectors", ""),
            "bbm_date": b.get("startdate", ""),
            "bbm_locality": b.get("localityname", ""),
            "mo_ids_on_bbm": _join(b["_mo_ids_on_bbm"]),
            "mo_records_citing_bbm": _join(mo_back.get(fnum, [])),
            "mo_explicit_link_status": mo_status,
            "mo_resolution_records": _join(_native(row.get("platform_record", "")) for row in res_rows),
            "mo_resolution_match_types": _join(row.get("match_type", "") for row in res_rows),
            "mo_resolution_quadrants": _join(row.get("quadrant", "") for row in res_rows),
            "mo_resolution_review_required": str(any(
                str(row.get("review_required")).lower() == "true" for row in res_rows
            )),
            "mycoportal_status": mp_status,
            "mycoportal_records": _join(row.get("id", "") for row in mp_rows),
            "gbif_status": gbif_status,
            "gbif_records": _join(row.get("id", "") for row in gbif_rows),
            "genbank_accessions": gb_acc,
            "genbank_accession_count": gb_total,
            "genbank_ubc_cites_accession_count": gb_ubc_links,
            "genbank_voucher_cites_f_count": gb_voucher_links,
            "breakdown_categories": cats,
            "recommended_action": recommended_action(
                cats,
                mo_status=mo_status,
                mp_status=mp_status,
                gbif_status=gbif_status,
                genbank_total=gb_total,
                genbank_ubc_links=gb_ubc_links,
                has_resolution_candidates=bool(res_rows),
            ),
        })

    for platform, orphans in (("mycoportal", mp_orphans), ("gbif", gbif_orphans)):
        for row in orphans:
            rows.append({
                "specimen_key": f"ORPHAN:{platform}:{row.get('id', '')}",
                "bbm_catalog": "",
                "bbm_altcatalog": "",
                "bbm_guid": "",
                "bbm_taxon": "",
                "bbm_collector": "",
                "bbm_date": "",
                "bbm_locality": "",
                "mo_ids_on_bbm": "",
                "mo_records_citing_bbm": "",
                "mo_explicit_link_status": "no_bbm_record",
                "mo_resolution_records": "",
                "mo_resolution_match_types": "",
                "mo_resolution_quadrants": "",
                "mo_resolution_review_required": "",
                "mycoportal_status": "orphan" if platform == "mycoportal" else "",
                "mycoportal_records": row.get("id", "") if platform == "mycoportal" else "",
                "gbif_status": "orphan" if platform == "gbif" else "",
                "gbif_records": row.get("id", "") if platform == "gbif" else "",
                "genbank_accessions": "",
                "genbank_accession_count": "",
                "genbank_ubc_cites_accession_count": "",
                "genbank_voucher_cites_f_count": "",
                "breakdown_categories": "03",
                "recommended_action": f"investigate {platform} record not joined to current BBM GUID set",
            })
    return rows


def write_report(rows, output=REPORTS_DIR / "specimen_lineage_report.csv"):
    REPORTS_DIR.mkdir(exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(REPORTS_DIR / "specimen_lineage_report.csv"))
    args = parser.parse_args()
    rows = build_report()
    out = write_report(rows, args.output)
    n_bbm = sum(1 for row in rows if row["specimen_key"].startswith(("BBM:", "BBM_ID:")))
    n_orphan = len(rows) - n_bbm
    print(f"Wrote {len(rows)} rows -> {out}")
    print(f"  BBM specimen rows: {n_bbm}")
    print(f"  harvested-platform orphan rows: {n_orphan}")


if __name__ == "__main__":
    main()
