# Breakdowns in Realizing the Digital Extended Specimen — Extensions

The original paper audited 131 BBM fungal records across five platforms (BBM, MO,
MyCoPortal, GenBank, GBIF), identified seven recurring breakdown categories, and
produced a manual harmonization workflow (Figure 3). This project scales that work
from a sample to the full collection and begins automating the audit.

## Goals

### 1. Quantifying cross-platform representation

For the full BBM fungal collection, harmonization assessment:

- Bidrectional: we cite a foreign key or number used on a public database, and that database cites back to our record's internal ID

- Unidirection: our database contains the id number or link of a public record on a database, but that record does not point back to us
  ---- OR ----
  an online record points to our database, and gives an id number we have in the database, but in BBM system, we don't list the public record anywhere

- Absent - we dont cite the public record, the public record doesnt site us. same exact specimen or sighting, but not connected in either way to each other.

- MO: we list the MO number in our database as MO # 66139, Mushroom Observer observation #254611, "MO posted as Leucopaxillus gentianeus (Qu�l.) Kotl." (so there is a record associated, but no MO number).
- once these references were gathered, I used that to query the MO database and find the associated record, which answers bidrectional, and unidrectional UBC --> MO

### 2. Quantifying Ceska / Observatory hill completeness in our database

- **How many MO records are likely ours, but are not connected?** Query MO for observations by known BBM
  collectors (the Ceskas), filter by locality (Observatory
  Hill, Victoria BC), and find:
  - total number of records with these filters on MO
  - our number of records with these filters
  - how many records have a bidirectional relationship
  - how many records have unidrectional relationship, going either direction
  
  estimate:
  - how many MO records likely correspond to BBM holdings - equal to Ceska OH records? or more filters needed?

### 2. Entity resolution pipeline

Build a system that can determine whether two records from the same or different
platforms refer to the same physical specimen.

- **Cross-platform specimen matching.** Given a specimen record, generate candidate
  matches on other platforms by searching on scientific name (NOT expanded to all known
  synonyms via the synonym pipeline), collector, collection date, and locality.
  Score each candidate on the strength of the match across these fields plus
  identifier cross-references and (where available) image similarity. Flag
  ambiguous cases for human review.

  This directly automates the paper's matching procedure (A10 / Figure 3): "filter
  occurrence searches based on scientific name, locality, and collection date" to
  produce a shortlist, then compare identifiers, collection information.

- **Automated lineage audit.** Given a specimen record (or a matched pair), trace
  its full lineage across BBM -> MP -> GBIF -> MO -> GenBank and check every
  cross-reference link (not in place yet; audits occur separately)
  - Is the MO ID present on the UBC record? Does it point to the right MO record?
  - Is the catalog number present on the MO record? Is it correct?
  - Does the GBIF record carry the correct occurrenceID / catalog number?
  - Does the GenBank accession link back to the voucher specimen?

  Assign a harmonization quality score (the paper's 0-5 scale) and output a report.

## Vendored from the Specify dedup pipeline (`scripts/evaluators/`)

Rather than depend on the orchestration repo at runtime, the **classify half** of
its evaluator subsystem is copied into `scripts/evaluators/` and used by
`resolve.py` for cross-platform record resolution. What was taken:

- `rule_based.py` — `RuleBasedEvaluator`, the first-match-wins ordered-predicate
  engine; drives the confident matches (name + date + locality/collector overlap).
- `llm.py` + `llm_client.py` + `prompts.py` — `LLMEvaluator` and its
  OpenAI/Ollama-compatible client; adjudicates the ambiguous middle when an LLM
  endpoint is configured (`LLM_MODEL` etc. in `.env`).
- `base.py`, `types.py` — the `BaseEvaluator` interface and the `MatchRule` /
  `EvaluationConfig` / `MatchContext` types the engine consumes.
- `record.py` (`RecordObject`) and `equality.py` (`get_matches`) — the record
  model and the pure matching helper the rule engine reaches through.

What was **not** vendored: candidate generation (replaced by genus-blocking in
`resolve.py`), the LangGraph workflow engine, the RabbitMQ review queue, and all
merge code (Specify-schema-bound and irreversible). Cross-package imports were
rewritten package-local; each file names its upstream source. The copy is a
snapshot — re-sync deliberately if the upstream evaluator contract changes.
Runtime dependency: `requests` only.

## Platforms (`scripts/platforms.py`)

Each external database the paper touches is one `Platform`, organized by **coupling**
(which decides the matching method). Audit, discovery, and resolution all consume
these — a new database is one subclass, no new script.

| Platform | Class | Coupling | Matched by |
| --- | --- | --- | --- |
| Mushroom Observer | `MushroomObserver` | independent | the MO id BBM stored (`MO # 82752`) |
| GenBank | `GenBank` | independent | a cited accession; reverse-linked via `specimen_voucher` |
| MyCoPortal | `MyCoPortal` | harvested | our GUID (`occurrenceID`) |
| GBIF | `GBIF` | harvested | our GUID (`occurrenceID`), via the UBC Fungi dataset |

Independent platforms are upstream/separately maintained (BBM stores *their* id;
they cite us only in free text). Harvested platforms are downstream of BBM
(Symbiota/GBIF harvest our Specify records, so they carry our GUID + catalog).

## Running the pipeline

All commands run from the repo root on a machine with the `.env` credentials and
network access. Fetches hit live APIs; the notebook writes up the numbers.

### One-time setup

```bash
conda env create -f environment.yml        # creates the "breakdowns-des" env
conda activate breakdowns-des
python -m ipykernel install --user --name breakdowns-des
```

`.env` holds the Specify creds and MO discovery seeds (see `.env.example`).
Leaving `FILTER_COLLECTORS` / `FILTER_LOCALITY` blank fetches the **whole**
fungal collection; set them (e.g. `Ceska` / `Observatory Hill`) for the pilot
subset. Set `LLM_MODEL` to enable the resolver's LLM tier.

### 1 — Fetch data (network → `data/`)

```bash
python scripts/get_bbm_records.py                 # our side  → data/bbm_records.csv
python scripts/get_records.py --platform mo        #           → data/mo_records.csv
python scripts/get_records.py --platform mycoportal
python scripts/get_records.py --platform gbif      # heavy: ~35k occurrences
python scripts/get_records.py --platform genbank   # sparse: voucher search
```

Add `--limit N` to any `get_records.py` call for a quick smoke test.

### 2 — Findings notebook (regenerates every number)

```bash
jupyter lab reports/harmonization_findings.ipynb
```

Select the **breakdowns-des** kernel, then Run All. Cells are self-locating, so
the launch directory does not matter. Network cells: §1 MO audit, §3 resolution,
§6 GenBank. Offline cells (read the CSVs): §2 MyCoPortal, §4 comparison, §5 GBIF.

### Or run the pieces from the CLI

```bash
python scripts/link_audit.py --platform mo         # cross-ref audit → counts
python scripts/link_audit.py --platform genbank
python scripts/resolve.py    --platform mo         # attribute resolution → mo_resolution.csv
python scripts/resolve.py    --platform mo --no-llm  # rule-based only
```

`resolve.py` needs `data/bbm_records.csv` and `data/<platform>_records.csv`
present first. `link_audit.py --probe <ID>` checks a single id end-to-end.

**Typical loop:** run step 1 once to refresh the CSVs, then step 2 to regenerate
all the numbers.
