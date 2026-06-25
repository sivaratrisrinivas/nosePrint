# Use Parfumo as the single Real Catalog

NosePrint uses the TidyTuesday Parfumo CSV as the Real Catalog for the local
application.

The public app command is:

```bash
python3 -m noseprint.catalog run --source /path/to/parfumo_data_clean.csv
```

That command imports Parfumo into SQLite with `catalog_kind = 'real'`, rebuilds
the local Qdrant-style index from SQLite, and serves the minimal browser UI.
SQLite remains the catalog source of truth. The index remains a rebuildable
helper.

The older Scale-Test separation was useful while proving ANN behavior, but the
project now needs one understandable catalog for learning, demos, and the local
UI. Parfumo rows therefore appear in normal browse, Scent Profile, Scent Match,
and Scent Request workflows.

Manual Curated Batches remain available as an enrichment workflow for reviewed
facts such as Comparable Prices, Wear Profiles, or source-checked corrections.
They are not the first-run catalog path.
