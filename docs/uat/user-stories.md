# User acceptance stories

Every story below uses a stable persona-scoped ID. Acceptance criteria describe public,
caller-observable behavior using only the README's synthetic examples.

## Pipeline integrator (PI)

### US-PI-01 — Run the quick-start pipeline

**Story:** As a pipeline integrator, I want to construct `MatchPipeline` with an
`InMemoryOntology`, so that I can resolve source text without a service or network dependency.

**Acceptance criteria:**

- The README quick-start ontology can be constructed from `Concept` objects.
- `pipe.match("rules of arbitration")` ranks the concept labelled `Arbitration Rules` first.

### US-PI-02 — Thread a domain prior

**Story:** As a pipeline integrator, I want to pass a `DomainPrior` into `MatchPipeline.match`, so
that an optional judge can receive the corpus subject without changing the offline core.

**Acceptance criteria:**

- `DomainPrior.from_manifest_subjects("treatise", [("R-lit", "Litigation")])` retains the
  `Litigation` subject.
- Passing that prior while matching `Defenses` completes without a provider, key, or network call.

### US-PI-03 — Filter metadata sources

**Story:** As a pipeline integrator, I want to classify front matter before matching it, so that a
copyright page is never tagged as substantive text.

**Acceptance criteria:**

- `SourceClassifier` classifies the README's synthetic copyright-page case as metadata rather than
  substance.
- The observable classification can be used to keep that source out of the matching stage.

## Scoring-only integrator (SI)

### US-SI-01 — Preserve the documented exact scores

**Story:** As a scoring-only integrator, I want literal scores for the README's word-order example,
so that my thresholds distinguish the exact-string and overlap paths.

**Acceptance criteria:**

- `arbitration rules` scores exactly `99.0` against `Arbitration Rules`.
- `rules of arbitration` scores exactly `88.0` against `Arbitration Rules`.
- Both queries rank `Arbitration Rules` first in the README ontology.

### US-SI-02 — Weight the specificity penalty

**Story:** As a scoring-only integrator, I want to weight the specificity penalty, so that short
claim names can reach fully named concepts without changing the historical default.

**Acceptance criteria:**

- `Habitability` against `Breach of Warranty of Habitability` scores exactly `67.5` with
  `specificity_penalty=1.0`.
- Setting `specificity_penalty=0.0` produces a score greater than `67.5` while remaining in the
  public `0`–`100` range.

### US-SI-03 — Drive multi-strategy terms explicitly

**Story:** As a scoring-only integrator, I want deterministic search-term generation, so that I can
drive recall explicitly instead of assuming it is a pipeline stage.

**Acceptance criteria:**

- `generate_search_terms("litigation")` returns exactly
  `["litigation", "litigation practice", "litigation service"]`.
- Calling the helper does not invoke `MatchPipeline` or an optional dependency.

## Reconciliation integrator (RI)

### US-RI-01 — Block Action versus Auction

**Story:** As a reconciliation integrator, I want the seeded alias blocklist to reject the homonym
`Action` → `Auction`, so that a fuzzy spelling match cannot become a concept tag.

**Acceptance criteria:**

- The blocklist returns a blocking decision for surface `Action` and the `Auction` concept.
- The accepted reconciled result contains no `Auction` candidate sourced from `Action`.

### US-RI-02 — Demote place-name traps

**Story:** As a reconciliation integrator, I want place-name gates on substantive matching paths,
so that generic legal terms do not become locations.

**Acceptance criteria:**

- The README regressions `Slovenia` at `99` and `Presumptions` → `Northern Mariana Islands` at
  `90` are not accepted as substantive matches.
- Gate decisions remain observable so a consumer can preserve location-valid paths separately.

### US-RI-03 — Reject law mapped to Delaware

**Story:** As a reconciliation integrator, I want shared label resolution to reject `law` →
`Delaware`, so that a generic legal word does not resolve to a place.

**Acceptance criteria:**

- Resolving `law` with `LabelResolver` does not return `Delaware` as an accepted result.
- Any surviving result carries its branch and calibrated `0`–`100` score.

## Annotation-app developer (AA)

### US-AA-01 — Review confidence and verdicts

**Story:** As an annotation-app developer, I want annotations with confidence and per-tag verdicts,
so that a reviewer can assess each proposed concept independently.

**Acceptance criteria:**

- An annotation exposes its concept tags and each tag's public confidence value.
- A per-tag `TagVerdict` record is constructed through the public model, and the public
  lifecycle operations (`promote`, `reject`, `restore`) change only the selected annotation's
  observable state. (The library ships the verdict model, not an operation that attaches a
  verdict to a tag; that remains a consumer-side step, see the report's follow-up note.)

### US-AA-02 — Reject and restore tags

**Story:** As an annotation-app developer, I want reject and restore lifecycle operations, so that a
reviewer's correction remains reversible.

**Acceptance criteria:**

- Rejecting a synthetic tag makes its rejected state observable.
- Restoring the same tag returns it to the active state without replacing its concept identity.

### US-AA-03 — Persist notes and derive insights

**Story:** As an annotation-app developer, I want notes, feedback storage, and insight helpers, so
that repeated reviewer corrections can inform later matching policy.

**Acceptance criteria:**

- A synthetic reviewer note round-trips through the public feedback store.
- Insight output is deterministic for identical stored feedback.

## LLM-judge integrator (LJ)

### US-LJ-01 — Supply a provider-neutral judge

**Story:** As an LLM-judge integrator, I want to implement `Judge.complete(system, user)`, so that I
can bring my own model, key, and spend policy.

**Acceptance criteria:**

- The public judge seam accepts an object implementing `complete(system, user) -> str`.
- The library itself reads no provider key and makes no network call in the zero-key path.

### US-LJ-02 — Parse hardened model output

**Story:** As an LLM-judge integrator, I want hardened JSON parsing, so that malformed rows and
markdown fences do not crash my matching pipeline.

**Acceptance criteria:**

- A fenced JSON object is stripped and parsed through the public helpers.
- A non-numeric `adjusted_score` row is dropped, while numeric scores are clamped to `0`–`100`
  before verdict enforcement.

### US-LJ-03 — Degrade without a judge

**Story:** As an LLM-judge integrator, I want deterministic candidates when no judge is configured,
so that offline operation remains useful.

**Acceptance criteria:**

- With no `Judge`, candidates pass through unchanged and the pipeline does not crash.
- `match(..., run_judge=True)` returns exactly the same candidates as `run_judge=False`,
  deterministically across two calls.
- No returned candidate carries a `verdict` attribute; the absence tells consumers that no model
  ran.

## Ontology and spec maintainer (OM)

### US-OM-01 — Decompose the proposed-heading example

**Story:** As an ontology and spec maintainer, I want compound headings decomposed into real sibling
labels, so that ontology lookup can score each named concept.

**Acceptance criteria:**

- `decompose("Proposed Findings of Fact and Conclusions of Law")` returns exactly
  `["Proposed Findings of Fact and Conclusions of Law", "Proposed Findings of Fact",
  "Proposed Conclusions of Law"]`.
- The README ontology resolves the two decomposed sibling labels to distinct concepts.

### US-OM-02 — Expand a genuine shared tail

**Story:** As an ontology and spec maintainer, I want a genuine elided head noun restored, so that
both siblings in a coordinated area-of-law heading can be found.

**Acceptance criteria:**

- `decompose("Antitrust and Securities Law")` returns exactly
  `["Antitrust and Securities Law", "Antitrust Law", "Securities Law"]`.
- `Antitrust Law` and `Securities Law` each resolve in the README ontology.

### US-OM-03 — Expose the documented shared-tail over-fire

**Story:** As an ontology and spec maintainer, I want the prepositional-tail heuristic's noise to
remain explicit, so that scoring—not silent decomposition drift—filters it.

**Acceptance criteria:**

- `decompose("Findings of Fact and Conclusions of Law")` returns exactly
  `["Findings of Fact and Conclusions of Law", "Findings of Fact Law", "Conclusions of Law"]`.
- `Findings of Fact Law` reaches only a Findings-of-Fact concept already produced by the first
  sibling, or reaches nothing.
- It never reaches a Conclusions-of-Law concept and any noise candidate remains ungated.

## Public synthetic-eval operator (EO)

### US-EO-01 — Load a versioned synthetic cohort

**Story:** As a public synthetic-eval operator, I want corpus and no-match slices verified against a
manifest, so that a score is tied to immutable public inputs.

**Acceptance criteria:**

- A test-local manifest verifies the SHA-256 hashes of its test-local JSONL slices.
- A hash mismatch fails closed without exposing a matching surface in the error.

### US-EO-02 — Score only eligible synthetic rows

**Story:** As a public synthetic-eval operator, I want the public lane to separate scoreable,
needs-review, and intentional no-match rows, so that each cohort is measured under its documented
contract.

**Acceptance criteria:**

- A small test-local corpus includes deterministic, human, needs-review, and intentional no-match
  rows using the documented schema.
- Needs-review rows stay visible but do not enter scoring, and intentional no-match rows remain in
  their separate slice.

### US-EO-03 — Run leak-safe and deterministically

**Story:** As a public synthetic-eval operator, I want test-local metadata and leak-check inputs, so
that the public lane can run end to end without owner-only artifacts.

**Acceptance criteria:**

- The scenario creates its corpus, manifest, public metadata, and salt under `tmp_path`.
- Repeating the scoring payload with `PYTHONHASHSEED=0` produces byte-identical output.

## Release maintainer (RM)

### US-RM-01 — Import the core without optional extras

**Story:** As a release maintainer, I want the public package to import when optional dependencies
are blocked, so that the core wheel remains pure Python apart from `pydantic`.

**Acceptance criteria:**

- A fresh interpreter can import `folio_resolve` while imports of `spacy`, `faiss`,
  `sentence_transformers`, `numpy`, and `folio` raise `ImportError`.
- The same interpreter runs the README quick-start match and ranks `Arbitration Rules` first.

### US-RM-02 — Expose version and extras independently

**Story:** As a release maintainer, I want to inspect the package version and optional-extra
availability, so that I can report the environment tested without importing a heavy dependency.

**Acceptance criteria:**

- `folio_resolve.__version__` equals the project version declared in `pyproject.toml`.
- Availability is reported independently for the `folio`, `spacy`, and `embedding` extras.

### US-RM-03 — Guarantee cross-process determinism

**Story:** As a release maintainer, I want public results compared across hash seeds, so that
consumer snapshots remain byte-identical.

**Acceptance criteria:**

- Identical public-entry-point input under multiple `PYTHONHASHSEED` values produces byte-identical
  serialized output.
- Tie-heavy results use stable ordering rather than hash iteration order.

## README promise coverage

| R3 README promise | Owning story IDs | Report link |
|---|---|---|
| Exact-score example (`99.0`, `88.0`, same first-ranked concept) | US-SI-01 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Proposed-heading and genuine shared-tail decomposition examples | US-OM-01, US-OM-02 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Shared-tail over-fire on a prepositional tail | US-OM-03 | [2026-09-02 report](2026-09-02-uat-report.md) |
| `Action` versus `Auction` guard | US-RI-01 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Place-name demotions (`Slovenia`, `Northern Mariana Islands`) | US-RI-02 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Rejected `law` → `Delaware` mapping | US-RI-03 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Quick-start ontology, pipeline match, and domain prior | US-PI-01, US-PI-02 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Specificity-penalty example (`67.5` at full penalty) | US-SI-02 | [2026-09-02 report](2026-09-02-uat-report.md) |
| Byte-identical determinism guarantee | US-RM-03 | [2026-09-02 report](2026-09-02-uat-report.md) |
