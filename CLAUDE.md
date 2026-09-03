# BREAKDOWNS DES

Extension of Kholmatova (2026) — programmatic tools for quantifying
cross-platform specimen representation and auditing harmonization quality
across biodiversity databases.

## What it does

Two-step pipeline:

1. **Fetch** (`scripts/fetch_bbm_specimens.py`) — pulls fungal specimen records
   from Specify 7, resolves collector/taxon/locality/date through the API's
   nested JSON, exports a flat CSV to `data/`.

2. **Search** (`scripts/search_mo.py`) — reads that CSV, checks which BBM
   records carry a Mushroom Observer ID (cross-reference presence), and queries
   MO to find candidate matches for records that lack one.

## Repository layout

- `scripts/` — pipeline scripts. `config.py` loads `.env` settings.
- `data/` — output CSVs (gitignored).
- `reports/` — analysis outputs (notebooks, figures, summaries).
- `notebooks/` — Jupyter notebooks for exploration and analysis.

## Conventions

- Keep it simple and readable. Short functions, minimal abstraction.
- Constants at the top of each file.
- Don't over-engineer scoring or classification — build incrementally,
  verify each step before adding complexity.
- Filters (collector, locality) are configurable via `.env` so scripts
  work for any subset of the collection without code changes.
- Environment: Python 3.12; deps: `requests`, `python-dotenv`.
- **Never make git commits yourself** — leave commits to the maintainer.
- **Ask frequent questions to the maintainer regarding design, architecture,
  solutions, etc.**

## Related projects

- **Synonym lookup pipeline** (`ubc-mds-project/scripts/apis_pipe/`) — API
  clients for MO, GBIF, MyCoPortal, GenBank, etc. Used for taxonomic name
  expansion.
- **Specify dedup pipeline** (`orchestration/`) — LangGraph dedup system for
  Specify 7. Its search → classify → review architecture is the template
  for future entity resolution work here.
- **specify-client** — Specify 7 REST client package. Auth, pagination,
  rate limiting.
