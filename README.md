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
| Word-order-invariant relevance scoring | `scoring` | "arbitration rules" = "rules of arbitration" |
| Multi-strategy label search + legal expansions | `scoring`, `pipeline` | recall on paraphrases |
| Pure-Python Aho-Corasick entity ruler | `entity_ruler`, `matching` | trusted exact-label spans, no spaCy/C deps |
| Candidate reconciliation (ruler + LLM + semantic) | `reconciler` | one clean candidate set with provenance |
| **Span decomposition** (conjunction split + shared head) | `decompose` | "Findings of Fact **and** Conclusions of Law" → both siblings |
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
