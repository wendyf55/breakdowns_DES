# Pipeline Guide

This repo is a research/audit pipeline for the Digital Extended Specimen work.
It is easiest to understand if you separate three things:

- fetching source records into `data/`
- running the reproducible audit into `reports/`
- inspecting large diagnostic CSVs and notebooks

The final paper-facing workflow is mostly automated by `scripts/run_audit.py`,
but a fresh final run still needs one fetch pass first.

## Quick Start

Run commands from the repo root.

```bash
conda env create -f environment.yml
conda activate breakdowns-des
```

Create `.env` from `.env.example` and fill in Specify credentials plus any MO
discovery settings.

For a CLI-only run, Jupyter kernel registration is optional. Use it only if you
will open notebooks:

```bash
python -m ipykernel install --user --name breakdowns-des
```

## Final Run

Use this when refreshing all source data and regenerating the analysis.

```bash
python scripts/build_dap_ground_truth.py
python scripts/get_bbm_records.py

for p in mo mycoportal gbif genbank; do
  python scripts/get_records.py --platform "$p"
done

python scripts/run_audit.py --include-network
```

This produces the paper-safe rule-based outputs. It avoids the experimental LLM
tier.

Then open `reports/harmonization_findings.ipynb` and run the notebook. The first
summary sections read the generated CSVs and present the important numbers by
breakdown category.

To add a capped LLM review subset:

```bash
python scripts/run_audit.py --include-network --include-llm --llm-review-limit 10
```

The LLM run requires `LLM_MODEL` in `.env` and a compatible local/model backend.
The LLM path is experimental and should not replace the rule-based headline
numbers unless it is revalidated.

## What `run_audit.py` Does

`scripts/run_audit.py` is the analysis runner. It assumes the latest source CSVs
already exist under `data/`.

By default it:

- rebuilds DAP-derived ground truth when raw DAP exports are present
- audits MyCoPortal and GBIF GUID coverage
- runs MO rule-based specimen resolution
- validates MO resolution against DAP ground truth
- audits GenBank linkage
- audits DAP requested-action implementation/decay
- builds the unified lineage/action report
- runs deterministic lineage spot checks
- writes `reports/audit_summary.csv` and `reports/audit_manifest.json`

`--include-network` also runs live independent-platform link audits.

`--include-llm` runs only a capped DAP-unmatched review subset, not the whole
corpus.

## Important Scope Distinction

Do not mix up these two outputs:

- `link_audit.py`: explicit identifier-link counts. This is the Goal 1,
  paper-facing representation view.
- `resolve.py`: inferred same-specimen candidate matching from name, date,
  locality, collector, identifier signals, and optional LLM review. This is the
  Goal 2 resolver/candidate view.

`mo_resolution.csv` is useful evidence for review and matching quality. It should
not be substituted for the explicit-link representation table.

## Filters

`FILTER_COLLECTORS` and `FILTER_LOCALITY` in `.env` apply to BBM fetching through
`scripts/get_bbm_records.py`.

If both are set, they use AND semantics:

```env
FILTER_COLLECTORS=Ceska
FILTER_LOCALITY=Observatory Hill
```

means fetched BBM rows must match both collector and locality.

Leaving both blank fetches the whole fungal collection.

External platform fetches are not all filtered the same way:

- MO discovery is controlled separately by `MO_USER` and `MO_LOCATION`.
- MyCoPortal and GBIF fetch harvested datasets and are reconciled by BBM GUID.
- GenBank is seeded from `data/genbank_ground_truth.csv` when available.

Duplicate outputs only reflect the records loaded into the current CSVs. A full
BBM fetch can produce broader duplicate candidates, but these remain review
candidates unless validated.

## Core Scripts

These are part of the main pipeline or shared runtime.

- `scripts/run_audit.py`: end-to-end analysis runner after data are fetched
- `scripts/get_bbm_records.py`: fetch BBM Specify records into `data/bbm_records.csv`
- `scripts/get_records.py`: fetch one external platform into `data/<platform>_records.csv`
- `scripts/platforms.py`: external database adapters and matching contracts
- `scripts/link_audit.py`: explicit cross-reference audit
- `scripts/guid_discovery.py`: harvested-platform GUID reconciliation
- `scripts/resolve.py`: metadata-based specimen resolution and duplicate candidates
- `scripts/validate_dap.py`: validation against DAP MO-to-UBC ground truth
- `scripts/genbank_audit.py`: GenBank accession/voucher linkage audit
- `scripts/dap_implementation_audit.py`: DAP requested-action implementation audit
- `scripts/lineage_report.py`: unified cross-platform lineage/action report
- `scripts/spot_check_lineage.py`: consistency checks for lineage report rows
- `scripts/build_dap_ground_truth.py`: normalize raw DAP exports into validation inputs
- `scripts/harmonization.py`: seven categories and confidence rubric
- `scripts/base_get_records.py`: shared fetch/filter/save template
- `scripts/config.py`: `.env` and path configuration
- `scripts/evaluators/`: vendored rule-based and LLM evaluator subsystem

## Optional Or Experimental Scripts

- `scripts/name_synonyms.py`: builds `data/name_synonyms.csv` for category 05
  name-drift evidence. It is not part of the default offline audit.

The synonym cache is resumable. Existing `(query_name, source)` pairs in
`data/name_synonyms.csv` are skipped unless `--refresh` is set, so if a run is
interrupted, rerun the same command.

Use the targeted DAP name-drift diagnostic first:

```bash
python scripts/name_synonyms.py --subset dap-name-drift --all --dry-run
python scripts/name_synonyms.py --subset dap-name-drift --all --sources indexfungorum,mo
```

This queries names from DAP gold links that the rule matcher missed for
name/genus-related reasons.

For a broader paper-facing diagnostic, include a small correct-control set:

```bash
python scripts/name_synonyms.py --subset paper-name-drift --all --dry-run
python scripts/name_synonyms.py --subset paper-name-drift --all --sources indexfungorum,mo
```

Avoid full-corpus GBIF crawling unless it is intentional and time is available:

```bash
python scripts/name_synonyms.py --all --sources indexfungorum,mo,gbif
```

After updating `data/name_synonyms.csv`, rerun DAP validation:

```bash
python scripts/validate_dap.py --no-llm
```

Then inspect:

- `reports/dap_validation_rules.csv`
- `reports/dap_validation_rules_unmatched_reasons.csv`
- `reports/dap_validation_rules_unmatched_reason_counts.csv`
- `reports/dap_validation_rules_wrong_links.csv`

Look for whether synonym-supported matches increased, whether they were correct,
and whether unmatched reasons shifted away from `genus_mismatch_blocks_match` or
`name_below_rule_threshold`.

Current framing: synonym/name-drift support is implemented as category 05
supporting evidence. It is not a stand-alone matcher. A synonym match still needs
date evidence plus locality or collector context before `resolve.py` accepts the
candidate.

## Legacy Or Development Helpers

These are useful, but not central to the final pipeline:

- `scripts/probe_ce_fields.py`: one-off Specify field/schema probe
- `scripts/get_mo_records.py`: thin wrapper around `get_records.py --platform mo`
- `scripts/audit_mo_links.py`: compatibility shim for the older MO notebook

Do not treat these as required steps for a final analysis run.

## Reports To Read First

Start here:

- `reports/audit_summary.csv`: compact metric/value summary
- `reports/audit_manifest.json`: commands, durations, outputs, provenance
- `reports/specimen_lineage_report.csv`: unified lineage/action ledger
- `reports/dap_implementation_audit.csv`: requested-action status rows
- `reports/dap_validation_summary.csv`: validation summary when multiple modes exist
- `reports/harmonization_findings.ipynb`: notebook narrative and tables

## Diagnostic Reports

These are large or detailed. They are useful for tracing and QA, but not pleasant
to read casually.

- `reports/mycoportal_guid_discovery.csv`
- `reports/gbif_guid_discovery.csv`
- `reports/mo_resolution.csv`
- `reports/mo_duplicates.csv`
- `reports/genbank_linkage.csv`
- `reports/dap_validation_*_wrong_links.csv`
- `reports/dap_validation_*_unmatched_reasons.csv`
- `reports/lineage_spot_check.csv`

## Current Caveats

- Image and physical morphology matching are not automated.
- The repo recommends actions but does not write fixes back to live databases.
- Synonym/name-drift support is supporting evidence, not a stand-alone matcher.
- MO duplicate output is candidate-level, not a confirmed duplicate count.
- LLM output is experimental and should be cited only after rerunning and validating.
- Some DAP action rows cannot be assessed from the normalized extracts.
- External platforms change; final paper numbers should come from a fresh fetch and
  cite the timestamp in `reports/audit_summary.csv`.
