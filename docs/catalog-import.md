# Audit and import a Real Catalog source

NosePrint uses one public workflow for catalog construction. Python 3's standard library is sufficient; no package installation is required.

## Audit a candidate

Download a candidate through its authorized source, keep it outside the repository, and run:

```bash
python3 -m noseprint.catalog audit \
  --manifest data/audits/perfume-recommendation-dataset-v1.json \
  --source /path/to/candidate.csv \
  --report /tmp/noseprint-audit-report.json
```

Exit code `0` means every declared license-chain, provenance, schema, row-count, and quality check passed. Exit code `2` means the verdict is inconclusive and import is blocked. The current candidate manifest intentionally returns `2` even if the claimed schema and row count match, because its provenance and license chain are inconclusive.

## Import an explicitly passing source

```bash
python3 -m noseprint.catalog import \
  --audit-report /tmp/noseprint-audit-report.json \
  --source /path/to/the-same-candidate.csv \
  --database var/noseprint.sqlite3
```

The source SHA-256 must match the audited file. Accepted rows become separate Fragrance and Fragrance Edition records in SQLite. Original source values remain attached to the normalized Scent Profile. Missing required identity fields are rejected, rows without notes are quarantined, and duplicate Fragrance Editions are skipped deterministically.

The command reports `accepted`, `rejected`, `transformed`, `duplicates`, and `quarantined` counts. Re-running the same import does not duplicate catalog records.

## Import a manually curated seed Real Catalog

For shopper-facing quality, prefer a small manually reviewed seed Real Catalog
over the current low-detail prototype dataset. See
[`docs/curated-real-catalog.md`](curated-real-catalog.md) for the source policy.

The curated CSV schema is:

```text
fragrance_name,fragrance_edition_name,brand,concentration,main_accords,top_notes,middle_notes,base_notes,scent_family,identity_source_urls,scent_profile_source_urls,curator_review_status,curator_reviewed_on,curation_notes
```

Use the same audit and import commands. The manifest's `expected_schema` must
match the curated columns exactly. Import accepts only rows where:

- identity fields are present: Fragrance name, Fragrance Edition name, and
  brand;
- `curator_review_status` is `reviewed`;
- identity and Scent Profile source URLs are present;
- at least one Scent Profile fact is known: notes, main accords, or scent
  family.

Accepted curated rows become Real Catalog Fragrance Editions with rich Scent
Profiles: main accords, top notes, middle notes, base notes, and scent family.
Missing fact groups remain `unknown` in shopper-facing commands. Rows with no
usable Scent Profile facts are quarantined. Rows that are not reviewed, are
missing source URLs, have missing identity fields, or have malformed columns are
rejected. Duplicate Fragrance Editions are skipped deterministically.

`inspect` keeps the source trace visible by showing the original curated row's
identity and Scent Profile source URLs. Do not copy marketing prose or images
into the curated seed unless their reuse rights are separately verified.

## Import after the owner accepts an inconclusive risk

For a personal side project, you may decide to use a source even when the audit is inconclusive. Do not change the audit verdict to `passed`. Keep the truth visible and import with an explicit owner-risk note:

```bash
python3 -m noseprint.catalog import \
  --audit-report /tmp/noseprint-audit-report.json \
  --source /path/to/the-same-candidate.csv \
  --database var/noseprint.sqlite3 \
  --accept-owner-risk \
  --risk-note "Personal side project: accept inconclusive CC0/provenance risk."
```

This still checks that the source file matches the audited file and that the schema and row count match the manifest. The database records that the project owner accepted the risk, so the data is usable without pretending the audit proved more than it did.

## Inspect the result

```bash
python3 -m noseprint.catalog inspect --database var/noseprint.sqlite3
python3 -m noseprint.catalog inspect-quarantine --database var/noseprint.sqlite3
```

The first command shows accepted Real Catalog records with their source identity and original name. The second shows rejected and quarantined rows with clear reasons.

## Browse the Real Catalog

After SQLite has an imported Real Catalog, search by Fragrance name:

```bash
python3 -m noseprint.catalog browse \
  --database var/noseprint.sqlite3 \
  --query "rose"
```

The response lists matching Real Catalog Fragrance Editions separately. For
example, an EDT and EDP can appear as distinct results with different
`fragrance_edition_id` values. Scale-Test Catalog records are not returned by
this shopping path.

If no Fragrance name matches, the command returns `status: "no_matches"` with a
plain-language message. If the database has not been imported yet, the command
exits with code `2` and explains that the Real Catalog must be imported into
SQLite first.

Use a returned `fragrance_edition_id` to inspect the selected Scent Profile:

```bash
python3 -m noseprint.catalog scent-profile \
  --database var/noseprint.sqlite3 \
  --edition-id 1
```

The Scent Profile contains main accords, note pyramid, and scent family. Brand,
price, bottle size, and marketing copy stay outside the comparison profile.
Missing scent facts are displayed as `unknown`; NosePrint does not guess them.

## Find exact Scent Matches

Use a selected Real Catalog Fragrance Edition as the reference for exact
cosine matching:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10
```

The command returns ranked Real Catalog alternatives and excludes the selected
edition from its own result list. It also excludes Scale-Test Catalog rows from
this shopper path, even if those rows exist in SQLite.

Each Scent Match includes:

- a calibrated `strength_label` such as `strong`, `weak`, `incomplete`, or
  `surprising`
- a `model_specific_score` with a `score_basis` that says the number is exact
  cosine over NosePrint Scent Profile embeddings, not a probability or
  percent-identical claim
- a `profile_comparison` showing shared, reference-only, and candidate-only
  main accords, note-pyramid facts, and scent family facts

The embedding metadata is included in the response:

- `model`: `noseprint-hash-embedding-384`
- `model_version`: `1`
- `pipeline_version`: `scent-profile-serialization-v1`
- `dimensions`: `384`
- `runtime_device`: `cpu`

NosePrint serializes only Scent Profile facts: notes, main accords, note
pyramid, and scent family. Missing fields receive a stable unknown marker rather
than guessed facts. Brand, price, bottle size, marketing copy, and Wear Profile
facts do not change the model-specific exact-cosine score or ordering. Profile
Comparisons are derived from normalized SQLite catalog facts, not from generated
claims about why the model ranked a result.

The generated 384-number embeddings are recorded in SQLite with their model and
pipeline versions. Later search indexes can be rebuilt from these catalog-owned
facts instead of becoming a second source of truth.

## Inspect and filter by Comparable Prices

Comparable Prices are curated SQLite facts attached to Fragrance Editions. Each
known snapshot stores the United States USD amount, bottle size in millilitres,
observation date, and source metadata. They are dated reference information, not
live retailer prices or availability promises.

Show price snapshots without filtering Scent Matches:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --show-prices
```

Return only alternatives with known same-size Comparable Prices below the
selected Fragrance Edition:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --cheaper-only
```

The response keeps the original Scent Match score and label unchanged. Price
metadata appears separately as `comparable_price` and `price_comparison`.
Different bottle sizes show price per millilitre for inspection but are not
treated as strict cheaper claims. Missing prices are displayed as `unknown` and
are not guessed.

## Inspect and filter by Wear Profiles

Wear Profile facts are curated SQLite facts attached to Fragrance Editions and
kept separate from Scent Profiles. They describe reported longevity and
projection after application. They do not become embedding input and do not
change Scent Match scores, labels, ranking, or Profile Comparisons.

Show Wear Profile facts without filtering Scent Matches:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --show-wear-profiles
```

Return only alternatives with known matching Wear Profile facts:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --edition-id 1 \
  --limit 10 \
  --wear-longevity moderate \
  --wear-projection soft
```

Known facts appear as plain catalog values. Missing longevity or projection is
shown as `unknown`, and unknown values do not match filters. The response also
states that Wear Profile facts are reported catalog observations, not a
guarantee for every person's skin.

## Search from a Scent Request

Use `scent-request` when a shopper knows the kind of scent they want but does
not know a Fragrance name. First inspect the interpretation:

```bash
python3 -m noseprint.catalog scent-request \
  --database var/noseprint.sqlite3 \
  --wanted "fresh rose" \
  --unwanted "oud"
```

The response returns `status: "needs_confirmation"` and shows interpreted
wanted and unwanted Scent Profile traits. Interpretation is deterministic: it
only uses note, main accord, and scent family terms already found in Real
Catalog Scent Profiles. Unsupported words are listed instead of guessed.
Ambiguous terms are also shown when a word maps to more than one catalog
vocabulary category.

Revise before searching:

```bash
python3 -m noseprint.catalog scent-request \
  --database var/noseprint.sqlite3 \
  --wanted "fresh rose" \
  --revise-wanted "floral iris" \
  --revise-unwanted "fresh"
```

Cancel before searching:

```bash
python3 -m noseprint.catalog scent-request \
  --database var/noseprint.sqlite3 \
  --wanted "fresh rose" \
  --cancel
```

Confirm to search:

```bash
python3 -m noseprint.catalog scent-request \
  --database var/noseprint.sqlite3 \
  --wanted "fresh rose" \
  --unwanted "oud" \
  --confirm \
  --limit 10
```

Confirmed Scent Requests create an ephemeral query vector from interpreted
wanted traits, then compare it with Real Catalog Scent Profile vectors. The
request is not inserted as a Fragrance or Fragrance Edition. Known unwanted
traits are applied as an explicit candidate filter and the response lists which
Fragrance Editions were excluded. Empty, unsupported, and no-result requests
return clear statuses without invented facts.

## Check and rebuild the Qdrant ANN index

The ANN index is a rebuildable helper derived from SQLite. It stores stable
Fragrance Edition identifiers, 384-number vectors, Real Catalog eligibility, and
metadata needed to detect stale or incompatible search data. It does not store
the shopper-facing Fragrance Edition details.

Check health before serving ANN Scent Matches:

```bash
python3 -m noseprint.catalog qdrant-health \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

The health response distinguishes:

- `sqlite_catalog`: whether SQLite has eligible Real Catalog Scent Profiles
- `embedding_runtime`: the configured model, model version, pipeline version,
  dimensions, selected runtime device, and CPU fallback status
- `qdrant_index`: whether the index is `missing`, `fresh`, `stale`,
  `unreadable`, or not yet applicable because the Real Catalog is empty

The CLI is the local application surface; no public deployment, account system,
or teaching-only mode is required. A healthy local startup has a non-empty
SQLite Real Catalog, an embedding runtime status of `ok` or `accelerated`, and a
fresh Qdrant-style index. If SQLite exists but has no eligible Real Catalog
Scent Profiles, health returns `status: "empty_catalog"` and tells you to run
the audit/import workflow before rebuilding the index.

By default, NosePrint uses the practical CPU embedding path. On a target machine
where the local embedding runtime supports a GTX 1050 Ti-class CUDA path, set:

```bash
NOSEPRINT_CUDA_SUPPORTED=1
NOSEPRINT_EMBEDDING_DEVICE=auto
```

Health then reports `runtime_device: "cuda"` and `status: "accelerated"`. To
force the CPU path, set `NOSEPRINT_EMBEDDING_DEVICE=cpu`. If CUDA is requested
with `NOSEPRINT_EMBEDDING_DEVICE=cuda` but the runtime does not support it,
health reports `status: "fallback"`, `requested_device: "cuda"`, and
`runtime_device: "cpu"` so startup remains usable.

If the index is missing or stale, rebuild it from SQLite:

```bash
python3 -m noseprint.catalog rebuild-qdrant-index \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

Rebuilding records one point for each eligible Real Catalog Fragrance Edition
with only this retrieval payload:

- `fragrance_edition_id`
- `catalog_kind`

Scale-Test Catalog records are not written to the normal shopping index. If
SQLite catalog facts, the embedding model, the model version, the serialization
pipeline, dimensions, or the index schema change, health reports the index as
stale and tells the user to rebuild.

If the index file is unreadable or malformed, shopper Scent Matches return
`status: "index_unavailable"` with `qdrant_index.status: "unreadable"` and the
same rebuild instruction. The broken index can be replaced from SQLite without
deleting catalog data, because SQLite remains the source of truth.

## Find ANN Scent Matches

Pass a fresh index to the same public Scent Match workflow:

```bash
python3 -m noseprint.catalog scent-matches \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json \
  --edition-id 1 \
  --limit 10
```

When `--index` is present, NosePrint checks index freshness before serving
matches. Fresh index hits are hydrated from SQLite before display, so SQLite
remains the source of truth for Fragrance, Fragrance Edition, Scent Profile, and
Profile Comparison details.

The response includes `retrieval` metadata:

- `method`: `qdrant_ann`
- `exact_baseline_method`: `exact_cosine`
- `recall_at_k`: how many ANN results overlap the exact baseline at the same
  limit
- `embedding_latency_ms`, `retrieval_latency_ms`, and `hydration_latency_ms`

If the index is missing, stale, or incompatible, the command returns
`status: "index_unavailable"` and a rebuild message instead of returning
possibly stale matches.

## Evaluate a Reference Match Set

Use a small, human-checked Reference Match Set to check whether exact cosine and
Qdrant ANN retrieval return reasonable alternatives:

```bash
python3 -m noseprint.catalog evaluate-reference-matches \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json \
  --reference-match-set data/reference-match-set-v1.json \
  --limit 10
```

The command requires a fresh ANN index so exact cosine and ANN retrieval are
evaluated against the same SQLite catalog state. The report includes top-k
configuration, embedding model and pipeline versions, recall-at-k metrics,
latency fields, per-reference hit ids, and inspectable weak or surprising
Profile Comparisons.

Reference Match Set JSON is evaluation data only. It is kept separate from Real
Catalog source imports, Scent Profile embeddings, training data, and user
activity. Running the evaluator does not create Fragrances or Fragrance
Editions and does not change shopper browse results.

## Generate a Scale-Test Catalog

Use the Scale-Test Catalog when you need performance data at a larger size than
the provenance-checked Real Catalog can safely provide:

```bash
python3 -m noseprint.catalog generate-scale-test-catalog \
  --database var/noseprint.sqlite3 \
  --records 10000 \
  --seed 20260624
```

The generator is deterministic: the same seed and record count produce the same
generated Fragrance Editions on repeated runs. Each generated row is stored in
SQLite with `catalog_kind: "scale-test"` and a generated source id of
`scale-test-catalog-v1`. These rows are benchmark material only. They are not
Real Catalog records, purchasable inventory, training data, Reference Match Set
quality data, or user activity.

Normal shopper workflows keep filtering below presentation code with
`catalog_kind = 'real'`. That includes browsing, selected-edition Scent Matches,
Scent Request search, Comparable Price filtering, and Wear Profile filtering.

## Benchmark the Scale-Test Catalog

Run Scale-Test performance measurements through the explicit benchmark path:

```bash
python3 -m noseprint.catalog benchmark-scale-test-catalog \
  --database var/noseprint.sqlite3 \
  --index var/scale-test-qdrant-index.json \
  --reference-edition-id 1000001 \
  --limit 10
```

The benchmark command reads only `scale-test` rows, writes a separate
`qdrant-scale-test-index-v1` index, and reports:

- catalog size
- exact-cosine configuration
- Qdrant ANN configuration
- recall-at-k
- embedding latency
- retrieval latency
- hydration latency

The normal shopping Qdrant index continues to use the `qdrant-index-v1` schema
and Real Catalog payloads. Shopper ANN search rejects malformed points whose
point id, payload `fragrance_edition_id`, payload `catalog_kind`, and SQLite
Real Catalog row do not agree.
