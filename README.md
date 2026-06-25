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

## Vector Database Primitives Learned

NosePrint uses a tiny local version of the ideas behind a vector database:

- **Vector**: a perfume's scent facts are turned into a list of 384 numbers in
  `noseprint/catalog.py`.
- **Point**: each search-index entry stores one perfume id, its vector, and a
  small label saying it came from the real catalog.
- **Payload**: the extra label data attached to a point, used to connect search
  results back to SQLite.
- **Similarity search**: NosePrint compares vectors to find perfumes with nearby
  scent facts.
- **Top k**: the app asks for the best few matches, not every possible match.
- **Recall**: tests can compare the fast index path with the exact slow path to
  check whether the fast path missed expected results.
- **Freshness check**: the index stores a fingerprint of the SQLite catalog, so
  NosePrint can tell when the index needs to be rebuilt.

We learned these by building the flow in small steps: first exact matching from
SQLite, then a rebuildable index file, then health checks, then the browser UI
that uses the same catalog and index.
