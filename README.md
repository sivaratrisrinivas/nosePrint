# NosePrint

NosePrint is a local-first fragrance discovery project. It helps someone start
with a Fragrance they already know, or describe the kind of scent they want, and
find nearby Fragrance Editions using clear scent facts.

## What

NosePrint is a small Python command-line app backed by SQLite. It can:

- audit and import fragrance data into a Real Catalog;
- browse Fragrance Editions by name;
- show the Scent Profile for a selected Fragrance Edition;
- find Scent Matches from either a selected Fragrance Edition or a beginner
  Scent Request;
- filter or explain results with Comparable Prices and Wear Profile facts;
- rebuild and check a local Qdrant-style search index;
- evaluate retrieval quality and benchmark larger generated data.

The three core ideas are:

- **Fragrance**: the named scent product people recognize.
- **Fragrance Edition**: a specific release of that Fragrance, such as EDT, EDP,
  Parfum, or Extrait.
- **Scent Profile**: the scent-only facts used for comparison: notes, main
  accords, note pyramid, and scent family.

## Why

Fragrance recommendations can sound confident while mixing real facts, unclear
source material, marketing copy, prices, and generated test data. NosePrint is
built to keep those things separate.

SQLite is the source of truth. Search indexes are helpful maps, not the catalog
itself. If an index is missing, stale, unreadable, or wrong, it can be rebuilt
from SQLite without losing catalog data.

Before fragrance information enters the Real Catalog, the project checks:

- where the information came from;
- who published it;
- whether reuse is clearly allowed;
- whether the file has the expected columns and number of rows;
- whether missing, repeated, or broken rows are handled honestly.

The proposed 2,191-row Perfume Recommendation Dataset has an **inconclusive**
audit. Its page says the data is public domain, but it does not show where all
descriptions, notes, and image links originally came from. NosePrint blocks
automatic import by default. For this personal side project, the owner can
explicitly accept that risk without pretending the audit passed.

Read the full [audit result](docs/audits/perfume-recommendation-dataset-v1.md).

## How

NosePrint needs only Python 3. There are no extra packages, no account system,
and no public deployment step.

```bash
python3 -m unittest discover -v
```

The normal flow is:

1. Audit a candidate source.
2. Import accepted Real Catalog records into SQLite.
3. Browse or search using Scent Profiles.
4. Build a local Qdrant-style index when faster search is needed.
5. Check health before using that index.

Health checks tell you whether SQLite has Real Catalog data, whether the
embedding runtime is using CPU or CUDA, and whether the index is fresh:

```bash
python3 -m noseprint.catalog qdrant-health \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

If the index is missing, stale, or unreadable, rebuild it from SQLite:

```bash
python3 -m noseprint.catalog rebuild-qdrant-index \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

The embedding runtime defaults to CPU. If a local CUDA runtime is supported, set
`NOSEPRINT_CUDA_SUPPORTED=1` and leave `NOSEPRINT_EMBEDDING_DEVICE=auto`. If
CUDA is requested but unavailable, health reports a clear CPU fallback.

## Vector Database Primitives Learned

- **Vectors**: NosePrint turns each Scent Profile into a 384-number list in
  `noseprint/catalog.py`; the exact Scent Match path compares those lists with
  cosine similarity.
- **Points**: `rebuild-qdrant-index` writes one point per Real Catalog Fragrance
  Edition to `var/qdrant-index.json`: an id, a vector, and a small payload.
- **Payloads**: each point carries `fragrance_edition_id` and `catalog_kind`.
  Shopper search accepts only payloads that match a Real Catalog row in SQLite.
- **Top-k search**: `scent-matches --index ... --limit 10` asks for the nearest
  matches, then hydrates final details from SQLite.
- **Freshness metadata**: the index stores model, dimension, schema version,
  point count, and catalog fingerprint so `qdrant-health` can detect stale or
  unreadable indexes.
- **Exact baseline**: exact cosine search stays available so ANN results can be
  checked instead of trusted blindly.
- **Recall-at-k**: `evaluate-reference-matches` and
  `benchmark-scale-test-catalog` report how much the ANN path overlaps the exact
  baseline.
- **Safe rebuilds**: the index can be deleted or regenerated because SQLite owns
  the catalog facts.

The catalog workflow has thirteen commands:

1. `audit` checks a candidate Real Catalog source and writes a report.
2. `import` accepts only the exact file that received a passing report, unless
   the owner explicitly accepts an inconclusive risk.
3. `inspect` shows accepted Real Catalog records and where they came from.
4. `inspect-quarantine` shows rows kept out of the catalog and explains why.
5. `browse` searches Real Catalog Fragrance Editions by Fragrance name.
6. `scent-profile` shows the selected Fragrance Edition's Scent Profile.
7. `scent-matches` returns ranked exact-cosine Scent Matches from the Real
   Catalog with calibrated labels, factual Profile Comparisons, and optional
   Comparable Price and Wear Profile filtering.
8. `scent-request` interprets beginner wanted and unwanted scent traits before
   searching from an ephemeral Scent Request.
9. `qdrant-health` reports SQLite catalog, embedding runtime, and ANN index
   freshness state.
10. `rebuild-qdrant-index` rebuilds the ANN index from SQLite Scent Profiles
    without turning the index into the catalog source of truth.
11. `evaluate-reference-matches` compares exact-cosine and Qdrant ANN retrieval
    against a separate Reference Match Set answer key.
12. `generate-scale-test-catalog` creates a deterministic generated Scale-Test
    Catalog for performance experiments.
13. `benchmark-scale-test-catalog` measures exact-cosine versus Qdrant ANN
    retrieval over the isolated Scale-Test Catalog.

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

If curated Wear Profile facts exist in SQLite, inspect reported longevity and
projection alongside Scent Matches:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --show-wear-profiles
```

Use `--wear-longevity` and `--wear-projection` to narrow alternatives by known
Wear Profile facts. Missing Wear Profile facts remain `unknown` and do not match
filters. Wear Profile output includes a notice that cataloged longevity and
projection are not guarantees for every person's skin.

Search from a beginner Scent Request when the shopper does not know a Fragrance
name:

```bash
python3 -m noseprint.catalog scent-request \
  --database var/noseprint.sqlite3 \
  --wanted "fresh rose" \
  --unwanted "oud"
```

The first response shows interpreted wanted and unwanted Scent Profile traits
using only vocabulary already present in the Real Catalog. Run the same request
with `--confirm` to search, `--revise-wanted` or `--revise-unwanted` to inspect a
revised interpretation before searching, or `--cancel` to stop. Confirmed Scent
Requests use an ephemeral query vector and never create a Fragrance or Fragrance
Edition. Known unwanted traits are filtered transparently from result candidates
without mutating catalog rows.

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

Evaluate retrieval quality with a small human-checked Reference Match Set:

```bash
python3 -m noseprint.catalog evaluate-reference-matches \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json \
  --reference-match-set data/reference-match-set-v1.json \
  --limit 10
```

This workflow reports exact-cosine and Qdrant ANN recall-at-k against the same
catalog state, keeps model and search configuration visible, and includes
factual Profile Comparisons for weak or surprising outcomes. Reference Match Set
data is evaluation-only: it is not imported into the Real Catalog, used for
training, inferred from user activity, or included in Scent Profile embeddings.
See [Reference Match Set evaluation](docs/reference-match-set.md) for the
documented answer-key format and report fields.

Generate and benchmark an isolated Scale-Test Catalog:

```bash
python3 -m noseprint.catalog generate-scale-test-catalog \
  --database var/noseprint.sqlite3 \
  --records 10000 \
  --seed 20260624

python3 -m noseprint.catalog benchmark-scale-test-catalog \
  --database var/noseprint.sqlite3 \
  --index var/scale-test-qdrant-index.json \
  --reference-edition-id 1000001 \
  --limit 10
```

The generated records are stored with `catalog_kind: "scale-test"` and indexed
with a separate `qdrant-scale-test-index-v1` schema. Benchmark reports include
catalog size, exact-cosine configuration, Qdrant ANN configuration, recall-at-k,
embedding latency, retrieval latency, and hydration latency. These performance
results stay separate from Reference Match Set quality evaluation and normal
shopping Scent Matches.

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
- Wear Profile longevity and projection facts are stored in SQLite separately
  from Scent Profiles. They can filter or annotate Scent Matches, but they do
  not change Scent Match scores, labels, embeddings, or Profile Comparisons.
- Scent Match scores are model-specific exact-cosine scores, not probabilities,
  percent-identical claims, or promises about how someone will perceive a
  Fragrance Edition on skin.
- Profile Comparisons report shared, reference-only, and candidate-only main
  accords, note-pyramid facts, and scent family facts from SQLite. Missing facts
  stay `unknown`.
- Scent Requests are ephemeral query state. They can produce Scent Matches, but
  they are never persisted as Fragrances or Fragrance Editions.
- Scent Request interpretation uses known Real Catalog Scent Profile vocabulary
  only. Empty and unsupported requests return clear states without invented
  facts; ambiguous terms are shown explicitly.
- SQLite records versioned 384-number Scent Profile embeddings so future search
  indexes can be rebuilt without becoming the catalog source of truth.
- Local runtime health distinguishes SQLite Real Catalog state, embedding
  runtime state, and Qdrant-style ANN index state before serving ANN Scent
  Matches.
- The Qdrant-style ANN index is derived from SQLite, freshness-checked before
  use, and safe to rebuild when missing, stale, unreadable, or incompatible.
- Reference Match Set data is kept separate from Real Catalog imports,
  embeddings, training data, and user activity. Evaluation reports use it as an
  answer key without changing shopper catalog results.
- Scale-Test Catalog data is deterministic generated benchmark data, not Real
  Catalog shopping inventory. Normal browse, selected-edition, Scent Request,
  Comparable Price, and Wear Profile workflows keep enforcing Real Catalog
  eligibility below presentation code.

## Learning Notes

This repository also contains local learning material created while building the
project. `MISSION.md`, `RESOURCES.md`, `NOTES.md`, `lessons/`, `reference/`, and
`learning-sessions/` capture teaching context and explanations for the project
owner. Product code should remain usable without reading those learning files.

## Direction

The local retrieval pipeline is now hardened around the truthful SQLite Real
Catalog, exact-cosine baseline, rebuildable ANN index, Reference Match Set
evaluation, and isolated Scale-Test Catalog benchmark. The important rule
remains the same: search can get faster and more useful, but catalog facts must
stay traceable and honest.
