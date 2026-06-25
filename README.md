# NosePrint

## What

NosePrint is a small local app for exploring perfume matches.

It imports the TidyTuesday Parfumo CSV, stores the perfumes in SQLite, and opens
a simple browser page where you can:

- search for a perfume by name;
- see its notes, accords, and scent family;
- find other perfumes with similar scent facts.

Everything runs on your machine with Python. There is no account, cloud service,
or package install step.

## Why

Perfume recommendations often mix real scent facts with sales copy, prices, and
guesses. NosePrint keeps the comparison focused on the scent facts that are
available in the catalog.

SQLite is the source of truth. The search index is only a helper, so it can be
rebuilt at any time from the SQLite database.

## How

Run tests:

```bash
python3 -m unittest
```

Start the app with a Parfumo CSV:

```bash
python3 -m noseprint.catalog run \
  --source /tmp/parfumo_data_clean.csv
```

Then open:

```text
http://127.0.0.1:8000/
```

The first run imports the CSV into `var/noseprint.sqlite3` and builds
`var/qdrant-index.json`. Later, reuse the local catalog with:

```bash
python3 -m noseprint.catalog run
```

Useful optional commands:

```bash
python3 -m noseprint.catalog import-parfumo \
  --source /tmp/parfumo_data_clean.csv \
  --database var/noseprint.sqlite3

python3 -m noseprint.catalog qdrant-health \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json
```

For the full command reference, see [docs/catalog-import.md](docs/catalog-import.md).
