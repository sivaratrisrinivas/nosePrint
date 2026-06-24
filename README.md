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
Fragrance Editions by Fragrance name, inspect a selected Scent Profile, find
exact-cosine Scent Matches for a selected Fragrance Edition, and serve those
matches through a rebuildable Qdrant-style ANN index.

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

The catalog workflow has nine commands:

1. `audit` checks a candidate Real Catalog source and writes a report.
2. `import` accepts only the exact file that received a passing report, unless
   the owner explicitly accepts an inconclusive risk.
3. `inspect` shows accepted Real Catalog records and where they came from.
4. `inspect-quarantine` shows rows kept out of the catalog and explains why.
5. `browse` searches Real Catalog Fragrance Editions by Fragrance name.
6. `scent-profile` shows the selected Fragrance Edition's Scent Profile.
7. `scent-matches` returns ranked exact-cosine Scent Matches from the Real
   Catalog with calibrated labels, factual Profile Comparisons, and optional
   Comparable Price filtering.
8. `qdrant-health` reports SQLite catalog, embedding runtime, and ANN index
   freshness state.
9. `rebuild-qdrant-index` rebuilds the ANN index from SQLite Scent Profiles
   without turning the index into the catalog source of truth.

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

If curated Comparable Prices exist in SQLite, inspect dated United States USD
snapshots alongside the same Scent Match results:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --show-prices
```

Use `--cheaper-only` to keep only alternatives with known same-size Comparable
Prices below the selected Fragrance Edition. Price per millilitre is shown as a
second check. Unknown prices remain `unknown`, and different bottle sizes do not
support a strict cheaper claim.

Build and check the ANN index before using the Qdrant retrieval path:

```bash
python3 -m noseprint.catalog qdrant-health \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json

python3 -m noseprint.catalog rebuild-qdrant-index \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

Then run Scent Matches through the fresh index:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json \
  --edition-id 1 \
  --limit 10
```

The ANN path checks model, model version, serialization pipeline, dimensions,
catalog fingerprint, and point count before serving results. Missing or stale
indexes return a rebuild message instead of serving possibly incorrect matches.
ANN hits contain only stable identifiers and retrieval payloads; final result
details and Profile Comparisons are hydrated from SQLite. The response also
keeps exact cosine as the baseline and reports recall-at-k plus separate
embedding, retrieval, and hydration latency fields.

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
- Comparable Prices are dated United States USD snapshots stored in SQLite.
  They can filter or annotate Scent Matches, but they do not change Scent Match
  scores, labels, or Profile Comparisons.
- Scent Match scores are model-specific exact-cosine scores, not probabilities,
  percent-identical claims, or promises about how someone will perceive a
  Fragrance Edition on skin.
- Profile Comparisons report shared, reference-only, and candidate-only main
  accords, note-pyramid facts, and scent family facts from SQLite. Missing facts
  stay `unknown`.
- SQLite records versioned 384-number Scent Profile embeddings so future search
  indexes can be rebuilt without becoming the catalog source of truth.
- The Qdrant-style ANN index is derived from SQLite, freshness-checked before
  use, and safe to rebuild when missing, stale, or incompatible.

## Learning Notes

This repository also contains local learning material created while building the
project. `MISSION.md`, `RESOURCES.md`, `NOTES.md`, `lessons/`, `reference/`, and
`learning-sessions/` capture teaching context and explanations for the project
owner. Product code should remain usable without reading those learning files.

## Direction

The next project step is layering shopper filters and evaluation workflows on
top of the truthful SQLite Real Catalog, exact-cosine baseline, and rebuildable
ANN index. The important rule remains the same: search can get faster and more
useful, but catalog facts must stay traceable and honest.
