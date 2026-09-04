# Paper alignment — how `breakdowns_DES` maps onto the CHI paper

Working note tying the repo's automation to *"Breakdowns in Realizing the Digital
Extended Specimen"*. Goal: make sure what the scripts measure lines up with what
the paper finds, section by section and category by category. All numbers below
are **verified offline from the saved `data/*.csv`** (last discovery fetch,
2026‑09) unless marked otherwise.

## Where the repo fits into the paper

- **Methods · Audit sampling.** The paper's manual audit is n=131 (80 MO Phase I +
  51 backlog Phase II). The repo scales this to the whole collection: `link_audit`
  scans all **34,856** BBM records for stored platform ids and backtraces them;
  `get_records` pulls each platform's full corpus.
- **Methods · Audit procedure.** *Harmonization assessment* (bidirectional /
  unidirectional / absent + incorrect flag) is automated for all four platforms.
  *Lineage tracing* (the full BBM→MP→GBIF→MO→GenBank chain in one pass) is **not**
  built — audits run per platform. *Record harmonization* (fixing) is out of scope.
- **Methods · Phase III (Automated Catalog Audit) — new.** The scripts reproducing
  Phase I's digital‑catalog audit at full scale across all four platforms. Does not
  yet exist in the paper draft.
- **Results · the table.** `harmonization_findings.ipynb` §7 emits the per‑platform
  representation table; §8 the category‑03 GUID reconciliation.
- **Analysis · seven categories.** Mapping and status below.
- **Harmonization Automation Pipeline — new section to write.** Notes at the end.

## Verified numbers (saved data, 2026‑09)

BBM: **34,856** records, **all carry a GUID** (the plan doc's "34,940 rows / 84
without GUID" is from an earlier extract — superseded).

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
which belongs to F23090). MP coverage 99.4% (223 gaps); GBIF 95.0% (1,757 gaps).

## Category-by-category mapping

| Cat | Paper definition (§5.1) | Repo output | Verified | Status |
| --- | --- | --- | --- | --- |
| **01** Missing x‑refs | unidirectional (each way) + absent | `resolve` quadrants + `link_audit` + discovery `cites_ubc` | MO uni plat→UBC ≈992; MO 7 no‑id mentions | **Done both directions** |
| **02** Identifier integrity | wrong / hanging / wrong‑field | `link_audit.wrong` + `wrong_field` (via `reference_fields`) | MO wrong‑id = 2 | **Done** (see decisions) |
| **03** Absence | backlog / never‑published / orphans | `guid_discovery.py` + ipynb §8 | MP gap 223 / orphan 313; GBIF gap 1,757 / orphan 1,779 | **Done** for harvest‑gap & orphan; backlog n/a |
| **04** Poor confidence | 0–5 rubric, ambiguous middle | `resolve` confidence + LLM `ambiguous`→04 | (per‑pair, not tallied) | **Done** |
| **05** Nomenclature | name instability / basionym | `resolve` `name_mismatch`→05 | (per matched pair) | **Partial** — mismatch flagged; basionym needs synonym pipeline |
| **06** Duplicates | multiple records / specimen | harvested: dup-GUID (`guid_discovery` `present_dup`); independent: same-platform pairs in a matched cluster (`resolve._same_platform_pairs`) -> `reports/<platform>_duplicates.csv` | harvested dup-GUID = 0; MO smoke (1.5k BBM head + full MO): 885 clusters / 3,705 pairs, all `similar`-tier | **Done (candidate-level)** - needs id/LLM/DAP confirm |
| **07** Decay | DAP unimplemented / dead links | independent dangling→07 (dead cited id) | 1 MO dangling | **Scoped to dead links** (no DAP data) |

## Decisions & status on 02 / 03 / 06 / 07

- **02 — done.** BBM has no separate structured cross‑reference field; by practice
  MO ids live in **`co_remarks`**, now set as `MushroomObserver.reference_fields`.
  So a prefixed id there is **neither hanging nor wrong‑field** (maintainer's call),
  and category 02 for MO reduces to the **wrong‑id** sub‑case = **2**. The 7 MO
  mentions in `co_remarks` with no resolvable id ("in MO posted as …", "? MO # as …")
  are an **incomplete reference (01)**, not hanging. We report the mismatch only —
  we do **not** try to adjudicate "UBC incorrect" vs "MO incorrect" (a curatorial call).
- **03 — done.** `guid_discovery.py` reconciles BBM GUIDs against a harvested
  platform both ways: present / harvest‑gap (03) / duplicate (06) / orphan (03) /
  no‑guid (02). MP 99.4% covered (223 gaps, 313 orphans); GBIF 95.0% (1,757 gaps,
  1,779 orphans). The **undigitized‑backlog** sub‑case of 03 stays unmeasurable —
  a backlog specimen has no digital trace to join on.
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
- **07 — dead links only.** The DAP 2025 dataset is not available to diff against,
  so decay is scoped to **dead links**: an independent cited id that no longer
  resolves (`link_audit` dangling → 07). DAP drift / re‑minted GUIDs are out of reach.

## Validation against DAP ground truth (C2) — items 1 & 2

Vivian's 2025 DAP sheet is a multi-label gold standard, far bigger than the paper's
n=131. `data/dap_ground_truth.csv` (Observatory Hill slice) = 408 records, **355 with a
gold MO->F# link**, 320 cat-01 (UBC-missing-MO), 1 cat-02 wrong-id, 36 GB-missing. The
full DAP set has 1,114 F#-matched + 412 GenBank accessions (item 4, later).

`scripts/validate_dap.py` runs `resolve.py` and scores MO->UBC matching against the gold
F#. **Rule-based baseline (OH, scoped smoke run): 267/355 = 75.2% recovered (~83% of the
323 whose BBM record is in the extract), all via the `similar` tier (strict = 0 — MO/BBM
date formats differ), 33 wrong links, 55 unmatched.** The 88 wrong+unmatched are the LLM
tier's target. Per-record output: `reports/dap_validation.csv`; notebook §9.

Status / next:

- **LLM tier** — run locally on the maintainer's Ollama: set `.env` `LLM_MODEL` (+ `LLM_URL`,
  default localhost:11434), then `python scripts/validate_dap.py` (drop `--no-llm`), or
  notebook §9 with `use_llm=True`. For a clean rules-vs-LLM comparison, **`--force-llm`
  (added)** routes every block through the LLM and ignores the rule-based matcher:
  `python scripts/validate_dap.py --force-llm` (or §9 mode 2). One LLM call per genus
  block, so use a smaller `--per-genus` (e.g. 40). `--no-llm` = rules, `--force-llm` = LLM,
  same gold set.
- **Caveats** — DAP is a 2025 snapshot: MO<->F# matching is stable, direction labels may be
  partly resolved in the 2026 refetch. `--per-genus` caps decoys (recall exact; wrong-link
  count is vs. bounded decoys). 32 gold F# absent from `bbm_records.csv` (recall ceiling ~91%).
- **strict-tier / BBM date+collector (root cause found, partly fixed).** `strict` fires 0x
  because BBM `startdate` **and** `collectors` are **0% populated** across all 34,856 rows
  (locality/taxon are 100%): the CO->CE->locality join works but CE.startDate and the
  CE->collector->agent join return empty. Only `catalogeddate` (cataloging date, wrong
  semantic) is present, so matching rides on name+locality alone. **Root cause (probe_ce_fields.py):** the
  collection date lives in **`endDate`** (startDate / startDateVerbatim / verbatimDate all empty
  on this instance); collector + agent rows exist and resolve, but the **agent fetch used
  `domainfilter=True`**, hiding the high-id collector agents. **Fixes applied & tested:**
  `norm_date` hardened (17 Nov 2001 / Nov 17, 2001 / 11-17-2001 / ISO+time; 13/13 unit cases);
  `get_bbm_records.py` now coalesces `startdate = startdate or startdateverbatim or enddate` and
  fetches agents unfiltered. **Next:** re-run `python scripts/get_bbm_records.py` (maintainer
  creds) then `validate_dap.py` — strict should fire, collectors populate, and the 33 wrong /
  55 unmatched should drop.

## GenBank linkage (item 4) — unlinked genomic data

The old `GenBank` fetch used a broad `"University of British Columbia" AND fungi` search
(500-cap) that returned mostly unrelated sequences (Swedish Russula), not this collection.
Replaced with a **voucher-anchored fetch seeded from `data/genbank_ground_truth.csv`** — 213
collection ITS accessions from the DAP sheet, each mapped to a UBC F#. `scripts/genbank_audit.py`
audits linkage:

- **UBC -> GenBank (offline): 212 / 213 (99.5%) unlinked** — only 1 UBC record cites its own
  GenBank accession, though all 213 vouchers are in the extract. This is the paper's §5.1.2
  category-01 "unlinked genomic data" number at collection scale (paper's n=131 had it as a
  qualitative note only). Per-accession: `reports/genbank_linkage.csv`.
- **GenBank -> UBC (needs fetch):** run `python scripts/get_records.py --platform genbank`
  (now seeded) on a machine with NCBI access, then `genbank_audit.py` reports whether each
  sequence's `specimen_voucher` cites the F#. NCBI is blocked from this session's shells
  (proxy 403) — run it in a normal terminal.
- 183 of the DAP accessions carry a "to GB?" action (needed a link added), consistent with
  the near-total UBC-side gap.

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
