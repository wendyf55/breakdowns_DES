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
- **`link_audit.py`** — BBM → platform cross-reference audit. Platform-agnostic
  engine: establish correspondences (stored id or GUID) → classify bidirectional /
  unidirectional / dangling, with a breakdown category + 0–5 confidence per row.
  `audit_mo_links.py` is a thin MO-bound shim kept working for the notebook.
- **`resolve.py`** — cross-platform specimen resolution (`--platform`): shape BBM +
  platform records into the vendored evaluator contract, cluster same-specimen
  records (rule-based + optional LLM), and score the four quadrants (bidirectional /
  unidirectional UBC→platform / platform→UBC / absent) with categories + confidence.
- **`harmonization.py`** — the framework as code: the seven breakdown categories,
  the 0–5 confidence rubric, the report shape, and the LLM tier's guiding principles
  (from the `specimen-harmonization` skill / Kholmatova 2026).
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
`RecordObject` and the pure `get_matches` helper. `resolve.py` imports from here.
Only classify was taken — no search / workflow / merge. Cross-package imports were
rewritten package-local; each file names its upstream source. It is a snapshot —
re-sync deliberately if the upstream contract changes. Runtime dep: `requests`.

## Conventions

- Keep it simple and readable. Short functions, minimal abstraction.
- Constants at the top of each file.
- Don't over-engineer scoring or classification — build incrementally,
  verify each step before adding complexity.
- Filters (collector, locality) are configurable via `.env` so scripts
  work for any subset of the collection without code changes.
- Environment: Python 3.12 (conda env `breakdowns-des`, see `environment.yml`);
  deps: `requests`, `python-dotenv`, plus `pandas` + `jupyterlab` for the notebooks.
- **Never make git commits yourself** — leave commits to the maintainer.
- **Ask frequent questions to the maintainer regarding design, architecture,
  solutions, etc.**

## Related projects

- **Synonym lookup pipeline** (`ubc-mds-project/scripts/apis_pipe/`) — API
  clients for MO, GBIF, MyCoPortal, GenBank, etc. Used for taxonomic name
  expansion.
- **Specify dedup pipeline** (`orchestration/`) — LangGraph dedup system for
  Specify 7. Its search → classify → review architecture is the template for the
  entity resolution here; its evaluator subsystem (classify half only) is
  **vendored into `scripts/evaluators/`** so this repo needs no dependency on it.
- **specify-client** — Specify 7 REST client package. Auth, pagination,
  rate limiting.
