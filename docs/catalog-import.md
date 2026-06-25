# Run and import the Real Catalog

NosePrint uses one public app workflow. Python 3's standard library is
sufficient; no package installation is required.

## Run the application

Download `parfumo_data_clean.csv` outside the repository, then run:

```bash
python3 -m noseprint.catalog run \
  --source /tmp/parfumo_data_clean.csv
```

The command imports Parfumo as the Real Catalog, writes SQLite data to
`var/noseprint.sqlite3`, rebuilds `var/qdrant-index.json`, and starts the local
UI at `http://127.0.0.1:8000/`.

On later runs, omit `--source` to reuse the existing local catalog:

```bash
python3 -m noseprint.catalog run
```

Use `--database`, `--index`, `--host`, or `--port` only when a different local
path or bind address is needed.

## Import Parfumo without serving the UI

```bash
python3 -m noseprint.catalog import-parfumo \
  --source /tmp/parfumo_data_clean.csv \
  --database var/noseprint.sqlite3
```

The importer expects the cleaned TidyTuesday columns:

```text
Number,Name,Brand,Release_Year,Concentration,Rating_Value,Rating_Count,Main_Accords,Top_Notes,Middle_Notes,Base_Notes,Perfumers,URL
```

Accepted rows become `real` Fragrance Editions. The importer normalizes main
accords and top, middle, and base notes into Scent Profiles, uses the first
listed main accord as the scent family, quarantines rows with no usable Scent
Profile facts, rejects malformed or identity-less rows, and skips duplicate
brand/name/concentration rows deterministically.

Running this command replaces the current Real Catalog so Parfumo remains the
only catalog used by the app.

## Historical audited imports

The older `audit` and `import` commands remain available for tests and learning
the original source-gating workflow, but the current product path is Parfumo via
`run` or `import-parfumo`.

## Review a Curated Batch before Real Catalog import

A Curated Batch is a working set of candidate Real Catalog rows. Batch review is
separate from import: first prepare and preview the Curated Batch, then import
only after every row that should ship has been verified.

Pass 1: collect facts. Create a draft Curated Batch CSV, then fill in one
Fragrance Edition per row with Fragrance name, Fragrance Edition name, brand,
concentration when known, Scent Profile facts, identity source URLs, Scent
Profile source URLs, and curation notes. Use explicit `unknown` values when a
permitted source does not support a fact. Draft Curated Batch CSV files stay
outside the repository while facts are still being collected.

Pass 2: verify facts. Re-open each source URL, check that the row still matches
the source, confirm that the Scent Profile facts are factual rather than copied
marketing prose, and set `curator_review_status` to `reviewed` only after that
check is complete. Commit only reviewed Curated Batch files, templates, docs,
and source-traceable data. Half-reviewed rows stay outside the repository.

Generate a header-only Curated Batch CSV template with:

```bash
python3 -m noseprint.catalog curated-template > /tmp/curated-batch.csv
```

Rows remain drafts until `curator_review_status` is set to `reviewed`.
Draft Curated Batch CSV files stay outside the repository until review is
complete.

Preview a draft Curated Batch before import with:

```bash
python3 -m noseprint.catalog curated-preview --source /tmp/curated-batch.csv
```

The preview is read-only. It reports ready, rejected, and duplicate rows, plus
rows missing identity source URLs, Scent Profile source URLs, or a reviewed
curator status. It also reports the stricter Batch 1 quality result: rows are
Batch 1 ready only when at least two of note pyramid facts, main accords, and
scent family are known. Rows that pass import rules but are too weak for Batch 1
are reported separately, and the preview lists missing top, middle, and base note
groups so weak Scent Profiles are easy to spot.

Read the preview from the top down:

- `rows.ready` should match the rows intended for Real Catalog import.
- `rows.rejected`, `rejections`, `missing_source_urls`, and `review_status`
  must be resolved before import.
- `duplicates` should contain only rows intentionally skipped because the same
  brand and Fragrance Edition already appeared earlier in the Curated Batch.
- `batch_1.ready` should contain only rows that meet the Batch 1 quality bar:
  at least two known Scent Profile groups across note pyramid facts, main
  accords, and scent family.
- `batch_1.too_weak_rows` identifies importable rows that should wait for a
  later Curated Batch because their Scent Profile is too sparse for useful Scent
  Matches.
- `missing_note_groups` shows which ready rows still lack top, middle, or base
  notes.

The preview also includes a `coverage` section for ready, non-duplicate rows:

- `scent_families` counts each known scent family, so a Curated Batch does not
  quietly lean too hard into one family.
- `repeated_brands` lists brands with more than one ready Fragrance Edition, so
  one brand does not dominate by accident.
- `common_notes` lists top, middle, and base notes that appear in more than one
  ready Fragrance Edition, so useful Scent Match overlap is visible before
  import.

Coverage is calculated only from facts present in the Curated Batch CSV. Missing
facts remain unknown and are not inferred.

Batch 1 data is added in a later issue. This workflow prepares, previews, and
reviews Curated Batch files; it does not add new Fragrance Editions to the Real
Catalog by itself.

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
`fragrance_edition_id` values.

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
edition from its own result list.

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

If SQLite catalog facts, the embedding model, the model version, the serialization
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
