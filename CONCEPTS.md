# Concepts

> Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound captures and refreshes learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Span tagging

### Span

A contiguous stretch of input text, reported in original-text character offsets, that matched at least one label or headword.

A span always covers whole source characters: it never begins or ends partway through the text a single character normalizes into.

### Annotation layer

One of the two independent sources of anchors on a span: FOLIO concepts, or OpenGloss word senses.

The layers are never merged into one identity; they relate only through crosswalk links.

### Anchor

A claim that a span denotes a particular FOLIO concept or OpenGloss sense, carrying its match method and provenance.

An anchor is evidence, never proof: consumers may rank or discount it, and nothing renames or merges facts on an anchor alone.

### Rank

An anchor's standing within its own annotation layer: primary when no anchored span of that layer contains its span, otherwise fallback.

A fallback anchor names the nearest anchored span that contains it, which outranks it, so a phrase with its own meaning ("good faith") outranks the anchors on its words while those stay available as weaker evidence.

### Crosswalk link

A typed mapping from a FOLIO anchor to an OpenGloss anchor on the same span or on a span it contains, taken from the FOLIO repository's published mapping set.

Its predicate is a SKOS mapping property recording the kind of match (exact, close, broad, narrow, or related), and it carries the mapping set's identity and version.

### Lexicon

The OpenGloss senses compiled from one pinned dataset revision into a compact, verified artifact that the tagger reads at run time.

## Relationships

- A span has zero or more anchors in each annotation layer; each anchor has exactly one rank.
- A crosswalk link joins one FOLIO anchor to one OpenGloss anchor and never changes either anchor's rank.
