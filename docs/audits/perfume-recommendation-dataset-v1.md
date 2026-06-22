# Perfume Recommendation Dataset v1 audit

Audited on 2026-06-23. Verdict: **INCONCLUSIVE — do not import as Real Catalog inventory.**

## Source identity and reuse claim

- Current catalog page: <https://www.kaggle.com/datasets/nandini1999/perfume-recommendation-dataset/data>
- Versioned download page: <https://www.kaggle.com/datasets/nandini1999/perfume-recommendation-dataset/versions/1>
- Claimed publisher: Nandini Bansal, Kaggle account `nandini1999`
- Claimed license: `CC0: Public Domain`
- Canonical CC0 1.0 legal text: <https://creativecommons.org/publicdomain/zero/1.0/legalcode>

The Kaggle page currently makes the CC0 claim, but it does not identify the upstream source of the descriptions, note lists, or image URLs. It also supplies no evidence that the publisher owns or is authorized to waive the rights in every field. Creative Commons' own legal text says an affirmer should secure the rights needed before applying CC0 and that CC0 grants only rights the affirmer has authority to grant. The license claim therefore does not establish a complete license chain.

## Claimed schema and size

The data card claims 2,191 rows and five columns:

1. `Name`
2. `Brand`
3. `Description`
4. `Notes`
5. `Image URL`

These are data-card claims, not observations from a locally pinned artifact. No candidate data is committed to this repository.

## Material quality risks

- Fragrance Edition concentration is absent, so EDT, EDP, Parfum, and Extrait cannot be reliably distinguished.
- Main accords, note pyramid positions, and scent family are absent.
- Missing-value, malformed-row, and duplicate rates cannot be reproduced until an immutable artifact is pinned and hashed.
- Descriptions and images may carry third-party rights not established by the Kaggle page's blanket CC0 label.
- Remote image URLs are not stable catalog facts and may disappear or change.

## Decision

The current evidence does not justify treating the 2,191 rows as Real Catalog inventory. The import gate remains closed. This is the intended safe outcome of issue #2, not an import failure to work around.

To reconsider the decision, obtain evidence identifying the upstream source and the publisher's authority for each reused field, pin the exact downloaded artifact, review its schema and quality, then update the manifest statuses only when every check is supported.
