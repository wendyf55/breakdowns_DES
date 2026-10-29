# Breakdowns in the Digital Extended Specimen — cross-platform link census

Descriptive statistics for the CHI 2027 revision: how often UBC Beaty Biodiversity
Museum records and records on other platforms point at each other, in which
direction, and how good those pointers are.

## Data

- **UBC side:** the full GBIF export for publisher `b542788f-0dc2-4a2b-b652-fceced449591`
  (Beaty Biodiversity Museum, all collections). Use an official GBIF download
  (Darwin Core Archive) so the snapshot has a DOI. Store it untouched in `data/raw/gbif/`.
- **Platforms (start):** Mushroom Observer, GenBank, iNaturalist.
  GBIF and MyCoPortal *harvest* UBC records, so they are not counted as independent
  link partners; report them separately if at all.
- **Snapshot:** every platform is fetched within one fixed window. Every raw API
  response is cached under `data/cache/<platform>/`, so reruns are offline and auditable.
- **Legacy (read-only):** `data/legacy_2026-09/` holds the September 2026 export of the
  museum's database (Specify), which we no longer have access to, plus the DAP and GenBank ground
  truth. It is the baseline for checking what does or doesn't survive into GBIF.
  `data2_nongenerated/` holds the original hand-made MO/DAP datasets.

## Questions

1. **UBC → platform:** how many UBC records point to a record on platform X?
2. **Point-back:** for those, does the platform record point back to UBC?
3. **Platform → UBC:** how many platform records point to UBC (found by searching the platform)?

Steps 2 and 3 should find the same bidirectional set from opposite ends, which serves as a consistency check.

## Definitions

**Unit:** UBC records (for questions 1–2) and platform records (for question 3). A record with several
links takes its best tier per platform. Broken and incorrect links are counted separately,
so a good link elsewhere never hides them.

**Direction** (paper §4.1): bidirectional · unidirectional UBC→X · unidirectional X→UBC · absent.

**Link tiers** (per pointer, either direction; built from §4.1 and HB2):

| Tier | Meaning |
|---|---|
| 1 explicit | Attributed ID/URL in a proper field; resolves to an existing record |
| 2 hanging / wrong field | Resolves, but bare number without attribution, or buried in free text (HB2) |
| 3 mention only | Refers to the platform/UBC with no identifier ("posted to MO", "voucher at UBC") |
| broken | Identifier given but target does not exist / link dead |
| incorrect | Resolves, but to a different specimen (other catalog number or taxon) — paper's *incorrect* flag |

**Point-back tiers** (question 2):

| Tier | Meaning |
|---|---|
| exact | Cites this UBC record's catalog number / occurrence ID |
| institution only | Cites UBC but no specific record |
| wrong record | Cites a different UBC catalog number |
| none | Does not cite UBC |

The 0–5 match-confidence score from the earlier pipeline is for matching records with no link
between them (a later step), not for these descriptive counts.

## History

The previous pipeline (entity resolution, DAP validation, reviewer bundle) was removed
on 2026-10-29. The tracked code remains in git history (`git log`, last commit `f78e2a6`).
