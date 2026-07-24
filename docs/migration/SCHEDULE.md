# folio-resolve — Consumer Migration Schedule

Damien approved **opportunistic** migration (each repo migrates as it next touches FOLIO matching)
**plus** this written schedule, and asked to be **reminded which repo is next as each migration
completes**. Status legend: ✅ done · 🟡 partial · ▶️ next · ⏳ queued · ➖ excluded.

> **NEXT UP: `clio-skills`.** (alea-intake migrated 2026-07-24 — the heaviest external consumer
> now takes its Stage-2 label scorer and its claim-fitness place gate from the library; classified
> delta 15 intended fixes / 0 regressions / 36 neutral, five canaries green, suite 1266 green.)

| # | Repo | Status | How it matches FOLIO today | What migration entails | Effort |
|---|---|---|---|---|---|
| 1 | **folio-insights** | 🟡 partial 2026-07-24 | 4-path tagger. Its **matching** now comes from the pinned library, but three `sys.path` bridges into folio-enrich / folio-mapper survive: `folio_bridge` (FolioService, EmbeddingService, the text normalizer), `mapper_bridge` (tabular Excel/CSV/TSV ingest), `ingestion_bridge` (format detection + multi-format text extraction). `config.folio_enrich_path` still points at `../folio-enrich/backend`, and six modules still import through the bridges (`folio_tagger`, `knowledge_classifier`, `boundary_detection`, `ingestion`, discovery `folio_mapping`, discovery `hierarchy_construction`) | **Done (matching seams):** the resolver, the entity ruler, the reconciler, the gates, and the judge + calibration are all consumed from `folio-resolve`; every `folio_matching` reference is gone (package rename absorbed); suite 992 green. **Not done (service tier):** the three bridges. They carry ontology/service-tier plumbing — a FOLIO/embedding service singleton, a normalizer, format-detection ingest, tabular parsing — not matching, so the library does not cover them today. Retiring them is a separate decision (grow the library's ontology/ingest surface, or vendor the pieces) and is **not** blocked on any matching work | Matching done; bridges open |
| 2 | **folio-enrich** | 🟡 partial 2026-07-16 (Stage 1) · scoped 2026-07-24 (Stage 2) | Its `folio/search.py` was literally "ported from folio-mapper"; owns the reconciler, entity ruler, domain-prior judge, feedback/annotate UI | **Done (Stage 1):** the scorer, stopwords, `LEGAL_TERM_EXPANSIONS`, search-term generation, `LabelResolver` as the primary resolution path, and `PlaceNameGate` + `AliasBlocklist` (adopted **globally** — enrich tags prose, so a place/agency latch is a false positive on every path) all come from the library; delta 5 intended fixes / 0 regressions, place mis-maps 3 → 0. **Not done:** `search.py` still exists (284 lines). Stage 2 measured why — see the findings below: it is no longer a *fork of library code*, it is folio-python recall orchestration the library has no equivalent for, and deleting it costs 87.5% of the ranked candidate set. Stage-0 baseline + two canaries are committed in `backend/migration/` and will gate the swap when the library can take it | Real |
| 3 | **folio-mapper** | ✅ 2026-07-24 | The canonical scorer + 4-stage pipeline + FAISS index (the donor of most of this library) | Done: scorer/stopwords/expansions/search-term generation + judge verdict rules + calibration prompt now consumed from the library; ontology-shaped code (folio-python gathering, branch logic, FAISS, LLM providers) stays. Golden-baseline harness in `backend/migration/` proves an EMPTY delta. `PlaceNameGate` deliberately NOT adopted — mapper maps jurisdictions on purpose | Real |
| 4 | **alea-intake** | ✅ 2026-07-24 | Heaviest external consumer: 3-stage cascade in `services/folio/concept_resolver.py` (embedding + hand-rolled word-overlap + LLM), FAISS/pgvector backends, `analysis/semantic_fit.py` LLM judge, `folio/term_expansions.py` ("ported from folio-mapper") | Done: the hand-rolled Stage-2 label scorer is deleted for `compute_relevance_score`, and `semantic_fit.is_geographic_concept` is backed by `PlaceNameGate` — **adopted here** (a claim is never a place *or an agency*) but scoped to claim fitness only, NOT to the general resolver, which resolves jurisdictions on purpose. Consumer seams stay local: lay-language expansions ("fired" → "wrongful termination"), the narrative stopword list, the 3-stage weighted combine, pgvector/FAISS. `semantic_fit` did **not** move into the lib — its LLM tier is a claim-context judge on alea's own structured-output client, not a transport the library covers. Golden-baseline harness in `backend/migration/`; classified delta 15 intended fixes / 0 regressions / 36 neutral | Real (largest surface) |
| 5 | **clio-skills** | ▶️ next | Polyglot: TS `fuzzy-match.ts` (bespoke Dice) + Python subprocess bridges (`folio_embedding.py` FAISS `IndexFlatIP`, `folio_bridge.py` rapidfuzz) | Replace the Dice scorer + both Python bridge scripts. Because it's Next/TS, either call a future `/match` service or keep one thin Python bridge that imports the library | Real (language split) |
| 6 | **mootloop** | ⏳ queued (greenfield) | None yet — `taskspec.py` has placeholder `folio_iri`/`folio_label`; resolution is deterministic keyword in v1, LLM concept-resolution lands in FE-3 | Nothing to rip out; wire `folio-resolve` directly when FE-3 lands. The ideal first *native* consumer | Adopt |
| 7 | **generative-folio** | ⏳ optional | Uses rapidfuzz only for QA dedup of *generated* concepts (`qa/detectors.py`) — not inbound matching | Optional: adopt the shared token scorer for dedup. Low value | Optional |
| — | **folio-api** | ➖ reference/donor | Already a FOLIO match service: `/search/label` (folio-python fuzzy) + ~18 `/llm/*` classifiers; uses pinned `folio-python` | No correctness change needed. It defines the eventual `/match` contract; swaps direct calls for the library opportunistically. Candidate host if/when the deferred `/match` service is built | Light-shim |
| — | **books** | ➖ excluded | "folio" = physical page numbers, not the ontology | N/A — naming collision | None |
| — | **book-indexer** | ➖ excluded | "folio" = physical page numbers; index-term extraction, not FOLIO | N/A — naming collision | None |

## Reminder protocol (per Damien's directive)

As each migration lands, the executing agent posts a one-line reminder naming the next repo:

- 🟡 **folio-insights migrated *for matching* → NEXT UP: folio-enrich** (retire its forked
  `search.py`). Corrected 2026-07-24 after the folio-insights merge landed: only the matching
  seams moved. The `folio_bridge` / `mapper_bridge` / `ingestion_bridge` `sys.path` hacks and
  `config.folio_enrich_path` are still there, so row 1 is **partial**, not done — see the row for
  exactly what did and did not move.
- ✅ **folio-enrich migrated (2026-07-16, Stage 1: deterministic core; ruler kept per Damien; fallback kept) → NEXT UP: folio-mapper** (collapse Python backend into the library).
- ✅ **folio-mapper migrated (2026-07-24, deterministic core + judge verdict policy; PlaceNameGate
  deliberately not adopted; delta empty) → NEXT UP: alea-intake** (biggest rewrite; exercises
  embed+label+LLM+judge).
- 🟡 **folio-enrich Stage 2 scoped (2026-07-24): the `search.py` retirement is LIBRARY work,
  not consumer work** — golden baseline + candidate-recall / fork-parity canaries committed;
  the swap itself waits on a `RecallOntology` protocol + `MultiStrategyRecall` engine + a
  release. Does not change the queue order.
- ✅ **alea-intake migrated (2026-07-24, Stage-2 label scorer + claim-fitness `PlaceNameGate`;
  gate deliberately NOT applied in the general resolver; 15 intended fixes / 0 regressions)
  → NEXT UP: clio-skills** (validate the polyglot/service path).
- ⬜ clio-skills migrated → NEXT UP: mootloop FE-3 (greenfield native adoption).

## Findings from the folio-mapper migration (2026-07-24)

- **`parse_judge_json` was weaker than its donor — FIXED in v0.2.1 (unpublished).** folio-mapper
  could not delete its local judge parse loop because the library version did not strip markdown
  fences, did not clamp `adjusted_score` to 0-100 (a `"penalized"` verdict could escape the
  scale), and *raised* `ValueError` when a model answered `"high"` instead of `85`. All three are
  now in `folio_resolve.judge` (plus non-list `judged`, non-dict rows, non-object payloads,
  `None` input and non-string `reasoning`), with `strip_markdown_fences` exported.
  **Release step:** `folio-resolve 0.2.1` is committed but NOT published to PyPI. Once it is,
  folio-mapper can delete `stage3_judge._parse_judge_json` / `_strip_markdown_fences` and bump its
  pin from `>=0.1.0` to `>=0.2.1`; the same applies to any consumer duplicating that transport.

- **`generate_search_terms` output order is nondeterministic — open.** It iterates a `set` of
  content words when emitting `LEGAL_TERM_EXPANSIONS` compounds and when length-sorting content
  words, so term ORDER varies between processes under PEP 456 hash randomization. In folio-mapper
  that order feeds candidate insertion order, which breaks ties among equally scored candidates,
  which per-branch caps then truncate — two runs of identical code differed by 6 rows out of 200.
  The behavior is inherited from the donor (it predates this library), so it is not a regression,
  but reproducible output is worth having. Left OPEN deliberately: sorting the iteration shifts
  tie-breaks for **every** consumer at once and would invalidate folio-enrich's and
  folio-mapper's committed golden captures, so it wants its own versioned change with each
  consumer's baseline recaptured. Consumers that need reproducibility today can pin
  `PYTHONHASHSEED=0` (folio-mapper's migration harness does).

## Findings from the alea-intake migration (2026-07-24)

> **All four findings below are CLOSED in v0.3.0 (unpublished).** See
> [Adopting `PlaceNameGate` is a per-path decision](#adopting-placenamegate-is-a-per-path-decision)
> for the fourth. **Release step:** `folio-resolve 0.3.0` is committed but NOT published to PyPI
> (0.1.0 is still the only published version, so 0.2.0, 0.2.1 and 0.3.0 are all pending one
> release). Once published, alea-intake can delete its `_as_text` boundary coercion and its
> `_GEOGRAPHIC_LABEL_EXACT` / `_GEOGRAPHIC_LABEL_MARKERS` backstop (pass them as `extra_tokens`
> / `extra_markers`), folio-mapper can delete its local judge parse loop, and every consumer
> can move its pin from `>=0.1.0` to `>=0.3.0`.

- **`compute_relevance_score` is not type-defensive — CLOSED in v0.3.0.** It passed `preferred_label`
  straight into `re.findall`, so a `None` (which folio-python genuinely returns for concepts
  with no preferred label) or a test double's `MagicMock` raises `TypeError` instead of being
  ignored. alea-intake coerces at its own boundary rather than patching the library. Same
  spirit as the v0.2.1 `parse_judge_json` hardening that folio-mapper prompted: the library
  should tolerate what its real callers actually hand it. **v0.3.0:** every text argument is
  coerced — non-`str` `label` / `query_full` / `definition` / `preferred_label` / synonym
  entries, and non-`str` members of `query_content`, all read as absent; a non-`str` `label`
  scores `0.0`. `tokenize` is hardened at the `re.findall` site too, so nothing below it can
  raise on a test double.
- **`PlaceNameGate`'s place-token set is private and closed — open.** `_PLACE_NAME_TOKENS` is a
  module-level frozenset with no constructor hook, so a consumer that knows additional
  over-scoring places (alea-intake carries *Macedonia*, *Rize*, the continents, and the
  "City of X" / "Republic of X" phrasings from its BUG-21 incident log) must keep a parallel
  local backstop — exactly the fork the library exists to prevent. **v0.3.0:**
  `PlaceNameGate(extra_tokens=…, extra_markers=…, extra_branch_markers=…)`. `extra_tokens`
  matches against the label's tokens *and* the whole normalized label (so multi-word names like
  *north america* work); `extra_markers` are substrings for productive phrasings (`"city of"`);
  `extra_branch_markers` extends the governed branch list for non-FOLIO ontologies. A
  `place_tokens` property exposes the merged vocabulary. Un-parameterized construction is
  unchanged.
- **The specificity penalty is calibrated for heading→label matching, not short-name→label.**
  alea-intake's dominant shape is a 1-2 word claim name against a 3-5 word FOLIO label
  ("Habitability" → *Breach of Warranty of Habitability*), which the penalty scores 67.5 — a
  27% haircut on what is a *correct* mapping, because FOLIO names things fully. For consumers
  whose queries are shorter than their targets by construction, the penalty runs the wrong way.
  Not a defect (folio-mapper maps taxonomy nodes, where an over-specific target IS an error),
  but the asymmetry is worth an option: a `specificity_penalty` weight argument would let a
  short-query consumer damp it without forking the scorer. **v0.3.0:**
  `compute_relevance_score(..., specificity_penalty=w)` scales the haircut — `1.0` is the
  historical default (bit-identical, pinned by a golden no-drift table), `0.0` disables it,
  `> 1.0` sharpens it (clamped so a score can never go negative).
- **The gate is a consumer-scoped decision, not a repo-scoped one.** Written up as migration
  guidance below.

## Adopting `PlaceNameGate` is a per-path decision

**Ask "which matching PATH?", never "does this repo want the gate?"** Three migrations have now
answered it three different ways, and all three were right:

| Precedent | Answer | Why |
|---|---|---|
| **folio-enrich** (2026-07-16) | **global yes** — wired into `search.candidate_vetoed`, so every candidate on every path passes the gate | It tags prose. A generic term latching a short place/agency label (`justice` → *U.S. Dept. of Justice*, `tax` → *U.S. Tax Court*) is a false positive on *every* path; the delta report shows 3 place/agency mis-maps → 0 with no named recovery dropped |
| **folio-mapper** (2026-07-24) | **global no** — deliberately not adopted; a PLACES-PRESERVED canary *enforces* its absence | It maps arbitrary taxonomies onto FOLIO. Places and jurisdictions are legitimate mapping targets, so the gate would delete correct output |
| **alea-intake** (2026-07-24) | **one path yes, one path no** — `PlaceNameGate` backs `semantic_fit.is_geographic_concept` (claim fitness) but is deliberately absent from `concept_resolver.resolve_concepts` | A legal *claim* is never a place or an agency (BUG-21), but the general resolver resolves jurisdiction and venue on purpose. Two canaries hold both halves: PLACE-REJECTED and PLACES-RESOLVABLE |

The decision procedure for the next migration:

1. **Enumerate the repo's matching paths** — not its repos, not its modules. A path is a
   question the consumer asks the ontology ("what concept does this heading name?", "does this
   claim fit this concept?", "which jurisdiction is this?").
2. **For each path, ask: can a correct answer to this question be a place, a jurisdiction, or a
   governmental body?** If yes, the gate must NOT govern that path. If no, adopt it there.
3. **Canary both directions.** A repo that adopts the gate on one path needs a canary that the
   gate fires there *and* a canary that the un-gated path still resolves places. One-sided
   canaries are how a global adoption sneaks in.
4. **Extend, don't fork.** If the path needs place names the library doesn't know, pass them as
   `extra_tokens` / `extra_markers` (v0.3.0) rather than keeping a local backstop.
5. **Record the answer in the row** so the next migration inherits the reasoning rather than
   re-deriving it.

## Findings from the folio-enrich Stage-2 scoping (2026-07-24)

Row 2's stated next step was "retire the forked `search.py`". Stage 0 of that retirement — a
`PYTHONHASHSEED=0` golden baseline with two new seams and two new canaries — is committed in
`folio-enrich/backend/migration/`. It measured the swap out of scope, for a reason worth
recording:

- **`search.py` is no longer a fork of this library.** Stage 1 already took the scorer,
  stopwords, expansions, search-term generation, the gates, the blocklist and `LabelResolver`.
  What remains is folio-python **recall orchestration**: 7-strategy candidate gathering
  (`search_by_label` / `search_by_prefix` / stem prefix / `search_by_definition`), expansion
  re-scoring, ancestor surfacing (`sub_class_of` to depth 3, decay `0.85^depth`), and enrich's
  own branch filter/colors. That is the same category folio-mapper's migration classified as
  "ontology-shaped code stays".
- **The library primary is already correct without it — but the fork carries the recall.**
  On the 24-row corpus, `LabelResolver` + gates resolve the right primary on **24/24** rows with
  the fork stubbed out. The *ranked candidate set* (`resolve_multi`, which the UI, the
  reconciler and every multi-candidate consumer read) collapses **120 → 15 (−87.5%)**, every
  term shrinking. A "retire by deleting" would have been a silent recall amputation; the new
  `candidate recall` canary fails it (exit non-zero) by design.
- **What the library must grow to take it.** (1) A `RecallOntology` protocol —
  `search_by_prefix`, `search_by_definition`, `parents_of(iri)` beside today's
  `search_by_label`, implemented by `FolioPythonProvider` and backed by `InMemoryOntology` for
  tests. (2) A `MultiStrategyRecall` engine holding gathering + re-scoring + expansion
  re-scoring + ancestor surfacing. This retires **two** forks at once — enrich's
  `multi_strategy_search` and folio-mapper's `search_candidates` are the same algorithm — which
  is exactly the kind of duplication this library exists to end. (3) A release.
- **A second, genuine fork surfaced: `AhoCorasickMatcher`.** enrich's
  `app/services/matching/aho_corasick.py` and `folio_resolve.matching.AhoCorasickMatcher` are
  the same contract; the library version is a deliberate pure-Python reimplementation of it.
  Swapping is a **speed trade**, not a correctness one — enrich's is the compiled
  `pyahocorasick` C extension, and published 0.1.0 still has the O(m²) `_resolve_overlaps` the
  ruler shootout flagged (the O(m log m) sweep landed after 0.1.0). Worth doing on ≥ 0.2.0 with
  a throughput measurement on the real 69K-pattern index; it is Damien's call, not a lane's.

## Deferred, revisit reminders

- **Recall engine in the library (`RecallOntology` + `MultiStrategyRecall`)** — the blocker for
  finishing row 2 and for deleting folio-mapper's `search_candidates`. Sized above; wants its
  own operation and a release.
- **Hosted `/match` service** — deferred per Damien; the library covers every current (Python)
  consumer. Reconsider when a non-Python or remote consumer (clio-skills TS, an external caller)
  needs it; folio-api is the natural host.
