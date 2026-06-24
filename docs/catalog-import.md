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
  dimensions, and CPU runtime fallback
- `qdrant_index`: whether the index is `missing`, `fresh`, or `stale`

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
