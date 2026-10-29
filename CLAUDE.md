# BREAKDOWNS DES

Extension of Kholmatova (2026) — programmatic tools for quantifying
cross-platform specimen representation and auditing harmonization quality
across biodiversity databases.

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
