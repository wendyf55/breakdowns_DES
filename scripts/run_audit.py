"""Run the reproducible paper-extension audit workflow.

Default mode is offline, rule-based, and laptop-friendly. It regenerates derived
ground truth, harvested-platform coverage reports, MO attribute-resolution
reports, DAP validation diagnostics, GenBank linkage outputs, DAP implementation
status, the unified lineage report, and lineage spot checks from CSVs already
present under data/.

Network and LLM work are opt-in because they are slower and less reproducible:

    python scripts/run_audit.py
    python scripts/run_audit.py --include-network
    python scripts/run_audit.py --include-llm --llm-review-limit 10
"""

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, PROJECT_ROOT, REPORTS_DIR

logger = logging.getLogger(__name__)


def _csv_count(path):
    path = Path(path)
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def _read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _run(cmd, *, required=True):
    start = time.monotonic()
    logger.info("Running: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = round(time.monotonic() - start, 2)
    if proc.stdout.strip():
        logger.info(proc.stdout.strip())
    if proc.stderr.strip():
        logger.info(proc.stderr.strip())
    result = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "seconds": elapsed,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }
    if required and proc.returncode:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return result


def _summarize_dap(slug="rules"):
    rows = _read_csv(REPORTS_DIR / f"dap_validation_{slug}.csv")
    if not rows:
        return {}
    correct = sum(r["result"] == "correct" for r in rows)
    wrong = sum(r["result"] == "wrong_F" for r in rows)
    absent = sum(r["result"] in {"absent", "not_in_corpus"} for r in rows)
    linked = correct + wrong
    return {
        "dap_mode": slug,
        "dap_gold_links": len(rows),
        "dap_correct": correct,
        "dap_wrong": wrong,
        "dap_unmatched": absent,
        "dap_recall_pct": round(100 * correct / len(rows), 1) if rows else 0,
        "dap_precision_linked_pct": round(100 * correct / linked, 1) if linked else 0,
        "dap_wrong_link_rate_pct": round(100 * wrong / linked, 1) if linked else 0,
        "dap_review_required": sum(str(r.get("review_required")).lower() == "true" for r in rows),
    }


def _summarize_resolution():
    rows = _read_csv(REPORTS_DIR / "mo_resolution.csv")
    dups = _read_csv(REPORTS_DIR / "mo_duplicates.csv")
    if not rows:
        return {}
    out = {
        "mo_resolution_pairs": len(rows),
        "mo_duplicate_candidate_pairs": len(dups),
    }
    for key in ("bidirectional", "unidirectional_ubc_to_platform",
                "unidirectional_platform_to_ubc", "absent"):
        out[f"mo_{key}"] = sum(r["quadrant"] == key for r in rows)
    return out


def _summarize_guid(platform):
    rows = _read_csv(REPORTS_DIR / f"{platform}_guid_discovery.csv")
    if not rows:
        return {}
    out = {f"{platform}_rows": len(rows)}
    for status in ("present", "present_dup", "harvest_gap", "no_guid"):
        out[f"{platform}_{status}"] = sum(r["status"] == status for r in rows)
    return out


def _summarize_genbank():
    rows = _read_csv(REPORTS_DIR / "genbank_linkage.csv")
    if not rows:
        return {}
    ubc_links = sum(str(r.get("ubc_cites_accession")).lower() == "true" for r in rows)
    fetched = sum(str(r.get("genbank_fetched")).lower() == "true" for r in rows)
    voucher_links = sum(str(r.get("voucher_cites_F")).lower() == "true" for r in rows)
    bidirectional = sum(
        str(r.get("ubc_cites_accession")).lower() == "true"
        and str(r.get("voucher_cites_F")).lower() == "true"
        for r in rows
    )
    uni_ubc_to_platform = sum(
        str(r.get("ubc_cites_accession")).lower() == "true"
        and str(r.get("voucher_cites_F")).lower() != "true"
        for r in rows
    )
    uni_platform_to_ubc = sum(
        str(r.get("ubc_cites_accession")).lower() != "true"
        and str(r.get("voucher_cites_F")).lower() == "true"
        for r in rows
    )
    return {
        "genbank_gt_accessions": len(rows),
        "genbank_ubc_cites_accession": ubc_links,
        "genbank_unlinked_on_ubc": len(rows) - ubc_links,
        "genbank_records_fetched": fetched,
        "genbank_voucher_cites_f": voucher_links,
        "genbank_bidirectional": bidirectional,
        "genbank_unidirectional_ubc_to_platform": uni_ubc_to_platform,
        "genbank_unidirectional_platform_to_ubc": uni_platform_to_ubc,
        "genbank_no_explicit_crossref": len(rows) - bidirectional - uni_ubc_to_platform - uni_platform_to_ubc,
    }


def _summarize_dap_implementation():
    rows = _read_csv(REPORTS_DIR / "dap_implementation_audit.csv")
    if not rows:
        return {}
    out = {"dap_action_rows": len(rows)}
    for status in (
        "implemented",
        "still_unimplemented",
        "possibly_implemented_unprefixed",
        "changed_elsewhere",
        "cannot_assess_current_extract",
    ):
        out[f"dap_actions_{status}"] = sum(r["status"] == status for r in rows)
    return out


def _summarize_lineage():
    rows = _read_csv(REPORTS_DIR / "specimen_lineage_report.csv")
    if not rows:
        return {}
    bbm_rows = [r for r in rows if r["specimen_key"].startswith(("BBM:", "BBM_ID:"))]
    orphan_rows = [r for r in rows if r["specimen_key"].startswith("ORPHAN:")]
    needs_action = [r for r in rows if r.get("recommended_action") not in {"", "none"}]
    out = {
        "lineage_rows": len(rows),
        "lineage_bbm_rows": len(bbm_rows),
        "lineage_orphan_rows": len(orphan_rows),
        "lineage_rows_needing_action": len(needs_action),
    }
    for status in (
        "none",
        "bidirectional",
        "unidirectional_ubc_to_mo",
        "unidirectional_mo_to_ubc",
        "wrong_id",
    ):
        out[f"mo_explicit_{status}"] = sum(
            r.get("mo_explicit_link_status") == status for r in bbm_rows
        )
    out["lineage_mycoportal_orphan_rows"] = sum(
        r["specimen_key"].startswith("ORPHAN:mycoportal:") for r in orphan_rows
    )
    out["lineage_gbif_orphan_rows"] = sum(
        r["specimen_key"].startswith("ORPHAN:gbif:") for r in orphan_rows
    )
    return out


def _summarize_lineage_spot_check():
    rows = _read_csv(REPORTS_DIR / "lineage_spot_check.csv")
    if not rows:
        return {}
    return {
        "lineage_spot_checks": len(rows),
        "lineage_spot_checks_passed": sum(r["result"] == "pass" for r in rows),
        "lineage_spot_checks_needing_attention": sum(r["result"] != "pass" for r in rows),
    }


def _write_summary(summary):
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "audit_summary.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "value"])
        writer.writeheader()
        for key in sorted(summary):
            writer.writerow({"metric": key, "value": summary[key]})
    return path


def _llm_review_ids(reason, limit):
    path = REPORTS_DIR / "dap_validation_rules_unmatched_reasons.csv"
    rows = [r for r in _read_csv(path) if r.get("diagnostic_reason") == reason]
    return [r["mo_id"] for r in rows[:limit]]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--include-network", action="store_true",
                        help="also run live independent-platform link audits")
    parser.add_argument("--include-llm", action="store_true",
                        help="also run a capped LLM review subset from DAP unmatched rows")
    parser.add_argument("--llm-review-limit", type=int, default=10,
                        help="maximum DAP-unmatched records to send through LLM review")
    parser.add_argument("--llm-review-reason", default="name_below_rule_threshold",
                        help="unmatched diagnostic reason to sample for LLM review")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    py = sys.executable
    commands = []

    raw_occ = PROJECT_ROOT / "data2_nongenerated" / "Occurences 25.07.30(25.04.csv"
    raw_oh = PROJECT_ROOT / "data2_nongenerated" / "DAP_ObservatoryHill_MO_Records_DAP2025.csv"
    if raw_occ.exists() and raw_oh.exists():
        commands.append(_run([py, "scripts/build_dap_ground_truth.py"]))
    else:
        logger.warning("Skipping DAP build; raw data2_nongenerated files are missing")

    if args.include_network:
        commands.append(_run([py, "scripts/link_audit.py", "--platform", "mo"], required=False))
        commands.append(_run([py, "scripts/link_audit.py", "--platform", "genbank"], required=False))

    for platform in ("mycoportal", "gbif"):
        if (DATA_DIR / f"{platform}_records.csv").exists():
            commands.append(_run([py, "scripts/guid_discovery.py", "--platform", platform]))
        else:
            logger.warning("Skipping %s GUID audit; data/%s_records.csv is missing", platform, platform)

    commands.append(_run([py, "scripts/resolve.py", "--platform", "mo", "--no-llm"]))
    commands.append(_run([py, "scripts/validate_dap.py", "--no-llm"]))
    commands.append(_run([py, "scripts/genbank_audit.py"]))
    commands.append(_run([py, "scripts/dap_implementation_audit.py"]))
    commands.append(_run([py, "scripts/lineage_report.py"]))
    commands.append(_run([py, "scripts/spot_check_lineage.py"]))

    if args.include_llm:
        if not os.getenv("LLM_MODEL"):
            raise RuntimeError("--include-llm needs LLM_MODEL set in .env")
        ids = _llm_review_ids(args.llm_review_reason, args.llm_review_limit)
        if ids:
            commands.append(_run([
                py, "scripts/validate_dap.py",
                "--only-mo-ids", ",".join(ids),
                "--per-genus", "40",
                "--label", "rules+llm_review",
            ]))
        else:
            logger.warning("No DAP unmatched rows found for LLM review reason %s",
                           args.llm_review_reason)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "include_network": args.include_network,
        "include_llm": args.include_llm,
        "bbm_records": _csv_count(DATA_DIR / "bbm_records.csv"),
        "mo_records": _csv_count(DATA_DIR / "mo_records.csv"),
        "mycoportal_records": _csv_count(DATA_DIR / "mycoportal_records.csv"),
        "gbif_records": _csv_count(DATA_DIR / "gbif_records.csv"),
        "genbank_records": _csv_count(DATA_DIR / "genbank_records.csv"),
    }
    summary.update(_summarize_resolution())
    summary.update(_summarize_dap("rules"))
    summary.update(_summarize_guid("mycoportal"))
    summary.update(_summarize_guid("gbif"))
    summary.update(_summarize_genbank())
    summary.update(_summarize_dap_implementation())
    summary.update(_summarize_lineage())
    summary.update(_summarize_lineage_spot_check())

    summary_path = _write_summary(summary)
    manifest = {
        "summary": summary,
        "commands": commands,
        "outputs": {
            "summary_csv": str(summary_path),
            "manifest_json": str(REPORTS_DIR / "audit_manifest.json"),
            "dap_implementation_audit_csv": str(REPORTS_DIR / "dap_implementation_audit.csv"),
            "lineage_report_csv": str(REPORTS_DIR / "specimen_lineage_report.csv"),
            "lineage_spot_check_csv": str(REPORTS_DIR / "lineage_spot_check.csv"),
        },
    }
    manifest_path = REPORTS_DIR / "audit_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved audit summary -> %s", summary_path)
    logger.info("Saved audit manifest -> %s", manifest_path)


if __name__ == "__main__":
    main()
