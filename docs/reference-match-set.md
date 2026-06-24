# Reference Match Set Evaluation

The Reference Match Set is a small, human-checked answer key for retrieval
evaluation. It is not training data, user activity, a Real Catalog source, or
embedding input. It should live as a separate JSON file and be passed to the
evaluation command explicitly.

Use a fresh Qdrant-style ANN index and the same SQLite catalog state for both
retrieval methods:

```bash
python3 -m noseprint.catalog rebuild-qdrant-index \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json

python3 -m noseprint.catalog evaluate-reference-matches \
  --database var/noseprint.sqlite3 \
  --index var/qdrant-index.json \
  --reference-match-set data/reference-match-set-v1.json \
  --limit 10
```

The report includes:

- the Reference Match Set id, purpose, and separation notice
- embedding model, model version, pipeline version, dimensions, and runtime
- exact-cosine and Qdrant ANN retrieval configuration
- recall-at-k metrics for reasonable alternatives
- per-reference retrieved ids, hit ids, and recall-at-k
- weak or surprising Scent Matches with factual Profile Comparisons for
  inspection

The starter file at `data/reference-match-set-v1.json` documents the expected
shape. Its edition ids must match the Real Catalog database being evaluated.
