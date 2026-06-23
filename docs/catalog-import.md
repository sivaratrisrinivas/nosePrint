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
