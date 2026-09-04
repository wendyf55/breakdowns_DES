"""External-platform abstractions.

Each database the paper touches (Mushroom Observer, MyCoPortal, GBIF, GenBank)
is one Platform. **Coupling decides the matching method** (established
empirically in this project):

  IndependentPlatform  — upstream / maintained separately; BBM stores THEIR id
                         ("MO # 82752"). Matched by that id; they cite us only
                         in free text. (MO, GenBank)
  HarvestedPlatform    — downstream of BBM (Symbiota/GBIF harvest our Specify
                         records), so the record carries OUR GUID + catalog.
                         Matched by GUID. (MyCoPortal, GBIF)

One place per database. Consumed by:
  - link_audit.py   (audit: establish_correspondences → classify)
  - get_records.py  (discovery: fetch_ours → to_common → CSV, via PlatformRecords)
  - resolve.py      (resolution: extract_refs on BBM, to_common on platform rows)
"""

import json
import re
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import MO_USER, MO_LOCATION


# ── shared HTTP ─────────────────────────────────────────────────────────────

def fetch_json(url, params=None):
    """GET a JSON endpoint, return parsed JSON (or {} on any error)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "breakdowns-DES/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"request failed ({e}): {url}")
        return {}


# ── data carriers ───────────────────────────────────────────────────────────

@dataclass
class BbmRecord:
    id: str
    catalog: str
    altcatalog: str
    guid: str
    text: str

    @property
    def our_ids(self):
        return {x for x in (self.catalog, self.altcatalog, self.guid) if x}


@dataclass
class Correspondence:
    bbm: BbmRecord
    record: dict | None
    matched_by: str          # "cited_id" | "guid"
    ref: str


# ── abstract base ───────────────────────────────────────────────────────────

class Platform(ABC):
    name = ""
    label = ""
    coupling = ""

    # audit ------------------------------------------------------------------
    @abstractmethod
    def establish_correspondences(self, bbm_records):
        """[Correspondence] linking BBM records to this platform's records."""

    def their_refs_to_us(self, record) -> set:
        """The identifiers of OURS this platform record carries/cites (catalog
        numbers or GUID). Empty set if none. Basis for cites_us and, in
        discovery, the `cites_ubc` flag."""
        return set()

    def cites_us(self, record, our_ids) -> bool:
        return bool(self.their_refs_to_us(record) & set(our_ids))

    # discovery --------------------------------------------------------------
    @abstractmethod
    def fetch_ours(self):
        """Pull the platform's records that correspond to BBM holdings → [record]."""

    # mapping / display ------------------------------------------------------
    def to_common(self, record) -> dict:
        """→ {id, sci_name, collector, date, locality} for the resolver."""
        return {}

    def record_url(self, record) -> str:
        return ""

    def display_name(self, record) -> str:
        return ""


# ── coupling: independent (matched by the id BBM stored) ────────────────────

class IndependentPlatform(Platform):
    coupling = "independent"
    ref_patterns = []

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


# ── coupling: harvested (matched by our GUID) ───────────────────────────────

class HarvestedPlatform(Platform):
    coupling = "harvested"
    guid_field = "occurrenceID"     # platform field carrying our GUID
    identity_fields = ()            # platform fields carrying our GUID / catalog
    legacy_ref_patterns = []        # non-queryable in-record annotations (reporting only)

    def their_refs_to_us(self, record) -> set:
        return {str(record.get(f)) for f in self.identity_fields if record.get(f)}

    def establish_correspondences(self, bbm_records):
        by_guid = {}
        for rec in self.fetch_ours():
            g = rec.get(self.guid_field)
            if g:
                by_guid[str(g).lower()] = rec
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
        re.compile(r"(?i)\bMO\s*#\s*0*(\d+)"),
        re.compile(r"(?i)\bMUOB[\s:#._/-]*0*(\d+)"),
        re.compile(r"(?i)mushroom\s*observer\s*(?:observation)?\s*#?\s*0*(\d+)"),
        re.compile(r"(?i)mushroomobserver\.org/(?:obs(?:ervations)?/|observer/show_observation/)?0*(\d+)"),
    ]
    _UBC_RE = re.compile(r"(?i)\bUBC\s*F?\s*0*(\d+)")

    def __init__(self, user=MO_USER, location=MO_LOCATION):
        self.user = user
        self.location = location

    # audit
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

    def their_refs_to_us(self, record):
        blob = str(record.get("notes", ""))
        for hr in record.get("herbarium_records", []) or []:
            if isinstance(hr, dict):
                blob += " " + str(hr.get("accession_number", ""))
        for cn in record.get("collection_numbers", []) or []:
            if isinstance(cn, dict):
                blob += " " + str(cn.get("number", ""))
        return {f"F{m.group(1)}" for m in self._UBC_RE.finditer(blob)}

    # discovery
    def _fetch_all(self, params):
        out, page = [], 1
        while True:
            data = fetch_json(self.API, {**params, "format": "json", "page": page})
            out += data.get("results", []) or []
            if page >= (data.get("number_of_pages", 1) or 1):
                return out
            page += 1
            time.sleep(1.0)

    def _obs_ids(self, **params):
        return [str(x) for x in self._fetch_all({**params, "detail": "none"})]

    def fetch_ours(self):
        ids = set()
        if self.user:
            ids.update(self._obs_ids(user=self.user))
        if self.location:
            ids.update(self._obs_ids(location=self.location))
        ids = sorted(ids, key=int)
        recs = []
        for i in range(0, len(ids), self.BATCH):
            recs += self._fetch_all({"id": ",".join(ids[i:i + self.BATCH]), "detail": "high"})
        return recs

    # mapping / display
    def to_common(self, record):
        return {
            "id": f"MO:{record.get('id')}",
            "sci_name": (record.get("consensus") or {}).get("name", ""),
            "collector": (record.get("owner") or {}).get("legal_name", ""),
            "date": record.get("date", ""),
            "locality": (record.get("location") or {}).get("name", ""),
        }

    def record_url(self, record):
        return f"https://mushroomobserver.org/{record.get('id')}"

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


# ── concrete: MyCoPortal (harvested — Symbiota) ─────────────────────────────

class MyCoPortal(HarvestedPlatform):
    name = "mycoportal"
    label = "MyCoPortal"
    BASE = "https://mycoportal.org/portal/api/v2"
    COLLID = 49
    PAGE = 1000
    guid_field = "occurrenceID"
    identity_fields = ("occurrenceID", "catalogNumber", "otherCatalogNumbers",
                       "recordID", "dbpk")
    legacy_ref_patterns = [re.compile(r"(?i)mycoportal\s*#?\s*(UBC\s*\d+)")]

    def fetch_ours(self):
        recs, offset = [], 0
        while True:
            data = fetch_json(f"{self.BASE}/occurrence",
                              {"collid": self.COLLID, "limit": self.PAGE, "offset": offset})
            results = data.get("results") or []
            recs += results
            if data.get("endOfRecords") or len(results) < self.PAGE:
                return recs
            offset += len(results)
            time.sleep(0.5)

    def to_common(self, record):
        return {
            "id": f"MP:{record.get('occid')}",
            "sci_name": record.get("sciname") or record.get("scientificName") or "",
            "collector": record.get("recordedBy", ""),
            "date": record.get("eventDate", ""),
            "locality": record.get("locality", ""),
        }

    def record_url(self, record):
        occid = record.get("occid")
        return (f"https://mycoportal.org/portal/collections/individual/index.php?occid={occid}"
                if occid else "https://mycoportal.org/portal/")

    def display_name(self, record):
        return record.get("sciname") or record.get("scientificName") or ""

    def probe(self, guid):
        data = fetch_json(f"{self.BASE}/occurrence", {"occurrenceID": guid, "limit": 2})
        res = data.get("results") or []
        print("top keys:", list(data.keys()), "| results:", len(res))
        if res and isinstance(res[0], dict):
            print("record fields:", list(res[0].keys()))
            print(json.dumps(res[0], indent=2)[:700])


PLATFORMS = {p.name: p for p in [MushroomObserver(), MyCoPortal()]}
