# folio-resolve

**The shared FOLIO source-text→concept matching engine.** One pinned, MIT-licensed Python library
that maps arbitrary source text (book headings, deposition transcripts, intake narratives, task
titles) to concepts in [FOLIO](https://folio.openlegalstandard.org) — the open legal ontology of
18,000+ concepts.

> This is a **lift-and-improve** extraction. The matching intelligence already existed across
> `folio-mapper`, `folio-enrich`, and `folio-insights` — but it had **diverged** (enrich literally
> forked mapper's scorer) and was informally shared through a fragile `sys.path` hack. `folio-resolve`
> is the single source of truth those repos now pin, plus the capabilities the recorded failures demanded.

---

## Why this exists

Three repos independently built the same engine and drifted apart:

- **folio-mapper** wrote the canonical, word-order-invariant scorer + a 4-stage pipeline + a FAISS embedding index.
- **folio-enrich** *copied* mapper's scorer (its `search.py` says "ported from folio-mapper" in the code) and added an Aho-Corasick entity ruler, a reconciler, a domain-prior judge, and a feature-rich annotate/feedback UI.
- **folio-insights** `sys.path`-imports **both** siblings at runtime — documented as fragile.

A book-annotation review (Ch02) then surfaced failures that no amount of local patching would fix:
place-names over-scoring (Slovenia → 99 units), homonyms (Action ≠ Auction), conjoined compound
headings that match nothing, metadata tagged as substance, and pure-semantic maps with no shared
label token (Presumptions → Litigation Burdens of Proof). Those became this library's new capabilities.

## What it does

| Capability | Module | Solves |
|---|---|---|
| Word-order-invariant relevance scoring | `scoring` | "arbitration rules" and "rules of arbitration" both reach *Arbitration Rules* |
| Multi-strategy search-term generation + legal expansions | `scoring` | recall on paraphrases (a helper the consumer drives — see the note below) |
| Pure-Python Aho-Corasick entity ruler | `entity_ruler`, `matching` | trusted exact-label spans, no spaCy/C deps |
| Candidate reconciliation (ruler + LLM + semantic) | `reconciler` | one clean candidate set with provenance |
| **Span decomposition** (conjunction split + shared head/tail) | `decompose` | "**Proposed** Findings of Fact **and** Conclusions of Law" → both siblings |
| **Place-name / short-label gates** | `gates` | kills Slovenia→99 and Presumptions→Northern Mariana Islands@90 |
| **Alias/homonym blocklist** | `blocklist` | deterministic Action ≠ Auction guard |
| **Metadata/front-matter exclusion** | `sources` | never tag the copyright page |
| **Multi-tag domain prior** (auto-suggest + validate/add) | `domain_prior` | Defenses → *Litigation* Defenses; corpus carries many subjects |
| **Shared label→IRI resolution** (decompose-first, calibrated 0–100 bar, branch-carrying) | `resolve` | every consumer resolves identically; `law`→*Delaware*@90 no longer accepted |
| **Score calibration** (weak-band recalibration) | `calibration` | verdict-labeled score→P(correct) fit |
| **Lemma-key index augmentation** (build-time spaCy, cached JSON) | `lemma` | singular *agreement* reaches plural-labelled *Agreements* |
| LLM judge interface + domain-prior prompts | `judge` | context-aware disambiguation, verdict enforcement |
| **Annotate primitives** (confidence, per-tag verdicts, notes, reject/restore, insights) | `annotate` | the self-improving feedback loop, as a library |
| 4-stage pipeline: filter → expand → rank → judge | `pipeline` | the build-once-use-many entry point |

### Reading that table precisely

Four rows mean slightly less than a quick read suggests. Consumers pin this library, so the
exact shape matters more than the headline:

- **"Word-order invariant" is a property of the *overlap*, not of the final number.**
  `content_words` reduces both phrasings to the same set, so both reach the concept — but the
  scorer also has exact-string and substring fast paths that word order does reach.
  `"arbitration rules"` scores **99.0** against *Arbitration Rules* (exact string), while
  `"rules of arbitration"` scores **88.0** (pure overlap). Same concept, same rank in practice;
  not the same score. Consumers that threshold on an absolute value should know which path fired.
- **The folio-python provider is a recall source, not a scoring authority.** Every label-search
  candidate it returns is re-scored by this library's own scorer, so consumers see the same
  0–100 relevance scale whether they use `FolioPythonProvider` or `InMemoryOntology`. This can
  change scores, ranks, result membership, and thresholded `MatchPipeline` output. When migrating,
  revalidate `MatchPipeline.score_floor`, score calibration, and committed ranking snapshots.
- **`generate_search_terms` is a helper you call, not a pipeline stage.** It produces the
  sub-phrases, content words, and `LEGAL_TERM_EXPANSIONS` suffixes ("litigation" → "litigation
  practice", "litigation service"), and it is exported for exactly that use — but
  `MatchPipeline` does **not** call it. The pipeline's `filter` stage runs one
  `search_by_label(surface_term)`; its `expand` stage runs `decompose` plus the optional semantic
  index. Drive the multi-strategy search yourself if you want that recall:
  `[pipe.match(t) for t in generate_search_terms(heading)]`.
- **The shared-*tail* rule is a heuristic, and it over-fires on prepositional tails.** It is
  right when the tail is a genuine elided head noun — `"Antitrust and Securities Law"` →
  `["Antitrust Law", "Securities Law"]` — and wrong when the tail word is the object of a
  preposition: `"Findings of Fact and Conclusions of Law"` emits `"Findings of Fact Law"`
  alongside the correct `"Conclusions of Law"`. That extra string is noise the scorer filters
  (nothing matches it), not a wrong tag, and a leading shared head suppresses the tail rule
  entirely — which is why the **Proposed** variant in the table decomposes cleanly.

### Determinism is a guarantee, not a coincidence

Identical input produces byte-identical output in any process, and several consumers commit
golden/snapshot baselines that depend on it. That is enforced, not assumed: `tests/test_determinism.py`
re-runs the public entry points in subprocesses under different `PYTHONHASHSEED` values and
compares them byte-for-byte. In-process assertions cannot catch this class — within one process a
set's iteration order is fixed, so the bug is invisible. Anything that reaches a caller-visible
result is therefore totally ordered: ties break on a stable field (IRI, label, filename), never on
a hash.

## Personas

- **Book-extraction pipelines** (`folio-insights`, `books`) — offline, CLI-first tagging of treatise text.
- **Intake / matter classifiers** (`alea-intake`, `mootloop`) — narrative → FOLIO concepts for routing.
- **Interactive mappers** (`folio-mapper`, `clio-skills`) — a UI over the library's ranked candidates.
- **The FOLIO ontology team** — a reference implementation of "good" source-text→concept matching.

## Use cases

- Tag a chapter of a litigation treatise with FOLIO concepts, with a litigation domain prior so
  "Defenses" resolves to *Litigation Defenses*, not the generic sense.
- Resolve a compound heading like *"Proposed Findings of Fact and Conclusions of Law"* to the two
  sibling concepts it actually names.
- Suggest the subject tags for a corpus ("Personal Injury Depositions" → *Personal Injury* +
  *Deposition*) and let a human validate/add via a FOLIO taxonomy-tree picker.
- Feed a reviewer's `wrong` verdict on a homonym straight into the alias blocklist so the mistake
  never recurs — the self-improving loop.

## Install

```bash
uv add folio-resolve                 # core (pure-Python, only pydantic)
uv add "folio-resolve[folio]"        # + folio-python live ontology adapter
uv add "folio-resolve[embedding]"    # + sentence-transformers / faiss for the semantic path
uv add "folio-resolve[spacy]"        # + lemma-key index augmentation (build-time only)
```

The **core is pure-Python** — the scorer, decomposition, gates, blocklist, domain-prior, reconciler,
calibration, and annotate primitives depend on nothing heavier than `pydantic`. FAISS,
sentence-transformers, spaCy, and folio-python live behind `Protocol` seams with working pure-Python
defaults, so the whole test suite runs with no model downloads and no network.

### Lemma-key augmentation (`[spacy]` extra, v0.2.0)

The 2026-07 ruler shootout ([bench/RESULTS.md](bench/RESULTS.md)) showed the recall edge folio-enrich's
spaCy ruler appeared to have lives in its **label index**, not its engine: lemma keys let the singular
surface *agreement* reach the plural-labelled concept *Agreements* (+200/200 lemma-gold hits, +698
corpus matches). v0.2.0 promotes that indexing here, engine-agnostic:

```python
from folio_resolve import FOLIOEntityRuler, augment_labels

labels = provider.all_labels()                      # any OntologyProvider
labels = augment_labels(                            # adds lemma_preferred / lemma_alternative keys
    labels,
    cache_dir="~/.folio-resolve/lemmas",            # cached by ontology hash + LEMMA_VERSION
    ontology_hash=owl_content_hash,
    on_missing_spacy="skip",                        # no [spacy] extra -> un-augmented index, no crash
)
ruler = FOLIOEntityRuler()
ruler.load_patterns(labels)                         # pure-Python matching, zero heavy deps
```

**spaCy is needed only at index-build time** (computing what each label's lemma is; requires the
`[spacy]` extra plus `python -m spacy download en_core_web_sm`). Steady-state consumers load the
cached JSON lemma map and never import spaCy. Without the extra: the default raises a clear
`SpacyNotInstalledError`; `on_missing_spacy="skip"` degrades to the un-augmented index. Use it
whenever documents refer to concepts in the singular while the ontology labels them in the plural
(FOLIO does, pervasively); skip it for exact-vocabulary corpora. Legal pluralia tantum (*damages*,
*proceedings*, *wills*, …) are denylisted per-ontology via `OntologySpec.behavior.lemma_denylist`.

### Judge transport hardening (v0.2.1)

`parse_judge_json` now survives everything models actually emit — ```` ```json ```` fences (a new
exported `strip_markdown_fences` does the stripping), a non-list `judged`, non-dict rows, a
non-object payload, `None` input, non-string `reasoning`, and an `adjusted_score` that is
non-numeric (`"high"` → the row is dropped, not an exception) or out of range (clamped to 0-100
*before* verdict enforcement, so a `"penalized"` verdict cannot escape the scale). Surfaced by the
folio-mapper migration: mapper is the donor of these verdict rules and had kept a local parse loop
precisely because the library's was weaker than the code it was lifted from.

### Consumer-driven API additions (v0.3.0)

Three gaps the **alea-intake** migration recorded (SCHEDULE.md row 4) are closed. All are
**additive** — default behavior is bit-identical, pinned by a golden no-drift table in
`tests/test_scoring.py` so folio-mapper's and folio-enrich's committed captures stay valid.

- **`compute_relevance_score` is type-defensive.** Every text argument is coerced: a `None`
  `preferred_label` (folio-python returns one for concepts with no preferred label), a test
  double, a number, a non-`str` synonym entry — all read as *absent* instead of raising
  `TypeError` out of `re.findall`. A non-`str` `label` scores `0.0`. Consumers can drop their
  boundary coercion shims. Same spirit as the v0.2.1 `parse_judge_json` hardening.
- **`PlaceNameGate` takes a consumer vocabulary.** `extra_tokens` (place names, matched against
  label tokens *and* the whole label so multi-word names work), `extra_markers` (substring
  phrasings — `("city of", "republic of")` catches *City of Exampleton* whatever it's called),
  and `extra_branch_markers` (extra governed branches). A `place_tokens` property exposes the
  merged vocabulary. This retires the parallel local backstops consumers had to carry:

  ```python
  gate = PlaceNameGate(
      min_signals=2,
      extra_tokens={"macedonia", "rize", "europe", "north america"},
      extra_markers=("city of", "republic of", "province of"),
  )
  ```

- **The specificity penalty is weightable.** `compute_relevance_score(..., specificity_penalty=w)`
  scales the "candidate is more specific than the query" haircut: `1.0` is the historical
  default, `0.0` disables it. Consumers whose queries are *shorter than their targets by
  construction* (a 1-2 word claim name against a 3-5 word FOLIO label — *Habitability* →
  *Breach of Warranty of Habitability*, scored 67.5 at full penalty) can damp it rather than
  fork the scorer. Consumers that map taxonomy nodes, where an over-specific target IS an
  error, leave it at `1.0`.

Adopting `PlaceNameGate` is a **per-path** decision, not a per-repo one — see
[docs/migration/SCHEDULE.md](docs/migration/SCHEDULE.md#adopting-placenamegate-is-a-per-path-decision).

## Bring Your Own Key (BYOK)

`folio-resolve` is **key-agnostic** — it never reads an env var, instantiates a provider SDK, or makes
a network call on its own. The **zero-key deterministic core** (ruler, scoring, decomposition, gates,
blocklist, metadata exclusion, calibration, annotate) runs fully offline and free. Three optional stages
accept a provider through a `typing.Protocol` seam you fill with an object you construct:

| Stage | Protocol | Buys you | Absent → |
|---|---|---|---|
| **Judge** | `Judge.complete(system, user) -> str` | context-aware disambiguation + verdict enforcement | items pass through **unjudged** |
| **Embeddings** | `EmbeddingProvider` (`embed`/`embed_batch`/`dimension`) | semantic recall for no-shared-token maps | local `all-MiniLM-L6-v2` default (no key), or skipped |
| **Domain-prior suggestions** | `DomainPriorSuggester(ontology)` | auto-suggest corpus subject tags | supply tags manually |

You own the key, the vendor (OpenAI / Gemini / Anthropic / local), and the spend. The library ships the
judge **prompt builders** and **deterministic verdict enforcement**; you supply only the raw model call.
Graceful degradation is the default — no key means deterministic-only output with items marked
`unjudged`, never a crash. Reference cost: **≈ $0.12 / chapter on `gemini-2.5-flash-lite`** (~1,875
calls, ~652K tokens, 464 units). Full guide, env-var conventions, and a minimal wiring example per
vendor: **[docs/BYOK.md](docs/BYOK.md)**.

## Quick start

```python
from folio_resolve import InMemoryOntology, Concept, MatchPipeline, DomainPrior

ontology = InMemoryOntology([
    Concept(iri="R-defenses", label="Litigation Defenses", branch="Objectives"),
    Concept(iri="R-arb", label="Arbitration Rules", branch="Service"),
])
pipe = MatchPipeline(ontology=ontology)

# word-order-invariant
pipe.match("rules of arbitration")        # -> Arbitration Rules

# domain prior threads a subject into the (optional) judge
prior = DomainPrior.from_manifest_subjects("treatise", [("R-lit", "Litigation")])
pipe.match("Defenses", domain_prior=prior)
```

## Development

```bash
uv sync --extra dev
uv run pytest          # full suite, pure-Python, no network
uv run mypy src        # strict
uv run ruff check
```

## License & attribution

MIT — see [LICENSE](LICENSE). Every extracted component and dependency is logged in
[THIRD-PARTY.md](THIRD-PARTY.md). The migration schedule for consumer repos is in
[docs/migration/SCHEDULE.md](docs/migration/SCHEDULE.md).
