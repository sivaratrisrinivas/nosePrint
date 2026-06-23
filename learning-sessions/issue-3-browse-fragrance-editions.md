# Issue 3: Browse Fragrance Editions and inspect a Scent Profile

## Understanding checklist

- [x] A Fragrance is the named scent people recognize.
- [x] A Fragrance Edition is a specific form of that scent, such as EDT or EDP.
- [x] SQLite owns the Real Catalog because it is the single trusted local catalog.
- [x] Browse searches only Real Catalog records, not Scale-Test Catalog records.
- [x] `browse` returns `fragrance_edition_id` so `scent-profile` can inspect the exact selected Fragrance Edition.
- [x] A Scent Profile contains scent comparison facts: main accords, note pyramid, and scent family.
- [x] Brand, price, bottle size, and marketing copy are not part of the Scent Profile.
- [x] The reason to exclude shopping/marketing facts is that NosePrint compares scent traits, not price, brand status, or advertising language.
- [x] Missing scent facts are shown as `unknown` instead of guessed.
- [x] Empty search and unavailable catalog states should be clear to a shopper.

## Teaching session: domain words

Current focus:

- Fragrance
- Fragrance Edition
- Scent Profile

The learner asked what these mean exactly, so the first teaching step should
verify that these three words are clear before moving into commands or edge
cases.

Progress:

- Fragrance vs Fragrance Edition distinction understood.
- Vocabulary nudge: prefer "Edition" over "version/variation" in NosePrint docs
  and tests.
- Scent Profile understood as scent-comparison facts, excluding marketing,
  brand, price, and bottle size.
- Vocabulary nudge: prefer "notes" or "scent facts" over "ingredients" because
  the catalog does not prove real formula ingredients.
- Learner explained why price/brand/marketing should stay out of Scent Profile:
  they do not help compare scent closeness.
- Learner explained the command flow: `browse` finds the Fragrance Edition id;
  `scent-profile` uses that id to show the selected edition's scent facts.
- Learner connected `unknown` to trustworthy comparison: guessed facts would
  make future similarity/vector work learn from false catalog information.
- Learner answered that EDT and EDP should both be shown, and missing main
  accords should display as `unknown`.
- Learner restated SQLite source-of-truth correctly: SQLite is the trusted
  catalog, and a search index can be rebuilt from it and checked against it.
- Learner gave a complete issue summary. Final nuance reinforced: `unknown` is
  for missing Scent Profile facts, while empty search uses `no_matches` and a
  missing/unimported catalog uses the unavailable-catalog message.

## Kid-friendly explanation

Think of a Fragrance as a book title, like "Sample Rose." A Fragrance Edition is
one particular copy of that book, like the paperback or hardcover. They share a
name, but they may feel different, cost different amounts, or be used
differently. NosePrint keeps EDT and EDP apart for the same reason a library
does not pretend every edition of a book is exactly the same object.

SQLite owns the catalog because NosePrint needs one dependable notebook where
the real fragrance records live. Search tools can be fast helpers later, but
SQLite is the place we trust for the final facts. That prevents two tools from
arguing about which record is real.

The new browse path lets someone search the Real Catalog by Fragrance name, pick
a Fragrance Edition by id, and then see its Scent Profile. That profile only
shows scent facts used for comparison: accords, note pyramid, and scent family.
If NosePrint does not know a scent fact, it says `unknown` instead of making one
up.
