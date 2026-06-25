# NosePrint

NosePrint helps people compare perfumes using their described scent characteristics while being clear about what the comparison can and cannot prove.

## Language

**Fragrance**:
The named scent product people recognize, independent of bottle size. It may have multiple editions with different concentrations.
_Avoid_: Perfume, cologne

**Fragrance Edition**:
A specific concentration of a Fragrance, such as EDT, EDP, Parfum, or Extrait. Editions remain separate because their scent character and price may differ even when they share a name.
_Avoid_: Version, concentration option

**Scent Match**:
A strength label and optional score showing how close two perfumes' listed notes and accords are according to the current comparison model. Raw cosine similarity belongs in the learning view; the match is not a probability or a claim that people will experience the perfumes as equally similar.
_Avoid_: Smell similarity, percentage identical

**Scent Profile**:
The cleaned scent facts used to compare a perfume: its main accords, note pyramid, and scent family. It excludes brand, price, bottle size, and marketing language.
_Avoid_: Marketing description, perfume copy

**Wear Profile**:
The separately reported behavior of a Fragrance Edition after application, including longevity and projection. It may filter or explain results but does not change the Scent Match.
_Avoid_: Scent Profile, strength score

**Comparable Price**:
The normal United States retail price in USD, compared with the same bottle size and with price per millilitre shown as a second check. It is a dated snapshot rather than a promise of a live store price; missing prices remain unknown instead of being guessed.
_Avoid_: Cheapness, global price

**Reference Match Set**:
A small, human-checked collection of perfumes and their reasonable alternatives used to judge whether Scent Matches are useful. It is an answer key for evaluation, not training data or live user feedback.
_Avoid_: Ground truth, feedback data

**Scent Request**:
A beginner-friendly description of wanted and unwanted scent traits used to find perfumes when the person does not know a perfume name. The app shows its interpretation before searching and does not treat the request as a cataloged perfume.
_Avoid_: Scent Profile, prompt

**Profile Comparison**:
A factual summary of scent traits shared by or different between two Scent Profiles. It helps explain a result without claiming to reveal the embedding model's internal reasoning.
_Avoid_: AI reasoning, match explanation

**Real Catalog**:
The collection of real Fragrance Editions whose source and reuse status have been checked. Only this catalog may appear as real shopping inventory.
_Avoid_: Production dataset, benchmark data

**Curated Batch**:
A small group of Fragrance Editions prepared and reviewed together before import into the Real Catalog. Draft batches may be messy, but a committed batch should be reviewed, source-traceable, and useful for Scent Matches.
_Avoid_: Spreadsheet, import dump

**Scale-Test Catalog**:
A clearly labeled collection of generated Fragrance Editions used only to measure vector-search speed and accuracy at larger sizes. Its records must never appear as real or purchasable products.
_Avoid_: Real Catalog, shopping inventory
