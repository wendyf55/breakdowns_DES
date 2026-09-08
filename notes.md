# Paper alignment — how `breakdowns_DES` maps onto the CHI paper

## Where the repo fits into the paper

- **Methods · Audit sampling.** The paper's manual audit is n=131 (80 MO Phase I +
  51 backlog Phase II). The repo scales this to the whole collection: `link_audit`
  scans all **34,856** BBM records for stored platform ids and backtraces them;
  `get_records` pulls each platform's full corpus.
- **Methods · Audit procedure.** *Harmonization assessment* (bidirectional /
  unidirectional / absent + incorrect flag) is automated for all four platforms.
  *Lineage tracing* is now represented by `reports/specimen_lineage_report.csv`,
  which joins BBM, MO, MP, GBIF, and GenBank outputs into one action ledger.
  *Record harmonization* (fixing live database records) is out of scope.
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
| MO | independent | 5,866 | — | — | — | — | 17 | 2 | 1,008 | 2 |
| MyCoPortal | harvested | 34,946 | 34,633 | 223 | 313 | 0 | — | — | — | — |
| GBIF | harvested | 34,878 | 33,099 | 1,757 | 1,779 | 0 | — | — | — | — |
| GenBank | independent | 213 | — | — | — | — | 1 | 0 | 182 | — |

Reading these: MO — of 5,866 Ceska/O.H. observations, **1,025 BBM specimen rows
are cited by at least one MO record** (1,033 total MO reverse-reference mentions),
but BBM records only **21** MO ids in return, so **1,008** BBM specimen rows are
cited by MO without a reciprocal BBM→MO link (the paper's "UBC missing MO
reference", 7× in n=131, is 1,008 at full scale). Of the 21 BBM→MO citations, 17 are clean
bidirectional and **2 are wrong** (F023000→MO#66139→F23003; F023033→MO#82705,
which belongs to F23090). MP coverage is 99.4% (223 gaps); GBIF coverage is
95.0% (1,757 gaps), based on the latest local fetch; rerun network fetches
before final submission.

GenBank has a different denominator: **212 / 213 UBC voucher records do not cite
their own GenBank accession**, which is the strongest category-01 paper claim.
From the GenBank side, 183 / 213 fetched sequence records cite the UBC voucher
F#, so explicit-link quadrants are 1 bidirectional, 182 GenBank→UBC only, 0
UBC→GenBank only, and 30 with no explicit cross-reference in the current fields.

Important counting distinction: the table above is the **Goal 1 explicit-link**
view. It counts stored identifiers and reverse identifiers in the latest fetched
data. `reports/mo_resolution.csv` is the **Goal 2 resolver-candidate** view: it
counts inferred BBM/MO candidate pairs from name/date/locality/collector. Both
are valid, but only the explicit-link view should be used as the paper-facing
cross-platform representation table.

## Category-by-category mapping

| Cat | Paper definition (§5.1) | Repo output | Verified | Status |
| --- | --- | --- | --- | --- |
| **01** Missing x‑refs | unidirectional (each way) + absent | `resolve` quadrants + `link_audit` + discovery `cites_ubc` | MO explicit uni plat→UBC = 1,008; GenBank UBC-missing-accession = 212/213 | **Done both directions** |
| **02** Identifier integrity | wrong / hanging / wrong‑field | `link_audit.wrong` + `wrong_field` (via `reference_fields`) | MO wrong‑id = 2 | **Done** (see decisions) |
| **03** Absence | backlog / never‑published / orphans | `guid_discovery.py` + ipynb §8 | MP gap 223 / orphan 313; GBIF gap 1,757 / orphan 1,779 | **Done** for harvest‑gap & orphan; backlog n/a |
| **04** Poor confidence | 0–5 rubric, ambiguous middle | `resolve` confidence; LLM `ambiguous`→04 when enabled | 2,657 attribute-only MO candidates have confidence 2.5-3.0; 0 accepted 04 rows in the default rule run | **Rubric done; category-04 claims need careful wording** |
| **05** Nomenclature | name instability / basionym | `resolve` `name_mismatch`→05 | (per matched pair) | **Partial** — mismatch flagged; basionym/accepted-name expansion needs synonym pipeline |
| **06** Duplicates | multiple records / specimen | harvested: dup-GUID (`guid_discovery` `present_dup`); independent: same-platform pairs in a matched cluster (`resolve._same_platform_pairs`) -> `reports/<platform>_duplicates.csv` | harvested dup-GUID = 0; MO full rule run: 22,941 candidate pairs / 4,927 clusters | **Done (candidate-level)** - needs id/curator/gold-set confirm |
| **07** Decay | DAP unimplemented / dead links | `dap_implementation_audit.py` + independent dangling ids | DAP actions: 368 still unimplemented, 2 changed elsewhere, 42 cannot assess | **Done initial DAP implementation audit** |

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
  writes `reports/<platform>_duplicates.csv`. Current full-MO rule run writes
  **22,941 candidate duplicate pairs / 4,927 clusters**. **Caveat:** on the single-collector /
  single-locality Ceska-OH corpus many are weak `similar`-tier candidates (same
  taxon + site, sometimes different dates = distinct specimens). Treat as
  **review candidates**, not a final duplicate count, until there is either
  identifier evidence, a curated duplicate gold set, or a stronger cluster-level
  evaluation. Open design question: have `resolve()` emit clusters (with size)
  instead of only pairwise rows?
- **07 — initial DAP implementation audit done.** DAP's 2025 requested actions
  are now compared against the current local extracts. The strongest supported
  claim is that prior harmonization work largely remains unimplemented in the
  current BBM extract. Richer MO/GenBank comment/reference fields are still
  needed before every requested action can be assessed.

## Current major gaps / caveats before paper claims

- **Image / physical morphology evidence.** The paper's manual workflow used
  visual comparison between dried specimens and Mushroom Observer images. The
  automated repo pipeline does not implement image similarity; it uses metadata
  and identifiers only. Paper wording should treat image/physical morphology as
  part of the manual audit and as a reason full automation is limited, not as a
  measured automated feature.
- **Name drift / synonym searching (category 05).** Current matching normalizes
  names and flags mismatches, but it does not expand a name through accepted-name
  and synonym relationships. Plan: borrow the MDS API-pipeline approach from
  `/Users/wfrankel/Desktop/MDS/capstone/ubc-mds-project/scripts/apis_pipe/`, build
  a cached `data/name_synonyms.csv` from Index Fungorum, Mushroom Observer, and
  GBIF (`scripts/name_synonyms.py` is the first scaffold), then add
  `synonym_match` evidence to `resolve.py`. Keep genus blocking for now. A
  synonym match should help a candidate pass only when paired with date plus
  locality/collector evidence. Do not run full-corpus synonym crawling for paper
  iteration; use `--subset dap-name-drift --all` first because those are the DAP
  gold links where synonym evidence can be validated directly.
- **Automated lineage tracing across platforms.** Initial version now exists:
  `scripts/lineage_report.py` writes `reports/specimen_lineage_report.csv`.
  This is a join/action ledger over existing outputs, not a new matcher. It
  should be the basis for the paper's automated "digital fingerprint" table.
- **DAP / harmonization decay (category 07).** Initial implementation audit now
  exists. `scripts/dap_implementation_audit.py` compares DAP's 2025 requested
  actions against the latest local BBM/MO/GenBank extracts and writes
  `reports/dap_implementation_audit.csv` plus
  `reports/dap_implementation_summary.csv`. Current result: 412 action rows;
  368 still unimplemented, 2 changed elsewhere, 42 cannot be assessed from the
  normalized extracts, 0 implemented. Paper framing: DAP work largely remains
  unimplemented in the current extracts; some GenBank/MO comment-level actions
  require richer source fields before they can be judged.
- **Duplicate validation (category 06).** `mo_duplicates.csv` is a review queue,
  not a confirmed duplicate count. The available gold truth validates OH/Ceska
  MO->UBC links, not duplicate/non-duplicate labels. Paper language should say
  "duplicate candidates" unless a curated duplicate gold set is added.
- **Current-data caveat.** Latest completed fetch/audit wins. Because external
  platforms can change, paper numbers should cite the audit timestamp and source
  CSV timestamps, not imply a permanent count.

## Results writing readiness check

**Ready to write as quantitative Results now:**
- Full-collection scale-up from manual n=131 to **34,856** BBM fungal records in
  the current audit set.
- Goal 1 explicit-link representation table:
  MO 17 bidirectional / 2 BBM→MO only / 1,008 MO→BBM only / 2 wrong-id;
  MyCoPortal 34,633 present, 223 harvest gaps, 313 orphans, 0 duplicate GUIDs;
  GBIF 33,099 present, 1,757 harvest gaps, 1,779 orphans, 0 duplicate GUIDs;
  GenBank 1 bidirectional, 182 GenBank→UBC only, 0 UBC→GenBank only, 30 with no
  explicit cross-reference in current fields.
- Strong GenBank category-01 claim: **212 / 213** UBC voucher records do not cite
  their own GenBank accession, even though **183 / 213** fetched GenBank records
  cite the UBC voucher F#.
- DAP validation baseline for C2: rule matching recovers **261 / 355** gold
  MO→UBC links (73.5% recall), with **17** wrong-F# links, **93.9%** precision
  among linked records, and **77** unmatched gold records.
- DAP implementation/decay claim: **412** requested action rows; **368** still
  unimplemented, **2** changed elsewhere, **42** cannot assess from current
  normalized extracts, **0** clearly implemented.
- Unified lineage report: **36,948** rows total, **34,856** BBM specimen rows,
  **2,092** harvested-platform orphan rows, **6,480** rows with a recommended
  action; deterministic spot check passes **22 / 22** representative cases.

**Write with caveats, not as settled headline claims:**
- MO resolver output is a review/candidate system, not the Goal 1 representation
  table: **3,401** BBM/MO candidate pairs, with 16 bidirectional, 2 BBM→MO only,
  726 MO→BBM only, and 2,657 absent explicit links.
- Duplicate results are candidate-level: **22,941** same-platform MO candidate
  pairs forming **4,927** connected components. This is useful for triage but not
  a confirmed duplicate count.
- Category 04 is represented by the confidence rubric and low-confidence
  attribute-only candidates, but the default rule-based run does not emit accepted
  ambiguous/04 rows. Use category 04 mainly to explain why unresolved candidates
  need human/curatorial review.
- LLM results are experimental: prior full files show `rules+llm` improves recall
  by only one correct link over rules, while LLM-only is cleaner but lower recall.
  Keep the rule baseline as the paper-facing automation result unless new LLM
  experiments improve the tradeoff.

**Do before final submission, but not required before drafting Results:**
- Re-fetch live platform data and rerun `python scripts/run_audit.py`; report the
  final `generated_at_utc` timestamp from `reports/audit_summary.csv`.
- Complete and validate synonym-expanded matching before making strong category
  05 automation claims. The cache scaffold exists, but synonym evidence is not
  wired into `resolve.py` yet.
- If duplicate counts become important, curate a small duplicate/non-duplicate
  validation set or present duplicates strictly as review candidates.
- If image/physical morphology automation is mentioned, either implement it or
  explicitly frame it as out of scope for this repo and central to why curator
  judgment remains necessary.

## Validation against DAP ground truth (C2) — items 1 & 2

`scripts/validate_dap.py` runs `resolve.py` and scores MO->UBC matching against
the gold F#. **Rule-based baseline (OH, default scoped run): 261/355 = 73.5%
recovered, split 237 strict / 24 similar correct links, with 17 wrong-F# links
and 77 unmatched.** Precision among linked records is **93.9%**; wrong-link rate
is **6.1%**. Per-record output: `reports/dap_validation_rules.csv`; notebook §9.

LLM validation is now opt-in and auditable. `RUN_LLM_VALIDATION = False` in the
notebook by default; when enabled with `LLM_MODEL`, §9 compares `rules`,
`rules+llm`, and `force-llm` in `reports/dap_validation_summary.csv`. LLM calls
receive bounded candidate groups instead of whole genus blocks, then deterministic
guardrails reject incompatible exact dates/years, conflicting explicit catalog
refs, and weak-evidence links. Validation/report CSVs include `llm_reason`,
`guardrail`, candidate group size/ids, and the compared BBM/MO field values.
LLM-derived links are `review_required` unless they also pass the stricter
`LLM_ACCEPT_SCORE` threshold; keep LLM out of headline numbers if recall gains
come with too many wrong-F# links.

Latest validation files present:
- `rules`: 261/355 correct (73.5% recall), 17 wrong, 77 unmatched, 93.9%
  precision among linked, 6.1% wrong-link rate.
- `rules+llm`: 262/355 correct (73.8% recall), 17 wrong, 76 unmatched, 1 LLM
  correct, 1 review-required link. This is only a tiny improvement over rules.
- `llm` / force-LLM: 232/355 correct (65.4% recall), 4 wrong, 119 unmatched,
  98.3% precision among linked, 1.7% wrong-link rate, 6 review-required links.
  This suggests the LLM is more conservative and cleaner when used alone, but
  misses too many gold links to replace the rule baseline.

New diagnostics for improvement tracking:
- `reports/dap_validation_<mode>_wrong_links.csv` - side-by-side DAP gold BBM,
  matched BBM, and MO fields for wrong-F# links.
- `reports/dap_validation_<mode>_unmatched_reasons.csv` - unmatched gold links
  with deterministic failure reasons. Current rule baseline: 49 genus mismatch /
  blocking failures, 25 name-below-threshold failures, 3 exact date conflicts.

## The working harmonization pipeline (for the automation section)

- **`run_audit.py` — end-to-end paper workflow.** Default command is
  `python scripts/run_audit.py`: offline, rule-based, no LLM, no live API lookups.
  It regenerates DAP-derived ground truth, harvested-platform GUID audits, MO
  rule-based resolution, DAP validation diagnostics, GenBank linkage, DAP
  implementation status, the unified lineage report, lineage spot checks, and
  writes `reports/audit_summary.csv` + `reports/audit_manifest.json`. Optional flags:
  `--include-network` for live independent-platform link audits and
  `--include-llm --llm-review-limit N` for a capped DAP-unmatched review subset.
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
  locality/collector) via the vendored rule engine + optional bounded LLM tier,
  then scores matched pairs into the four quadrants. Tags 04 (ambiguous), 05
  (name mismatch), and 02 (platform cites a different catalog). Also returns
  same-platform duplicate pairs -> 06 (candidate-level;
  `reports/<platform>_duplicates.csv`). Main report rows now carry audit columns
  for LLM reason, guardrail result, candidate group size/ids, review status, and
  the compared fields.
- **`lineage_report.py` — automated lineage tracing.** Joins the current outputs
  into `reports/specimen_lineage_report.csv`: one row per BBM specimen plus
  harvested-platform orphan rows. Columns carry BBM identity fields, explicit MO
  link status, MO resolver candidates, MP/GBIF harvest status, GenBank accession
  status, breakdown categories, and recommended action. Use this as the automated
  digital-fingerprint/action ledger.
- **`dap_implementation_audit.py` — DAP decay/implementation audit.** Compares
  DAP requested actions to current local extracts. Action statuses:
  implemented / still_unimplemented / possibly_implemented_unprefixed /
  changed_elsewhere / cannot_assess_current_extract.
- **`spot_check_lineage.py` — lineage report consistency check.** Samples
  representative lineage rows and verifies status/category/action consistency.
  Latest run: 22/22 checks passed.
- **`name_synonyms.py` — synonym cache scaffold (not in default audit).** Fetches
  accepted-name/synonym relationships from Index Fungorum, Mushroom Observer, and
  GBIF into `data/name_synonyms.csv`. This is the planned input to category-05
  matching improvements. The no-argument command is deliberately only a small
  resumable smoke run: 25 names against Index Fungorum + MO. Use `--all` and
  opt into GBIF only for a deliberate full slow pass.
- **`harmonization.py` — the framework as code.** Seven categories, the 0–5
  confidence rubric (maps to Fig‑4), and `classify_breakdowns`, driven by explicit
  02 sub‑case signals.
- **`scripts/evaluators/` — vendored classify half** of the orchestration dedup
  pipeline, self‑contained.
