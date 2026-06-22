# NosePrint

NosePrint is a fragrance discovery project that runs on your own computer. Its long-term goal is to help someone start with a fragrance they know—or describe a scent they want—and find similar choices.

## What is built today

The first part of NosePrint is complete: a safety check for catalog data.

Before fragrance information can enter NosePrint, the project checks:

- where the information came from;
- who published it;
- whether reuse is clearly allowed;
- whether the file has the expected columns and number of rows;
- whether missing, repeated, or broken rows are handled honestly.

Only a file that passes every check can enter the Real Catalog. Accepted information is stored in a small local database. NosePrint keeps both the original values and the cleaned values, so every change can be traced later.

## Why this matters

A search tool can return answers quickly, but speed does not make questionable information trustworthy. NosePrint checks the source first so it does not present copied, unclear, or made-up fragrance facts as real shopping information.

The proposed 2,191-row Perfume Recommendation Dataset currently has an **inconclusive** result. Its page says the data is public domain, but it does not show where all descriptions, notes, and image links originally came from or whether the publisher could give reuse permission for them. NosePrint therefore imports none of those rows. This is the expected safe result, not an error to work around.

Read the full [audit result](docs/audits/perfume-recommendation-dataset-v1.md).

## How it works

The catalog tool has four commands:

1. `audit` checks a source file and writes a report.
2. `import` accepts only the exact file that received a passing report.
3. `inspect` shows accepted Real Catalog records and where they came from.
4. `inspect-quarantine` shows rows kept out of the catalog and explains why.

The import is safe to run more than once: it will not create extra copies of the same fragrance. Missing names or brands are rejected, missing scent notes are set aside for review, and repeated fragrance editions are counted and skipped.

See [how to run the audit and import](docs/catalog-import.md) for complete commands.

## Run the checks

NosePrint currently needs only Python 3; there are no extra packages to install.

```bash
python3 -m unittest discover -v
```

The tests use small made-up files. They prove that a failed check blocks the import, a valid file can be imported, original values remain traceable, questionable rows stay out, and running the import twice does not duplicate records.

## Learn from this step

The short lesson [Trust before import](lessons/0001-trust-before-import.html) explains the safety check with a simple trading-card example.

## What comes next

The next planned step is catalog browsing. It will let someone search approved Real Catalog records by fragrance name, choose a specific fragrance edition, and view its scent information. It will not weaken or bypass the source check added here.
