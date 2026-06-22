# Mission: Learn vector databases by building NosePrint

## Why
Build a working local fragrance-finding product so Srinivas can understand vector databases well enough to design, inspect, and debug a real similarity-search pipeline rather than only repeat definitions.

## Success looks like
- Explain how a Scent Profile becomes a 384-number embedding.
- Implement and compare exact cosine search with Qdrant ANN search.
- Diagnose why a Scent Match is useful or misleading.
- Grow the catalog toward 10,000 Fragrance Editions without mixing data generations.

## Constraints
- Teach after each implementation issue, not through a Learning Lab inside the product.
- Explain new ideas in language understandable to a curious 12-year-old.
- Run locally on a GTX 1050 Ti with 4 GB VRAM; CPU fallback must remain practical.
- Keep lessons short, concrete, and tied to code that was just implemented.

## Out of scope
- A separate educational mode inside the NosePrint application.
- Model training or fine-tuning before the core retrieval pipeline is understood.
- Public deployment during the local-first version.
