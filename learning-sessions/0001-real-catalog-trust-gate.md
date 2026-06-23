# Learning session: Real Catalog trust gate

This checklist tracks demonstrated understanding of NosePrint issue #2. An item is checked only after Srinivas explains or applies it successfully.

Latest answer: Srinivas identified the core safety concern: issue #2 protects NosePrint from using data unless we are confident we have permission/access, especially around copyright. Next checkpoint is distinguishing a license claim from proof of source history.

Srinivas also distinguished private practice from importing into the trusted Real Catalog: private experiments have lower risk, while Real Catalog data needs clear source and permission confidence. Correction given: personal practice is not "do anything"; avoid publishing or mixing uncertain data into trusted data.

## 1. The problem

- [x] Explain why a dataset page saying “CC0” is a claim, not complete proof.
- [x] Explain the difference between source history and reuse permission.
- [x] Explain why an inconclusive audit must keep rows out of the Real Catalog.
- [x] Describe the three audit outcomes: passed, failed, and inconclusive.

## 2. The solution

- [ ] Explain what the `audit` command checks and records.
- [ ] Explain why the source file gets a SHA-256 fingerprint.
- [ ] Explain why `import` checks the audit again instead of trusting one word.
- [ ] Explain why SQLite stores original and cleaned values.
- [ ] Explain why Fragrance and Fragrance Edition are separate records.

## 3. Edge cases

- [ ] Predict what happens to missing names or brands.
- [ ] Predict what happens to missing scent notes.
- [ ] Predict what happens to duplicate Fragrance Editions.
- [ ] Explain why running the same import twice creates no extra catalog records.
- [ ] Explain why changing the source file after its audit blocks import.

## 4. Broader impact

- [ ] Explain why trustworthy input matters before embeddings or vector search.
- [ ] Explain what issue #2 enables for later catalog browsing and matching.
- [ ] Explain what issue #2 deliberately does not solve yet.
