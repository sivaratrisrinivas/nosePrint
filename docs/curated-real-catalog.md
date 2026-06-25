# Curated Real Catalog enrichment strategy

Last reviewed: 2026-06-25.

NosePrint now uses the TidyTuesday Parfumo CSV as the Real Catalog for the local
application. Manual curation remains useful as a future enrichment path for
Comparable Prices, Wear Profiles, and source-reviewed corrections, but it no
longer blocks the first usable app catalog. Fragrances of the World remains a
strong large product-quality fit, but it is subscription-gated and must not be
treated as available unless the project has a lawful license.

This document records the permitted-source route to make the app feel real
without scraping fragrance communities or importing a gated database.

## Decision

Use Parfumo as the current app Real Catalog. Build manually reviewed enrichment
batches only when the app needs facts Parfumo does not provide or corrections
that should be source-traceable.

The seed catalog should favor quality over size. A useful first target is about
100 Fragrance Editions, curated in small batches. The first 100 should optimize
for Scent Match coverage, with shopper familiarity as a constraint: include
recognizable Fragrance Editions, but deliberately spread and overlap scent
families, notes, and concentrations so matching feels useful rather than sparse.

Each curated row must include:

- Fragrance name.
- Fragrance Edition name.
- Brand.
- Concentration when known.
- Scent Profile facts: main accords, top notes, middle notes, base notes, scent
  family.
- Source URLs used for each fact group.
- Curator review status and review date.
- Explicit `unknown` values for facts that the sources do not support.

Batch 1 should use a stricter quality bar than the importer itself. A Fragrance
Edition should enter Batch 1 only when at least two of these three Scent Profile
groups are known from permitted sources:

- note pyramid facts: top notes, middle notes, or base notes;
- main accords;
- scent family.

A row with only one known group is still useful for later review, but it should
not be part of the first shopper-facing batch.

Draft batch CSV files should stay outside the repository until review is
complete. Commit only reviewed batches, templates, and documentation. This keeps
half-checked rows, messy notes, and source questions out of the permanent repo
history.

Use a two-pass review before a row becomes Real Catalog:

1. Collect the Fragrance Edition facts and source URLs.
2. Verify the row against those sources, then mark it `reviewed`.

No row should become Real Catalog just because it was typed into a CSV.

Build a curated batch preview report before adding more rows. The report should
show:

- ready rows;
- rejected rows;
- duplicate Fragrance Editions;
- rows that pass the importer but are too weak for Batch 1;
- scent family coverage;
- repeated brands;
- common notes and missing note groups;
- rows missing source URLs or review status.

The goal is to see whether a batch is balanced, useful, and clean before import.

Build the curation workflow tools before adding Batch 1 data. The next PRD
should focus on the CSV template, preview report, stricter Batch 1 checks, and
review docs. Batch 1 should be a later issue that uses those tools.

## Source policy

Prefer these sources:

- Official brand product pages, manually reviewed one Fragrance Edition at a
  time.
- Wikidata for CC0 identity facts when a matching item exists.
- Open Beauty Facts only for product-label metadata, not for Scent Profile
  quality.

Avoid these sources unless permission is obtained:

- Fragrances of the World, because it is a commercial/subscription reference.
- Fragrantica, Basenotes, Parfumo, and similar community databases as direct
  import sources.
- Bulk search result snippets or copied third-party roundups as catalog data.

Source notes:

- Wikidata states that structured data in the main, property, and lexeme
  namespaces is available under CC0.
  Source: https://www.wikidata.org/wiki/Wikidata:Licensing
- The Open Database License permits reuse of a database under conditions such
  as attribution and share-alike, but it does not automatically clear rights in
  every individual content item.
  Source: https://opendatacommons.org/licenses/odbl/1-0/
- Open Beauty Facts belongs to the Open Food Facts family of open product
  databases. It is useful for product metadata, but its cosmetic product shape
  does not provide the main accords and note pyramids NosePrint needs.
  Source: https://world.openbeautyfacts.org/
- Official product pages can expose factual scent notes. Examples reviewed:
  CHANEL N°5 Eau de Parfum describes May rose, jasmine, citrus top notes,
  aldehydes, and bourbon vanilla.
  Source: https://www.chanel.com/us/fragrance/p/125530/n5-eau-de-parfum-spray/
- Dior Sauvage Eau de Parfum is presented as a citrus and vanilla Fragrance
  Edition.
  Source: https://www.dior.com/en_us/beauty/products/sauvage-eau-de-parfum-Y0785220.html
- Le Labo Santal 33's official page describes cardamom, iris, violet,
  sandalwood, cedarwood, spicy, leathery, and musky notes.
  Source: https://www.lelabofragrances.com/santal-33-147.html
- Byredo Gypsy Water's official page publishes top, heart, and base notes.
  Source: https://www.byredo.com/us_en/p/gypsy-water-eau-de-parfum?sku=0065204851

This is not legal advice. The project should keep the workflow conservative:
manual review, small seed size, source URLs, no copied marketing prose, no
images unless their reuse rights are separately verified, and no bulk scraping.

## Implementation shape

The existing importer accepts the old candidate schema:

```text
Name,Brand,Description,Notes,Image URL
```

That shape is too weak for the desired seed catalog. Add a new curated import
path instead of bending the old one.

Recommended CSV columns:

```text
fragrance_name,fragrance_edition_name,brand,concentration,main_accords,top_notes,middle_notes,base_notes,scent_family,identity_source_urls,scent_profile_source_urls,curator_review_status,curator_reviewed_on,curation_notes
```

Recommended rules:

- Accept only `curator_review_status=reviewed`.
- Require at least one identity source URL and one Scent Profile source URL.
- Store the source URLs in `source_records.original_values_json`.
- Import main accords, note pyramid, and scent family into `scent_profiles`.
- Keep missing fact groups as `unknown`; do not infer from brand, price,
  marketing text, or nearby Fragrance Editions.
- Reject rows with missing identity fields.
- Quarantine rows whose Scent Profile has no notes, no main accords, no note
  pyramid, and no scent family.
- Preserve SQLite as the catalog source of truth and keep indexes rebuildable.

## Suggested issue

Title:

```text
Import a manually curated seed Real Catalog with rich Scent Profiles
```

Labels:

```text
ready-for-agent
```

Body:

```text
## Goal

Add a curated Real Catalog enrichment path for reviewed facts that are not
present in the Parfumo app catalog.

## Context

The TidyTuesday Parfumo CSV is the current Real Catalog. Use the manual curation
route described in docs/curated-real-catalog.md only for reviewed enrichment.

## Acceptance criteria

- A curated seed CSV schema supports Fragrance, Fragrance Edition, brand,
  concentration, main accords, top/middle/base notes, scent family, source URLs,
  review status, and review date.
- The import command accepts only reviewed rows with source URLs.
- Rich Scent Profile fields are imported into SQLite.
- Missing facts remain unknown instead of being guessed.
- Rows with missing identity fields are rejected.
- Rows with no usable Scent Profile facts are quarantined.
- Browse, scent-profile, Scent Match, Scent Request, Comparable Price, Wear
  Profile, and Qdrant rebuild flows keep using only Real Catalog rows.
- Tests cover accepted, duplicate, rejected, and quarantined curated rows.
- docs/catalog-import.md explains the curated import workflow.
```
