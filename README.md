# NosePrint

## What

NosePrint is a small local app for exploring perfume matches.

It imports the TidyTuesday Parfumo CSV, stores the perfumes in SQLite, and opens
a simple browser page where you can:

- search for a perfume by name;
- choose one Fragrance Edition from the Real Catalog;
- inspect its Scent Profile: notes, accords, and scent family;
- ask for nearby Scent Matches only when you want them.

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

## Browser Flow

The local UI is intentionally one action at a time:

1. **Name the Fragrance**: type any Fragrance name text.
2. **Choose one result**: select a Real Catalog Fragrance Edition.
3. **Read the Scent Profile**: review accords, note pyramid, and scent family.
4. **Find matches**: optionally run Scent Matches for the selected edition.

Scent Matches are opt-in because matching over the full Real Catalog can be
slower than loading the selected Scent Profile. The UI keeps those actions
separate so the profile appears first and matching does not surprise the user.

The app also serves its local logo asset from `assets/noseprint-logo.png`.

## Vector Database Primitives Learned

NosePrint uses a tiny local version of the ideas behind a vector database:

- **Embedding input**: only Scent Profile facts are embedded: notes, main
  accords, note pyramid, and scent family. Brand, price, bottle size, marketing
  copy, and Wear Profile facts are not embedding input.
- **Vector**: each eligible Real Catalog Fragrance Edition's Scent Profile is
  turned into a 384-number vector using `noseprint-hash-embedding-384`.
- **Point**: each index entry stores one Fragrance Edition id, its vector, and
  payload fields needed to connect it back to SQLite.
- **Payload**: metadata attached to a point, including catalog kind and
  Fragrance Edition identity. SQLite remains the source of truth for final
  details.
- **Similarity search**: NosePrint compares vectors to find perfumes with nearby
  scent facts.
- **Top k**: the app asks for the best few matches, not every possible match.
- **Exact cosine baseline**: NosePrint can compare every eligible vector with
  exact cosine similarity. This is slower, but useful as the correctness
  baseline.
- **Qdrant-style ANN index**: `var/qdrant-index.json` is a rebuildable local
  helper that stores the same kind of point data a vector database would use for
  approximate nearest-neighbor retrieval.
- **Recall**: tests can compare the fast index path with the exact slow path to
  check whether the fast path missed expected results.
- **Freshness check**: the index stores a fingerprint of the SQLite catalog, so
  NosePrint can tell when the index needs to be rebuilt.

When you run:

```bash
python3 -m noseprint.catalog run --source /tmp/parfumo_data_clean.csv
```

NosePrint imports the Parfumo rows into SQLite, keeps eligible Real Catalog
Scent Profiles, records their embeddings in SQLite, rebuilds the Qdrant-style
index from those embeddings, and then serves the browser UI. In the current
local catalog, all `41,775` Real Catalog Fragrance Editions have usable Scent
Profile facts and recorded embeddings.
