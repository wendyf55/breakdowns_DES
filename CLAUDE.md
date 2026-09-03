# BREAKDOWNS DES

Extension of Kholmatova (2026) — programmatic tools for quantifying
cross-platform specimen representation and auditing harmonization quality
across biodiversity databases.

## What it does

Quantifies BBM ↔ external-platform cross-references and resolves records across
platforms. Scripts in `scripts/`:

- **`get_bbm_records.py`** — fetch fungal collection objects from Specify 7 and
  join CO → collecting-event → locality, CO → current determination → taxon, and
  CE → collector → agent into `data/bbm_records.csv` (all CO text fields plus
  name / collector / date / locality).
- **`get_mo_records.py`** — discovery fetch: the union of Mushroom Observer
  observations by the Ceska account (`MO_USER`) and the Observatory Hill location
  (`MO_LOCATION`), flagging whether each cites a UBC herbarium number back.
- **`link_audit.py`** — BBM → external-platform cross-reference audit. One engine
  (`scan → lookup → classify`) with a `LinkProvider` per platform
  (`MushroomObserver`, `MyCoPortal`); classifies each link bidirectional /
  unidirectional / dangling. `audit_mo_links.py` is a thin MO-bound shim kept
  working for the notebook.
- **`resolve.py`** — cross-platform specimen resolution: shape BBM + MO records
  into the vendored evaluator contract, cluster same-specimen records (rule-based
  + optional LLM), and score the four harmonization quadrants (bidirectional /
  unidirectional UBC→MO / unidirectional MO→UBC / absent).
- **`base_get_records.py`** — `BaseGetRecords`, the shared fetch → filter → save
  pipeline the `get_*` fetchers subclass. `config.py` loads `.env`.

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
