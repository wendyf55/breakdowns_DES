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

- **Extend beyond fungi.** Run the same analysis for other BBM taxa (lichens,
  bryophytes, algae) against other databases.

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
  matches on other platforms by searching on scientific name (expanded to all known
  synonyms via the synonym pipeline), collector, collection date, and locality.
  Score each candidate on the strength of the match across these fields plus
  identifier cross-references and (where available) image similarity. Flag
  ambiguous cases for human review.

  This directly automates the paper's matching procedure (A10 / Figure 3): "filter
  occurrence searches based on scientific name, locality, and collection date" to
  produce a shortlist, then compare identifiers, collection information, and
  photographs.

  Architecture draws from the Specify dedup pipeline (search -> classify -> human
  review -> act): candidate generation produces groups, a rule-based evaluator
  scores confidence, high-confidence matches are linked automatically,
  low-confidence matches are queued for curatorial review.

- **Automated lineage audit.** Given a specimen record (or a matched pair), trace
  its full lineage across BBM -> MP -> GBIF -> MO -> GenBank and check every
  cross-reference link:
  - Is the MO ID present on the UBC record? Does it point to the right MO record?
  - Is the catalog number present on the MO record? Is it correct?
  - Does the GBIF record carry the correct occurrenceID / catalog number?
  - Does the GenBank accession link back to the voucher specimen?
  - Are any links broken (dead URLs, re-minted identifiers)?

  Assign a harmonization quality score (the paper's 0-5 scale) and output a report
  of what needs fixing. This audit should be re-runnable to detect harmonization
  decay (breakdown category 07 — links that were once correct but broke over time).

## Related assets

- **Synonym lookup pipeline** — API clients for GBIF, MO, MyCoPortal (Symbiota),
  GenBank, ITIS, Tropicos, Index Fungorum, Catalogue of Life, FishBase. Used to
  expand taxonomic names to all known synonyms before cross-platform searching, so
  matches aren't missed due to nomenclature differences (breakdown category 05).

- **Specify dedup pipeline** — LangGraph-based deduplication system for Specify 7
  databases (locality, geography, agent, taxon tables). Its search -> classify ->
  human review -> merge architecture is the template for the entity resolution
  pipeline above.
