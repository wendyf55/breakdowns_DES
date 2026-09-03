"""Generic BBM → external-platform cross-reference audit.

Reads data/bbm_records.csv, scans every field for a platform's identifier,
looks each id up on that platform, and classifies the link:

  bidirectional        — BBM cites the platform id *and* the platform record
                         cites one of our identifiers (catalog no. or GUID) back
  unidirectional       — BBM cites it, the record exists, but doesn't cite us
  dangling             — BBM cites it, but the id doesn't resolve

Each platform is a LinkProvider (reference regex + lookup + reverse-cite check);
the scan/classify engine is platform-agnostic.

    python link_audit.py --platform mo
    python link_audit.py --platform mycoportal
    python link_audit.py --platform mycoportal --probe UBC16931   # confirm API shape
"""

import argparse
import csv
import json
import logging
import re
import time
import urllib.parse
import urllib.request

from config import DATA_DIR, REPORTS_DIR

logger = logging.getLogger(__name__)

INPUT = DATA_DIR / "bbm_records.csv"

# BBM columns holding our own identifiers (what a platform might cite back)
OUR_ID_COLUMNS = ["catalognumber", "altcatalognumber", "guid"]


# ── HTTP helper ────────────────────────────────────────────────────

def fetch_json(url, params=None):
    """GET a JSON endpoint, return parsed JSON (or {} on any error)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "breakdowns-DES/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — log and continue
        logger.warning("request failed (%s): %s", e, url)
        return {}


# ── Provider base ──────────────────────────────────────────────────

class LinkProvider:
    """One external platform. Subclasses set ref_patterns and implement lookup /
    cites_us_back / record_url."""

    name = ""            # cli slug, e.g. "mo"
    label = ""           # human label
    ref_patterns = []    # compiled regexes; group(1) is the platform id

    def extract_ids(self, text):
        """Return the set of platform ids referenced in a string."""
        if not text:
            return set()
        ids = set()
        for pat in self.ref_patterns:
            for m in pat.finditer(text):
                ids.add(m.group(1).strip())
        return ids

    def lookup(self, ids):
        """{id: record} for the ids that resolve on the platform."""
        raise NotImplementedError

    def cites_us_back(self, record, our_ids):
        """True if this platform record references any of our identifiers."""
        raise NotImplementedError

    def record_url(self, rid, record=None):
        return ""

    def display_name(self, record):
        return ""

    def probe(self, rid):
        """Fetch one id and print the raw response shape (self-diagnostic)."""
        raise NotImplementedError


# ── Engine ─────────────────────────────────────────────────────────

def scan(provider, path=INPUT):
    """Scan the BBM CSV for a platform's ids.

    Returns (ref_map, n_rows, n_with_ref) where ref_map maps each cited
    platform id → the set of OUR identifiers (catalog no. + GUID) on the
    records that cite it (used for the reverse-cite check).
    """
    ref_map = {}
    n_rows = 0
    n_with_ref = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n_rows += 1
            blob = " ".join(str(v) for v in row.values() if v)
            ids = provider.extract_ids(blob)
            if ids:
                n_with_ref += 1
            our_ids = {row.get(c, "").strip() for c in OUR_ID_COLUMNS if row.get(c)}
            for pid in ids:
                ref_map.setdefault(pid, set()).update(our_ids)
    return ref_map, n_rows, n_with_ref


def classify(exists, cited_back):
    if not exists:
        return "dangling"
    return "bidirectional" if cited_back else "unidirectional"


def audit(provider, input_path=INPUT, limit=None):
    """Full scan → lookup → classify. Returns a results dict (no file writes)."""
    ref_map, n_rows, n_with_ref = scan(provider, str(input_path))
    ids = set(ref_map)
    if limit:
        ids = set(sorted(ids)[:limit])
    found = provider.lookup(ids) if ids else {}

    rows = []
    counts = {"bidirectional": 0, "unidirectional": 0, "dangling": 0}
    for pid in sorted(ids):
        rec = found.get(pid)
        exists = rec is not None
        cited_back = provider.cites_us_back(rec, ref_map[pid]) if exists else False
        cls = classify(exists, cited_back)
        counts[cls] += 1
        rows.append({
            "platform": provider.name,
            "id": pid,
            "our_ids": "; ".join(sorted(ref_map[pid])),
            "exists": exists,
            "cites_us_back": cited_back,
            "classification": cls,
            "url": provider.record_url(pid, rec) if exists else "",
            "name": provider.display_name(rec) if exists else "",
        })
    return {
        "n_rows": n_rows,
        "n_with_ref": n_with_ref,
        "n_ids": len(ids),
        "ref_map": ref_map,
        "found": found,
        "rows": rows,
        "counts": counts,
    }


# ── Provider: Mushroom Observer ────────────────────────────────────

class MushroomObserver(LinkProvider):
    name = "mo"
    label = "Mushroom Observer"
    API = "https://mushroomobserver.org/api2/observations"
    BATCH = 100
    ref_patterns = [
        re.compile(r"(?i)\bMO\s*#\s*0*(\d+)"),                 # MO # 82752
        re.compile(r"(?i)\bMUOB[\s:#._/-]*0*(\d+)"),           # MUOB 12345
        re.compile(r"(?i)mushroomobserver\.org/(?:obs(?:ervations)?/|observer/show_observation/)?0*(\d+)"),
    ]

    def lookup(self, ids):
        found = {}
        ids = sorted(ids, key=int)
        for i in range(0, len(ids), self.BATCH):
            batch = ids[i:i + self.BATCH]
            data = fetch_json(self.API, {"id": ",".join(batch),
                                         "detail": "high", "format": "json"})
            for obs in data.get("results", []):
                if isinstance(obs, dict) and "id" in obs:
                    found[str(obs["id"])] = obs
            time.sleep(1.0)
        return found

    def cites_us_back(self, record, our_ids):
        parts = [str(record.get("notes", ""))]
        for hr in record.get("herbarium_records", []) or []:
            if isinstance(hr, dict):
                parts += [str(hr.get("accession_number", "")),
                          str((hr.get("herbarium") or {}).get("code", ""))]
        for cn in record.get("collection_numbers", []) or []:
            if isinstance(cn, dict):
                parts.append(str(cn.get("number", "")))
        blob = " ".join(parts)
        return any(x and x in blob for x in our_ids)

    def record_url(self, rid, record=None):
        return f"https://mushroomobserver.org/{rid}"

    def display_name(self, record):
        return (record.get("consensus") or {}).get("name", "")

    def probe(self, rid):
        data = fetch_json(self.API, {"id": rid, "detail": "high", "format": "json"})
        res = data.get("results") or []
        print("top keys:", list(data.keys()), "| results:", len(res))
        if res and isinstance(res[0], dict):
            for k in res[0]:
                print("  ", k)
            for f in ("notes", "herbarium_records", "collection_numbers"):
                print(f"\n{f}:", json.dumps(res[0].get(f), indent=2)[:600])


# ── Provider: MyCoPortal (Symbiota) ────────────────────────────────

class MyCoPortal(LinkProvider):
    name = "mycoportal"
    label = "MyCoPortal"
    BASE = "https://mycoportal.org/portal/api/v2"
    # BBM stores the reference as "Mycoportal # UBC16931".
    ref_patterns = [re.compile(r"(?i)mycoportal\s*#?\s*(UBC\s*\d+)")]
    # Candidate (endpoint, param) pairs — Symbiota v2 wording varies by install;
    # the first that returns a record wins. `probe` confirms which is right.
    SEARCH = [("occurrence", "catalogNumber"),
              ("occurrence/search", "catalogNumber")]

    def extract_ids(self, text):
        return {i.replace(" ", "") for i in super().extract_ids(text)}

    def _search(self, cn):
        for path, param in self.SEARCH:
            data = fetch_json(f"{self.BASE}/{path}", {param: cn, "limit": 2})
            recs = data if isinstance(data, list) else (
                data.get("results") or data.get("data") or [])
            if recs:
                return recs[0]
        return None

    def lookup(self, ids):
        found = {}
        for cn in sorted(ids):
            rec = self._search(cn)
            if rec:
                found[cn] = rec
            time.sleep(0.5)
        return found

    def cites_us_back(self, record, our_ids):
        # MyCoPortal is harvested from BBM, so a match should carry our GUID /
        # catalog number in one of its identifier fields.
        fields = ["occurrenceID", "occurrenceid", "catalogNumber", "catalognumber",
                  "otherCatalogNumbers", "othercatalognumbers", "recordID", "recordId"]
        blob = " ".join(str(record.get(f, "")) for f in fields)
        return any(x and x in blob for x in our_ids)

    def record_url(self, rid, record=None):
        occid = (record or {}).get("occid") or (record or {}).get("id")
        if occid:
            return f"https://mycoportal.org/portal/collections/individual/index.php?occid={occid}"
        return f"https://mycoportal.org/portal/ (catalogNumber {rid})"

    def display_name(self, record):
        return record.get("scientificName") or record.get("sciname") or ""

    def probe(self, rid):
        print(f"probing catalogNumber {rid} against Symbiota v2 candidates …\n")
        for path, param in self.SEARCH:
            url = f"{self.BASE}/{path}"
            data = fetch_json(url, {param: rid, "limit": 2})
            shape = (f"list[{len(data)}]" if isinstance(data, list)
                     else f"dict keys={list(data.keys())}" if isinstance(data, dict)
                     else type(data).__name__)
            print(f"  GET {path}?{param}={rid}  ->  {shape}")
            recs = data if isinstance(data, list) else (
                data.get("results") or data.get("data") or [])
            if recs and isinstance(recs[0], dict):
                print("    first record fields:", list(recs[0].keys()))
                print("   ", json.dumps(recs[0], indent=2)[:700])
                return
        print("\n  No candidate returned a record — the endpoint/param may differ; "
              "check https://mycoportal.org/portal/api/v2/documentation")


# ── Registry + CLI ─────────────────────────────────────────────────

PROVIDERS = {p.name: p for p in [MushroomObserver(), MyCoPortal()]}


def main():
    parser = argparse.ArgumentParser(description="BBM → platform cross-reference audit")
    parser.add_argument("--platform", required=True, choices=sorted(PROVIDERS),
                        help="which platform to audit")
    parser.add_argument("--input", default=str(INPUT), help="BBM records CSV")
    parser.add_argument("--limit", type=int, default=None,
                        help="only look up the first N distinct ids (probe)")
    parser.add_argument("--probe", metavar="ID",
                        help="fetch one id, print the raw API shape, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    provider = PROVIDERS[args.platform]

    if args.probe is not None:
        provider.probe(args.probe)
        return

    res = audit(provider, input_path=args.input, limit=args.limit)
    logger.info("Scanned %d BBM rows; %d cite a %s id; %d distinct ids",
                res["n_rows"], res["n_with_ref"], provider.label, len(res["ref_map"]))
    if not res["ref_map"]:
        logger.warning("No %s references found — nothing to look up", provider.label)
        return

    rows, counts = res["rows"], res["counts"]
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{provider.name}_link_audit.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    on_platform = counts["bidirectional"] + counts["unidirectional"]
    logger.info("─" * 50)
    logger.info("Distinct %s ids cited by BBM : %d", provider.label, res["n_ids"])
    logger.info("  resolve on %-14s: %d", provider.label, on_platform)
    logger.info("    bidirectional            : %d", counts["bidirectional"])
    logger.info("    unidirectional (BBM->%s) : %d", provider.name, counts["unidirectional"])
    logger.info("  dangling (don't resolve)    : %d", counts["dangling"])
    logger.info("Saved per-id audit → %s", out)


if __name__ == "__main__":
    main()
