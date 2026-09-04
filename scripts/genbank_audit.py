"""GenBank linkage audit (category 01 — unlinked genomic data).

The paper (§5.1.2) claims GenBank ITS sequences derived from UBC vouchers
"frequently remain unlinked to either the Mushroom Observer or UBC lineage,"
and that this matters most for the UBC record. This quantifies it against ground
truth: `data/genbank_ground_truth.csv` lists the collection's GenBank accessions
(from the 2025 DAP sheet) with the UBC `F#` and MO id each is derived from.

OFFLINE (always): UBC -> GenBank — does the BBM record *for that voucher's F#*
cite the accession? Almost never — this is the headline unlinked number.

If a voucher-fetched `data/genbank_records.csv` is present (get_records.py
--platform genbank, after the platforms.py fix), also reports GenBank -> UBC:
does the sequence's specimen_voucher cite the F#, and which accessions were found.

    python scripts/genbank_audit.py
"""

import csv
import logging
import re

from config import DATA_DIR, REPORTS_DIR
import harmonization as harm
from platforms import norm_catalog

logger = logging.getLogger(__name__)
ACC = re.compile(r"\b([A-Z]{1,2}\d{5,8})(?:\.\d+)?\b")


def load_gt():
    with open(DATA_DIR / "genbank_ground_truth.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bbm_accessions_by_F():
    """{norm F# : set(accession tokens found anywhere on that BBM record)}."""
    out = {}
    with open(DATA_DIR / "bbm_records.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fnum = norm_catalog(r.get("catalognumber"))
            blob = " ".join(str(v) for v in r.values() if v).upper()
            out[fnum] = {m.group(1) for m in ACC.finditer(blob)}
    return out


def voucher_refs():
    """{accession : set(F#)} from a voucher-fetched genbank_records.csv, or None."""
    p = DATA_DIR / "genbank_records.csv"
    if not p.exists():
        return None
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            acc = (r.get("id", "").split(":", 1)[-1]).upper()
            out[acc] = {norm_catalog(x) for x in (r.get("ubc_ref") or "").split("; ") if x}
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    gt = load_gt()
    bbm = bbm_accessions_by_F()
    gb = voucher_refs()

    rows, cats = [], []
    ubc_links = gb_links = fetched = f_present = 0
    for r in gt:
        acc = r["accession"].upper()
        fnum = norm_catalog(r["ubc_F"])
        in_bbm = fnum in bbm
        f_present += in_bbm
        ubc_cites = acc in bbm.get(fnum, set())          # UBC record carries the accession?
        ubc_links += ubc_cites
        voucher_cites = fetched_flag = ""
        if gb is not None:
            fetched_flag = acc in gb
            fetched += bool(fetched_flag)
            voucher_cites = fnum in gb.get(acc, set())    # sequence points back to F#?
            gb_links += bool(voucher_cites)
        c = [] if ubc_cites else ["01"]                   # unlinked cross-reference
        cats.append(c)
        rows.append({"accession": acc, "ubc_F": fnum, "mo_id": r["mo_id"],
                     "f_in_bbm": in_bbm, "ubc_cites_accession": ubc_cites,
                     "genbank_fetched": fetched_flag, "voucher_cites_F": voucher_cites,
                     "breakdown": ",".join(c)})

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / "genbank_linkage.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    n = len(gt)
    logger.info("=" * 56)
    logger.info("GenBank linkage audit (collection ITS sequences vs UBC)")
    logger.info("  collection accessions (DAP ground truth) : %d", n)
    logger.info("  voucher F# present in BBM extract         : %d", f_present)
    logger.info("  UBC -> GenBank (UBC record cites the acc.): %d", ubc_links)
    logger.info("  UNLINKED on UBC lineage (category 01)     : %d  (%.1f%%)",
                n - ubc_links, 100 * (n - ubc_links) / n if n else 0)
    if gb is not None:
        logger.info("  --- with fetched genbank_records.csv ---")
        logger.info("  accessions actually fetched              : %d / %d", fetched, n)
        logger.info("  GenBank -> UBC (voucher cites the F#)     : %d", gb_links)
    else:
        logger.info("  (run get_records.py --platform genbank for the GenBank->UBC direction)")
    for code, k in harm.summarize(cats).items():
        logger.info("  breakdown %s (%s): %d", code, harm.CATEGORIES[code], k)
    logger.info("Saved per-accession -> %s", out)


if __name__ == "__main__":
    main()
