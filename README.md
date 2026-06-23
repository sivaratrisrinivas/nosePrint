# NosePrint

NosePrint is a local-first fragrance discovery project. Its goal is to help
someone start with a Fragrance they already know, or describe the kind of scent
they want, and find nearby Fragrance Editions using clear, traceable scent
facts.

## What

NosePrint separates three ideas that are easy to blur:

- **Fragrance**: the named scent product people recognize.
- **Fragrance Edition**: a specific concentration or release of that Fragrance,
  such as EDT, EDP, Parfum, or Extrait.
- **Scent Profile**: the scent-only facts used for comparison: main accords,
  note pyramid, and scent family.

The current application is a Python command-line workflow backed by SQLite. It
can audit a candidate data source, import accepted Real Catalog records, browse
Fragrance Editions by Fragrance name, inspect a selected Scent Profile, and find
exact-cosine Scent Matches for a selected Fragrance Edition.

SQLite is the catalog source of truth. Future search indexes, including vector
search, should be rebuildable helpers rather than competing master copies.

## Why

Fragrance recommendation can look confident while quietly mixing trustworthy
facts, unclear source material, marketing language, and generated test data.
NosePrint is built to keep those things separate.

Before fragrance information can enter the Real Catalog automatically, the
project checks:

- where the information came from;
- who published it;
- whether reuse is clearly allowed;
- whether the file has the expected columns and number of rows;
- whether missing, repeated, or broken rows are handled honestly.

The proposed 2,191-row Perfume Recommendation Dataset currently has an
**inconclusive** result. Its page says the data is public domain, but it does not
show where all descriptions, notes, and image links originally came from or
whether the publisher could give reuse permission for them. NosePrint therefore
blocks automatic import by default. For this personal side project, the owner can
explicitly accept that risk without pretending the audit passed.

Read the full [audit result](docs/audits/perfume-recommendation-dataset-v1.md).

## How

NosePrint currently needs only Python 3; there are no extra packages to install.
Run the test suite with:

```bash
python3 -m unittest discover -v
```

The catalog workflow has seven commands:

1. `audit` checks a candidate Real Catalog source and writes a report.
2. `import` accepts only the exact file that received a passing report, unless
   the owner explicitly accepts an inconclusive risk.
3. `inspect` shows accepted Real Catalog records and where they came from.
4. `inspect-quarantine` shows rows kept out of the catalog and explains why.
5. `browse` searches Real Catalog Fragrance Editions by Fragrance name.
6. `scent-profile` shows the selected Fragrance Edition's Scent Profile.
7. `scent-matches` returns ranked exact-cosine Scent Matches from the Real
   Catalog with calibrated labels and factual Profile Comparisons.

See [how to run the catalog workflow](docs/catalog-import.md) for complete
commands.

### Example browsing flow

After import, search the Real Catalog by Fragrance name:

```bash
python3 -m noseprint.catalog browse \
  --database var/noseprint.sqlite3 \
  --query "rose"
```

Use a returned `fragrance_edition_id` to inspect the Scent Profile:

```bash
python3 -m noseprint.catalog scent-profile \
  --database var/noseprint.sqlite3 \
  --edition-id 1
```

The Scent Profile output includes main accords, note pyramid, and scent family.
Brand, price, bottle size, and marketing copy are not part of that comparison
profile. Missing scent facts are shown as `unknown` instead of being guessed.

Find exact Scent Matches for that same selected edition:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10
```

This path deterministically serializes each Real Catalog Scent Profile, creates a
versioned 384-number local embedding, compares vectors with exact cosine
similarity, and hydrates the final Fragrance Edition details from SQLite.
Each result includes a calibrated strength label, a model-specific exact-cosine
score, and a factual Profile Comparison. Scale-Test Catalog rows are excluded
from this shopper workflow.

## Current Guarantees

- Inconclusive catalog audits are blocked unless the owner explicitly accepts
  the risk.
- The accepted Real Catalog is stored in SQLite with source traceability.
- Imports are idempotent and do not duplicate existing Fragrance Editions.
- Malformed rows are rejected, missing note rows are quarantined, and duplicate
  Fragrance Editions are skipped deterministically.
- Shopping browse results only include Real Catalog records, not Scale-Test
  Catalog records.
- Missing scent facts are displayed as `unknown`; NosePrint does not guess them.
- Exact Scent Matches use Scent Profile facts only. Brand, price, bottle size,
  marketing copy, and Wear Profile facts do not alter the model-specific score
  or ranking.
- Scent Match scores are model-specific exact-cosine scores, not probabilities,
  percent-identical claims, or promises about how someone will perceive a
  Fragrance Edition on skin.
- Profile Comparisons report shared, reference-only, and candidate-only main
  accords, note-pyramid facts, and scent family facts from SQLite. Missing facts
  stay `unknown`.
- SQLite records versioned 384-number Scent Profile embeddings so future search
  indexes can be rebuilt without becoming the catalog source of truth.

## Learning Notes

This repository also contains local learning material created while building the
project. `MISSION.md`, `RESOURCES.md`, `NOTES.md`, `lessons/`, `reference/`, and
`learning-sessions/` capture teaching context and explanations for the project
owner. Product code should remain usable without reading those learning files.

## Direction

The next project step is using the truthful SQLite Real Catalog and exact-cosine
baseline for later vector-search experiments. The important rule remains the
same: search can get faster and smarter, but catalog facts must stay traceable
and honest.
