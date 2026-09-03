"""External-platform abstractions.

Each database the paper touches (Mushroom Observer, MyCoPortal, GBIF, GenBank)
is one Platform. **Coupling decides the matching method** — established
empirically in this project:

  IndependentPlatform  — upstream / maintained separately; BBM stores THEIR id
                         (e.g. "MO # 82752"). Matched by that stored id, and the
                         record cites us back only in free text. (MO, GenBank)
  HarvestedPlatform    — downstream of BBM (Symbiota/GBIF harvest our Specify
                         records), so the record carries OUR GUID + catalog.
                         Matched by GUID. (MyCoPortal, GBIF)

The audit engine (link_audit), the discovery fetch, and the resolver all consume
Platform, so a database is defined in exactly one place. Adding GBIF or GenBank
is one concrete subclass, not a new script.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ── shared HTTP ─────────────────────────────────────────────────────────────

def fetch_json(url, params=None):
    """GET a JSON endpoint, return parsed JSON (or {} on any error)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "breakdowns-DES/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — log and continue
        print(f"request failed ({e}): {url}")
        return {}


# ── data carriers ───────────────────────────────────────────────────────────

@dataclass
class BbmRecord:
    """A BBM row reduced to what cross-referencing needs."""
    id: str
    catalog: str
    altcatalog: str
    guid: str
    text: str            # all columns concatenated (for reference scanning)

    @property
    def our_ids(self):
        return {x for x in (self.catalog, self.altcatalog, self.guid) if x}


@dataclass
class Correspondence:
    """One (BBM record, platform record) pairing the engine will classify."""
    bbm: BbmRecord
    record: dict | None       # the platform record, or None if it didn't resolve
    matched_by: str           # "cited_id" | "guid"
    ref: str                  # the id/guid that established the pairing


# ── abstract base ───────────────────────────────────────────────────────────

class Platform(ABC):
    name = ""      # cli slug
    label = ""     # human label
    coupling = ""  # "independent" | "harvested"

    @abstractmethod
    def establish_correspondences(self, bbm_records):
        """[Correspondence] linking BBM records to this platform's records."""

    @abstractmethod
    def cites_us(self, record, our_ids) -> bool:
        """True if this platform record references any of our identifiers."""

    def record_url(self, record) -> str:
        return ""

    def display_name(self, record) -> str:
        return ""

    def to_common(self, record) -> dict:
        """Map a platform record to the common comparable fields used by the
        resolver: {id, sci_name, collector, date, locality}."""
        return {}


# ── coupling layer: independent (matched by the id BBM stored) ───────────────

class IndependentPlatform(Platform):
    coupling = "independent"
    ref_patterns = []          # compiled regexes; group(1) is the platform id

    def extract_refs(self, text):
        ids = set()
        for pat in self.ref_patterns:
            for m in pat.finditer(text or ""):
                ids.add(m.group(1).strip())
        return ids

    @abstractmethod
    def lookup(self, ids):
        """{id: record} for the ids that resolve on the platform."""

    def establish_correspondences(self, bbm_records):
        ref_to_bbm = {}
        for b in bbm_records:
            for rid in self.extract_refs(b.text):
                ref_to_bbm.setdefault(rid, []).append(b)
        found = self.lookup(set(ref_to_bbm)) if ref_to_bbm else {}
        out = []
        for rid, bbms in ref_to_bbm.items():
            rec = found.get(rid)
            for b in bbms:
                out.append(Correspondence(b, rec, "cited_id", rid))
        return out


# ── coupling layer: harvested (matched by our GUID) ─────────────────────────

class HarvestedPlatform(Platform):
    coupling = "harvested"
    identity_fields = ()       # platform fields that carry our GUID / catalog
    legacy_ref_patterns = []   # non-queryable annotations, kept for reporting only

    @abstractmethod
    def fetch_ours(self):
        """Bulk-fetch our records from the platform → {guid: record}."""

    def cites_us(self, record, our_ids) -> bool:
        blob = " ".join(str(record.get(f, "")) for f in self.identity_fields)
        return any(x and x in blob for x in our_ids)

    def establish_correspondences(self, bbm_records):
        by_guid = {g.lower(): rec for g, rec in self.fetch_ours().items()}
        out = []
        for b in bbm_records:
            if not b.guid:
                continue
            out.append(Correspondence(b, by_guid.get(b.guid.lower()), "guid", b.guid))
        return out


# ── concrete: Mushroom Observer (independent) ───────────────────────────────

class MushroomObserver(IndependentPlatform):
    name = "mo"
    label = "Mushroom Observer"
    API = "https://mushroomobserver.org/api2/observations"
    BATCH = 100
    ref_patterns = [
        re.compile(r"(?i)\bMO\s*#\s*0*(\d+)"),                     # MO # 82752
        re.compile(r"(?i)\bMUOB[\s:#._/-]*0*(\d+)"),              # MUOB 12345
        re.compile(r"(?i)mushroom\s*observer\s*(?:observation)?\s*#?\s*0*(\d+)"),
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

    def cites_us(self, record, our_ids):
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

    def record_url(self, record):
        return f"https://mushroomobserver.org/{record.get('id')}"

    def display_name(self, record):
        return (record.get("consensus") or {}).get("name", "")

    def to_common(self, record):
        return {
            "id": f"MO:{record.get('id')}",
            "sci_name": (record.get("consensus") or {}).get("name", ""),
            "collector": (record.get("owner") or {}).get("legal_name", ""),
            "date": record.get("date", ""),
            "locality": (record.get("location") or {}).get("name", ""),
        }

    def probe(self, rid):
        data = fetch_json(self.API, {"id": rid, "detail": "high", "format": "json"})
        res = data.get("results") or []
        print("top keys:", list(data.keys()), "| results:", len(res))
        if res and isinstance(res[0], dict):
            for k in res[0]:
                print("  ", k)
            for f in ("notes", "herbarium_records", "collection_numbers"):
                print(f"\n{f}:", json.dumps(res[0].get(f), indent=2)[:600])


# ── concrete: MyCoPortal (harvested — Symbiota) ─────────────────────────────

class MyCoPortal(HarvestedPlatform):
    name = "mycoportal"
    label = "MyCoPortal"
    BASE = "https://mycoportal.org/portal/api/v2"
    COLLID = 49                # UBC fungi collection on MyCoPortal
    PAGE = 1000
    identity_fields = ("occurrenceID", "occurrenceid", "catalogNumber",
                       "catalognumber", "otherCatalogNumbers", "recordID", "dbpk")
    # Non-queryable — BBM records annotate "Mycoportal # UBC16931" as legacy
    # free text, but the real link is the GUID. Kept only for a reporting count.
    legacy_ref_patterns = [re.compile(r"(?i)mycoportal\s*#?\s*(UBC\s*\d+)")]

    def fetch_ours(self):
        """Bulk-page the UBC collection → {occurrenceID: record}."""
        by_guid, offset = {}, 0
        while True:
            data = fetch_json(f"{self.BASE}/occurrence",
                              {"collid": self.COLLID, "limit": self.PAGE, "offset": offset})
            results = data.get("results") or []
            for rec in results:
                guid = rec.get("occurrenceID")
                if guid:
                    by_guid[guid] = rec
            if data.get("endOfRecords") or len(results) < self.PAGE:
                break
            offset += len(results)
            time.sleep(0.5)
        return by_guid

    def record_url(self, record):
        occid = record.get("occid")
        return (f"https://mycoportal.org/portal/collections/individual/index.php?occid={occid}"
                if occid else "https://mycoportal.org/portal/")

    def display_name(self, record):
        return record.get("sciname") or record.get("scientificName") or ""

    def to_common(self, record):
        return {
            "id": f"MP:{record.get('occid')}",
            "sci_name": record.get("sciname") or record.get("scientificName") or "",
            "collector": record.get("recordedBy", ""),
            "date": record.get("eventDate", ""),
            "locality": record.get("locality", ""),
        }

    def probe(self, guid):
        data = fetch_json(f"{self.BASE}/occurrence", {"occurrenceID": guid, "limit": 2})
        res = data.get("results") or []
        print("top keys:", list(data.keys()), "| results:", len(res))
        if res and isinstance(res[0], dict):
            print("record fields:", list(res[0].keys()))
            print(json.dumps(res[0], indent=2)[:700])


PLATFORMS = {p.name: p for p in [MushroomObserver(), MyCoPortal()]}
