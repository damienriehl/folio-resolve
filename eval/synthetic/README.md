# Synthetic evaluation corpus

This directory holds versioned, generated evaluation cohorts. U5 defines the contract only;
corpus data is produced by later campaign units.

## Files and versioning

Each version `N` consists of three immutable files:

- `corpus_vN.jsonl`: scoreable rows plus `needs_review` rows awaiting adjudication.
- `nomatch_vN.jsonl`: intentionally empty-gold passages used only for later false-positive-rate
  measurement. These rows never enter `score_items`.
- `corpus_vN.manifest.json`: content hashes, ontology and answer-rule pins, counts, cohort quality,
  seed, and caller-supplied creation timestamp.

Newly ratified rows create version `N+1`; existing version files are never rewritten. A loader
must verify the SHA-256 hashes of both JSONL slices before returning any rows.

## Row schema

Every JSONL row is canonical JSON with `item_id`, `doc_type`, `jurisdiction`, `text`,
`gold_labels`, `gold_iris`, `verification`, and `provenance`. `item_id` is a stable slug.
`verification` is `deterministic`, `human`, or `needs_review`. `provenance` is opaque audit
metadata containing at least the generator identity in normal production; grader votes and a
`disagreement_class` may be recorded by generation and adjudication stages.

An intentional no-match row sets `provenance.no_match` to `true` and has no gold labels or IRIs.
Without that marker, a deterministic or human-verified empty-gold row is a build error. Rows in
`needs_review` remain visible in the corpus slice but are excluded from scoring.

## Build requirements

Before emission, every string value in every row must pass the salted U4 surface-manifest scan.
Only collision counts and safe item IDs may appear in an error; matching text is never reported.
Every scoreable gold set must be non-empty and no larger than the pinned synthetic answer rule's
`top_k`.

The manifest records `non_lexical_fraction` over individual scoreable `(item, gold IRI)` pairs.
Resolution records an explicit IRI-to-preferred-label map in provenance; that ontology label is
normalized with `normalize_label`, and the pair is non-lexical when the surface is absent verbatim
from the likewise normalized passage. Manually ratified single-gold rows may use their sole gold
label directly; multi-gold rows require the explicit map. A corpus below `non_lexical_floor`
(default `0.30`) is retained for audit but marked `scoreable: false`.

## Frozen cohorts

Published versions are frozen cohorts. Extend a verified manifest only through `extend_corpus`,
which loads and hash-verifies version `N`, combines it with newly ratified rows, and emits new
version `N+1` paths. Never edit, regenerate in place, or repoint an old version after results cite
it.
