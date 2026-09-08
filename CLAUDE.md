# BREAKDOWNS DES

Extension of Kholmatova (2026) — programmatic tools for quantifying
cross-platform specimen representation and auditing harmonization quality
across biodiversity databases.

## What it does

Quantifies BBM ↔ external-platform cross-references and resolves records across
platforms, labeling every output with the harmonization framework (breakdown
categories + 0–5 confidence). Scripts in `scripts/`:

- **`platforms.py`** — one `Platform` per external database, the axis everything
  turns on. `IndependentPlatform` (matched by the id BBM stored: `MushroomObserver`,
  `GenBank`) vs `HarvestedPlatform` (matched by our GUID, since these harvest our
  records: `MyCoPortal`, `GBIF`). Each concrete class defines its id patterns / API
  / reverse-cite / record→fields mapping in one place; audit, discovery, and
  resolution all consume it. A new database = one subclass.
- **`get_bbm_records.py`** — fetch fungal collection objects from Specify 7 and
  join CO → collecting-event → locality, determination → taxon, collector → agent
  into `data/bbm_records.csv` (CO text fields + name/collector/date/locality).
- **`get_records.py`** — generic discovery fetch: `PlatformRecords` drives any
  platform's `fetch_ours()` → `to_common()` → `data/<platform>_records.csv`.
  `get_mo_records.py` is a thin shim over `PlatformRecords(MushroomObserver())`.
- **`run_audit.py`** — end-to-end paper workflow. Default mode is offline,
  rule-based, no LLM, and no live API lookups; it writes
  `reports/audit_summary.csv`, `reports/audit_manifest.json`,
  `reports/specimen_lineage_report.csv`, the DAP implementation audit, and
  lineage spot checks.
- **`link_audit.py`** — BBM → platform cross-reference audit. Platform-agnostic
  engine: establish correspondences (stored id or GUID) → classify bidirectional /
  unidirectional / dangling, with a breakdown category + 0–5 confidence per row.
  `audit_mo_links.py` is a thin MO-bound shim kept working for the notebook.
- **`resolve.py`** — cross-platform specimen resolution (`--platform`): shape BBM +
  platform records into the vendored evaluator contract, cluster same-specimen
  records (rule-based + optional bounded LLM), and score the four quadrants
  (bidirectional / unidirectional UBC→platform / platform→UBC / absent) with
  categories + confidence. Report rows include audit columns for LLM reason,
  guardrail result, candidate group size/ids, review status, and compared fields.
- **`lineage_report.py`** — unified cross-platform lineage/action report. Joins
  existing outputs for BBM, MO explicit links, MO resolver candidates,
  MyCoPortal/GBIF GUID coverage, and GenBank linkage. This is a reporting layer,
  not a new matcher.
- **`dap_implementation_audit.py`** — DAP implementation/decay audit. Compares
  2025 DAP requested actions against the latest local BBM, MO, and GenBank CSVs
  and writes action-level status rows plus a summary.
- **`spot_check_lineage.py`** — deterministic consistency spot check for the
  lineage report. This checks reporting logic, not biological correctness.
- **`name_synonyms.py`** — networked synonym-cache scaffold for category 05.
  Fetches accepted-name/synonym relationships from Index Fungorum, Mushroom
  Observer, and GBIF into `data/name_synonyms.csv`. Keep it outside the default
  offline audit until synonym-expanded matching is validated. The no-argument
  command is a small resumable smoke run only; use `--all --sources
  indexfungorum,mo,gbif` for the deliberate full slow pass.
- **`harmonization.py`** — the framework as code: the seven breakdown categories,
  the 0–5 confidence rubric, the report shape, and the LLM tier's guiding
  principles (from the `specimen-harmonization` skill / Kholmatova 2026).
- **`base_get_records.py`** — `BaseGetRecords`, the shared fetch → filter → save
  pipeline the fetchers subclass. `config.py` loads `.env`.

Findings are written up in `reports/harmonization_findings.ipynb` (with the MO
deep-dive in `reports/mo_crossref_audit.ipynb`).

## Repository layout

- `scripts/` — pipeline scripts. `config.py` loads `.env` settings.
- `scripts/evaluators/` — vendored evaluator subsystem (see below).
- `data/` — output CSVs (gitignored).
- `reports/` — analysis outputs (notebooks, figures, summaries).
- `environment.yml` — conda env `breakdowns-des`.

## Vendored evaluators (`scripts/evaluators/`)

The **classify half** of the orchestration dedup pipeline's evaluator subsystem,
copied in so this repo is self-contained (no dependency on that repo's path or
install): `RuleBasedEvaluator`, `LLMEvaluator` + client + prompts, the
`BaseEvaluator` interface and `MatchRule` / `EvaluationConfig` types, plus
`RecordObject` and the pure `get_matches` helper. `resolve.py` imports from here
but now bounds LLM inputs before calling the evaluator and applies deterministic
guardrails after it. Only classify was taken — no search / workflow / merge.
Cross-package imports were rewritten package-local; each file names its upstream
source. It is a snapshot — re-sync deliberately if the upstream contract changes.
Runtime dep: `requests`.

## Conventions

- Keep it simple and readable. Short functions, minimal abstraction.
- Constants at the top of each file.
- Don't over-engineer scoring or classification — build incrementally,
  verify each step before adding complexity.
- Goal 1 numbers are explicit identifier-link counts from the latest fetched
  data. Do not substitute `mo_resolution.csv` candidate-pair quadrants for the
  paper-facing representation table.
- Treat `mo_duplicates.csv` and same-platform duplicate rows as review
  candidates unless a duplicate/non-duplicate gold set exists.
- For name/taxonomic drift, add a cached synonym/accepted-name layer before
  relaxing the resolver. Keep genus blocking until the DAP validation shows the
  synonym layer improves recall without increasing wrong-F# links.
- Filters (collector, locality) are configurable via `.env` so scripts
  work for any subset of the collection without code changes.
- Environment: Python 3.12 (conda env `breakdowns-des`, see `environment.yml`);
  deps: `requests`, `python-dotenv`, plus `pandas` + `jupyterlab` for the notebooks.
- **Never make git commits yourself** — leave commits to the maintainer.
- **Ask frequent questions to the maintainer regarding design, architecture,
  solutions, etc.**

## Related projects

- **Synonym lookup pipeline** (`ubc-mds-project/scripts/apis_pipe/`) — API
  clients for MO, GBIF, MyCoPortal, GenBank, Index Fungorum, etc. Use the idea
  and source-specific clients for taxonomic name expansion, but keep this repo's
  runtime self-contained by copying/adapting only the minimal code needed.
- **Specify dedup pipeline** (`orchestration/`) — LangGraph dedup system for
  Specify 7. Its search → classify → review architecture is the template for the
  entity resolution here; its evaluator subsystem (classify half only) is
  **vendored into `scripts/evaluators/`** so this repo needs no dependency on it.
- **specify-client** — Specify 7 REST client package. Auth, pagination,
  rate limiting.
