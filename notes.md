# Paper alignment — how `breakdowns_DES` maps onto the CHI paper

## Where the repo fits into the paper

- **Methods · Audit sampling.** The paper's manual audit is n=131 (80 MO Phase I +
  51 backlog Phase II). The repo scales this to the whole collection: `link_audit`
  scans all **34,856** BBM records for stored platform ids and backtraces them;
  `get_records` pulls each platform's full corpus.
- **Methods · Audit procedure.** *Harmonization assessment* (bidirectional /
  unidirectional / absent + incorrect flag) is automated for all four platforms.
  *Lineage tracing* (the full BBM→MP→GBIF→MO→GenBank chain in one pass) is **not**
  built — audits run per platform. *Record harmonization* (fixing) is out of scope.
- **Methods · Phase III (Automated Catalog Audit?)** The scripts reproducing
  Phase I's digital‑catalog audit with the entire fungal collection. Does not
  yet exist in the paper draft.
- **Results · the table.** `harmonization_findings.ipynb` §7 emits the per‑platform
  representation table; §8 the category‑03 GUID reconciliation.
- **Analysis · seven categories.** Mapping and status below.
- **Harmonization Automation Pipeline — new section to write.** Notes at the end.

## Verified numbers (saved data, WF local copy, 2026‑09)

BBM: 34,856 records all have a GUID

| platform | coupling | records | present (harvested) | harvest gap · 03 | orphan · 03 | dup · 06 | bidirectional | uni UBC→plat | uni plat→UBC · 01 | wrong id · 02 |
| --- | --- | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| MO | independent | 5,866 | — | — | — | — | 17 | 1 | 992 | 2 |
| MyCoPortal | harvested | 34,946 | 34,633 | 223 | 313 | 0 | — | — | — | — |
| GBIF | harvested | 34,878 | 33,099 | 1,757 | 1,779 | 0 | — | — | — | — |
| GenBank | independent | 500 | — | — | — | — | — | — | 10 | — |

Reading these: MO — of 5,866 Ceska/O.H. observations, **1,010 cite a UBC number**,
but BBM records only **20** MO ids in return, so ~**992** MO records reference a
UBC specimen we don't link back to (the paper's "UBC missing MO reference", 7× in
n=131, is ~992 at full scale). Of the 20 BBM→MO citations, 17 are clean
bidirectional and **2 are wrong** (F023000→MO#66139→F23003; F023033→MO#82705,
which belongs to F23090). MP coverage 99.4% (223 gaps); GBIF 95.0% (1,757 gaps) (probably not
actually this; needs access to up to date specify)

## Category-by-category mapping

| Cat | Paper definition (§5.1) | Repo output | Verified | Status |
| --- | --- | --- | --- | --- |
| **01** Missing x‑refs | unidirectional (each way) + absent | `resolve` quadrants + `link_audit` + discovery `cites_ubc` | MO uni plat→UBC ≈992; MO 7 no‑id mentions | **Done both directions** |
| **02** Identifier integrity | wrong / hanging / wrong‑field | `link_audit.wrong` + `wrong_field` (via `reference_fields`) | MO wrong‑id = 2 | **Done** (see decisions) |
| **03** Absence | backlog / never‑published / orphans | `guid_discovery.py` + ipynb §8 | MP gap 223 / orphan 313; GBIF gap 1,757 / orphan 1,779 | **Done** for harvest‑gap & orphan; backlog n/a |
| **04** Poor confidence | 0–5 rubric, ambiguous middle | `resolve` confidence + LLM `ambiguous`→04 | (per‑pair, not tallied) | **Done** |
| **05** Nomenclature | name instability / basionym | `resolve` `name_mismatch`→05 | (per matched pair) | **Partial** — mismatch flagged; basionym needs synonym pipeline |
| **06** Duplicates | multiple records / specimen | harvested: dup-GUID (`guid_discovery` `present_dup`); independent: same-platform pairs in a matched cluster (`resolve._same_platform_pairs`) -> `reports/<platform>_duplicates.csv` | harvested dup-GUID = 0; MO smoke (1.5k BBM head + full MO): 885 clusters / 3,705 pairs, all `similar`-tier | **Done (candidate-level)** - needs id/LLM/DAP confirm |
| **07** Decay | DAP unimplemented / dead links | independent dangling→07 (dead cited id) | 1 MO dangling | **Scoped to dead links** (TODO: incorporate DAP data) |

## Decisions & status on 02 / 03 / 06 / 07

- **02 — done.** BBM has no separate structured cross‑reference field; by practice
  MO ids live in **`co_remarks`**, now set as `MushroomObserver.reference_fields`.
  So a prefixed id there is **neither hanging nor wrong‑field** (maintainer's call),
  and category 02 for MO reduces to the **wrong‑id** sub‑case = **2**. The 7 MO
  mentions in `co_remarks` with no resolvable id ("in MO posted as …", "? MO # as …")
  are an **incomplete reference (01)**, not hanging. We report the mismatch only.
- **03 — done.** `guid_discovery.py` reconciles BBM GUIDs against a harvested
  platform both ways: present / harvest‑gap (03) / duplicate (06) / orphan (03) /
  no‑guid (02).
- **06 - done (candidate-level), with a caveat.** Two sides:
  (a) *harvested* duplicate-GUID -> `guid_discovery` `present_dup` (currently 0);
  (b) *independent / attribute-level* -> `resolve.py` now keeps same-platform pairs
  within an attribute-matched cluster (`_same_platform_pairs`), tags them 06, and
  writes `reports/<platform>_duplicates.csv`. **Caveat:** on the single-collector /
  single-locality Ceska-OH corpus these are almost all `similar`-tier matches (name +
  locality/collector overlap, no strict date, no id). A smoke run on a 1,500-row BBM
  head + full MO gave 3,705 pairs = **885 clusters** (634 size-2, a few size 30+), so
  the raw count is dominated by false positives (same taxon + site, different dates =
  distinct specimens - the weak-evidence case the LLM domain hint warns about). Treat
  as **review candidates**, report **clusters not pairs**, and confirm via the
  identifier/LLM tier or the DAP ground truth before trusting a number. Open design
  question: have `resolve()` emit clusters (with size) instead of pairwise rows?
- **07 — dead links only.** The DAP 2025 dataset has not yet been diffed against,
  so decay is scoped to **dead links**: an independent cited id that no longer
  resolves (`link_audit` dangling → 07). DAP drift / re‑minted GUIDs are out of reach.

## Validation against DAP ground truth (C2) — items 1 & 2

`scripts/validate_dap.py` runs `resolve.py` and scores MO->UBC matching against the gold
F#. **Rule-based baseline (OH, scoped smoke run): 267/355 = 75.2% recovered (~83% of the
323 whose BBM record is in the extract), all via the `similar` tier (strict = 0 — MO/BBM
date formats differ), 33 wrong links, 55 unmatched.** The 88 wrong+unmatched are the LLM
tier's target. Per-record output: `reports/dap_validation.csv`; notebook §9.

## The working harmonization pipeline (for the automation section)

- **`platforms.py` — the axis.** One `Platform` per external DB, split by *coupling*:
  `IndependentPlatform` (MO, GenBank — BBM stores *their* id) vs `HarvestedPlatform`
  (MyCoPortal, GBIF — they carry *our* GUID). Shared `norm_catalog()` makes
  padded/unpadded F‑numbers compare equal; `refs_by_field()` records which BBM
  column a cited id came from; `reference_fields` names each platform's recognized
  reference column. A new database = one subclass.
- **Fetch (`get_bbm_records.py`, `get_records.py`, `base_get_records.py`).** Specify‑7
  login + table joins → `bbm_records.csv`; generic `fetch_ours()→to_common()` →
  `<platform>_records.csv`. Filters are `.env`‑driven.
- **`link_audit.py` — cross‑reference audit (Goal 1).** Establishes BBM↔platform
  correspondences, classifies bidirectional / unidirectional / dangling, and tags
  category‑02 sub‑cases per §5.1.3: **wrong** (platform record cites a *different*
  catalog — per BBM record, so a co‑citing true owner doesn't mask it) and
  **wrong‑field** (id outside `reference_fields`). Emits per‑ref CSV + `wrong` list
  - `cat02` summary.
- **`guid_discovery.py` — offline coverage audit (Goal 1, category 03/06).** Reads
  the saved discovery CSV, reconciles BBM GUIDs both directions, writes
  `reports/<platform>_guid_discovery.csv` + a four‑quadrant summary.
- **`resolve.py` — specimen resolution (Goal 2).** Attribute matching (name/date/
  locality/collector) via the vendored rule engine + optional LLM tier, then scores
  matched pairs into the four quadrants. Tags 04 (ambiguous), 05 (name mismatch),
  02 (platform cites a different catalog). Also returns same-platform duplicate pairs -> 06 (candidate-level; `reports/<platform>_duplicates.csv`).
- **`harmonization.py` — the framework as code.** Seven categories, the 0–5
  confidence rubric (maps to Fig‑4), and `classify_breakdowns`, driven by explicit
  02 sub‑case signals.
- **`scripts/evaluators/` — vendored classify half** of the orchestration dedup
  pipeline, self‑contained.
