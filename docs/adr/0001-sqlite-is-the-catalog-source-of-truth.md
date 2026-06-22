# SQLite is the catalog source of truth

SQLite owns Fragrances, Fragrance Editions, Scent Profiles, Wear Profiles, and Comparable Prices. Qdrant is a rebuildable search index containing identifiers, vectors, and fields needed for filtering; search results use those identifiers to load final details from SQLite. This prevents two databases from becoming competing master copies while preserving Qdrant's vector-search features.
