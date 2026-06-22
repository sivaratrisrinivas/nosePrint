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

## Inspect the result

```bash
python3 -m noseprint.catalog inspect --database var/noseprint.sqlite3
python3 -m noseprint.catalog inspect-quarantine --database var/noseprint.sqlite3
```

The first command shows accepted Real Catalog records with their source identity and original name. The second shows rejected and quarantined rows with clear reasons.
