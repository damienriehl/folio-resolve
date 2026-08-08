# AC Engine Shootout — pyahocorasick C extension (folio-enrich) vs pure Python (folio-resolve)

**Date:** 2026-08-05 · **Benchmark:** `bench/ac_engine_shootout.py` (deterministic, $0 LLM) ·
**Committed numbers:** `bench/ac_engine_runs.json` (4-run aggregate, every value below) and
`bench/ac_engine_summary.json` (one representative run, full detail) · raw per-engine captures in
`bench/results/`, gitignored

## Question

folio-enrich's `AhoCorasickMatcher` is backed by the compiled `pyahocorasick` C extension — a
**hard dependency** in `backend/pyproject.toml`. folio-resolve ships a pure-Python
reimplementation of the same contract. Keep the C extension, or take the library's? Damien's
answer to the framing question was *"measure first, then decide."* This document is the
measurement. The recommendation at the bottom is labelled as such and is his to overrule.

## Why every earlier number was void

Two real bugs in `folio_resolve/matching/aho_corasick.py` were fixed **today** (`78a8ca4`):

- `build()` was documented "Idempotent-safe" and was not — it folded the failure chain into
  `outputs` in place, so each rebuild re-folded already-folded lists and grew them superlinearly
  (10 → 20 → 35 → 56 → 84 entries over four rebuilds of a 4-pattern trie). `_resolve_overlaps`
  deduped the fallout, so results looked right; **any prior timing that rebuilt the trie was
  measuring a corrupted structure.**
- `search(case_sensitive=True)` matched **nothing at all** — patterns are keyed lowercase, but
  the flag walked the original-cased text against that lowercase trie.

Everything below was captured against `folio-resolve` `main` after that fix (`e4fe379`, gate
green at 556 tests, mypy + ruff clean), with folio-enrich's venv resolving `folio_resolve` to
`/home/damienriehl/Coding Projects/folio-resolve/src/folio_resolve/__init__.py` (editable onto
the working tree — asserted by the harness). **No earlier benchmark numbers are reused.**
A concurrent session advanced `main` to `6b2ff5b` mid-benchmark;
`git diff e4fe379..6b2ff5b -- src/folio_resolve/matching/ src/folio_resolve/entity_ruler.py` is
empty (a migration-doc commit), so the captures still describe HEAD.

## Setup

| | |
|---|---|
| Engines | `c` = `app.services.matching.aho_corasick.AhoCorasickMatcher` (folio-enrich, pyahocorasick **2.3.0**, compiled `.so`) · `py` = `folio_resolve.matching.aho_corasick.AhoCorasickMatcher` (pure Python, stdlib only) |
| Patterns | **68,693**, from **16,682 distinct FOLIO concepts** (69,368 raw label keys through `folio_resolve.entity_ruler.build_patterns`, i.e. production's stopword + min-length guards). Identical dict handed to both engines. |
| Corpus | folio-enrich's 22 synthetic demo documents — **156,876 chars**, median document **8,253 chars**. No matter data. |
| Python | 3.13.11, folio-enrich `backend/.venv` |
| Repetitions | **4 full independent runs**; within each run 5 build reps, 11 corpus-search reps, 5 per-document reps. Tables show **median across the 4 runs**, `[min–max across runs]`. |

**Statistical honesty.** This machine was running several concurrent agents throughout. Absolute
numbers are noisy — the run-to-run median of the walk timing moved ±40%. **The relative
comparison under identical conditions is what carries weight**, and where a ratio was unstable
across runs the per-run values are given (in `ac_engine_runs.json`, and inline below) so you can
see the spread rather than a single number dressed up as precision. Memory figures and parity
results were bit-stable across all four runs.

---

## 1. Correctness / parity — this outranks speed

### Verdict: **exact parity on the production path.** Two divergences exist, both off it.

**Full-corpus match-set diff (the number that decides swap-safety).** Both engines were run over
the whole 156,876-char corpus with the real 68,693-pattern FOLIO set, and their
`(start, end, pattern_id)` sets compared:

| | C engine | Pure Python |
|---|---|---|
| Raw matches before overlap resolution | 4,928 | 4,928 |
| Final spans | **4,914** | **4,914** |
| Spans in both | 4,914 | |
| C-only / Python-only | **0 / 0** | |

Identical in all 4 runs. Same spans, same matched concepts, byte-for-byte. **A swap on the
production path changes no output.**

### Divergence 1 — `case_sensitive=True`, and the C engine is the wrong one

Fixture: patterns `{"Court", "ruled"}`, text `"The Court ruled. the court adjourned. THE COURT reconvened."`

| Engine | `case_sensitive=True` result | Reading |
|---|---|---|
| Pure Python | `(4,9)→Court`, `(10,15)→ruled` | **Correct.** Matches the registered casing at offset 4; rejects lowercase `court` (21) and `COURT` (42). |
| C engine | `(10,15)→ruled`, `(21,26)→Court` | **Inverted.** *Misses* the actual `Court` at offset 4 and reports the *lowercase* `court` at 21 as a `Court` hit. |

The C engine's semantics are not "case sensitive" — they are "only matches text that happens to
be lowercase," because patterns are keyed lowercase by `add_pattern` while the flag walks the
original-cased text. This is the same bug class fixed in the library today; folio-enrich still
carries it.

**Live impact: none today.** `grep -rn "case_sensitive" --include=*.py` across folio-enrich
(excluding `.venv`) returns exactly two hits, both inside the method definition itself — the
parameter and its use. Zero call sites in production code or tests. It is a latent trap, not an
active bug.

### Divergence 2 — duplicate lowercase keys (payload differs, span does not)

Fixture: patterns `{"Court" → P-upper, "court" → P-lower}`, text `"The court met."`

| Engine | `pattern_count` | Match |
|---|---|---|
| C engine | 1 | `(4,9) → P-lower` — `pyahocorasick.add_word` overwrites by key; last write wins |
| Pure Python | 2 | `(4,9) → P-upper` — outputs list appends; identical-span dedup keeps first |

Same span, different attached value. `pattern_count` also differs *by definition*: the C engine
reports distinct automaton keys, the Python engine counts `add_pattern` calls.

**Live impact: none measured.** The real FOLIO index contains **zero** lowercase-collision
groups — concept labels 69,368 keys → 0 groups; property labels 265 → 0; `build_patterns` output
68,693 → 0. And all three folio-enrich call sites (`string_match_stage`, `llm_concept_stage`,
`property_matcher`) already dedupe on `.lower()` before adding.

### Everything else: identical

| Probe | Result |
|---|---|
| Case-insensitive search (the production path) | identical |
| Rebuild idempotence — 4 consecutive `build()` calls | both stable, identical spans (this is the bug fixed today; the library now matches the C engine here) |
| Empty pattern (`add_pattern("")`) | both accept and ignore it; identical spans |
| Nested/overlap/word-boundary fixture (4 patterns, contained + partial overlap + non-word-boundary) | identical spans |

---

## 2. Performance

### The structural finding that reframes the table

**The C extension only accelerates the automaton walk. Everything after the walk is Python in
both engines** — and the two shipped `_resolve_overlaps` implementations differ: folio-enrich
still has the full-rescan **O(m²)** version; folio-resolve replaced it with an **active-interval
sweep**. At corpus scale that difference dwarfs the engine difference:

| Phase, 156,876 chars / 4,928 raw matches | C engine | Pure Python |
|---|---|---|
| Automaton walk only (overlap step stubbed out) | **0.0221 s** [0.0206–0.0285] · 7.1M chars/s | 0.0481 s [0.0375–0.0523] · 3.3M chars/s |
| Its own `_resolve_overlaps` | **0.364 s** [0.347–0.377] | **0.0018 s** [0.0015–0.0031] |
| Same active-interval sweep fed both engines' raw matches | 0.0019 s | 0.0018 s |

The walk is **~6% of the C engine's end-to-end corpus time**; its quadratic resolver accounts for
essentially all the rest (the two phases were timed in separate loops, so they need not sum to
exactly 100%). Feed both engines' raw matches through the same sweep and the post-processing
costs are equal — 1.9 ms vs 1.8 ms.

### End-to-end timings

| Metric | C engine | Pure Python | py ÷ C |
|---|---|---|---|
| **Cold build** (fresh process, add + build, 68,693 patterns) | **0.225 s** [0.205–0.238] | 3.46 s [3.19–3.89] | **~16×** [13.6–17.9] |
| — `add_patterns` | 0.070 s | 2.50 s (cold) / 7.92 s (warm process) | — |
| — `build()` (failure links) | 0.154 s | 0.897 s | ~6× |
| **Steady-state rebuild** (median of 5 fresh builds in a warm process) | **0.236 s** [0.209–0.271] | 8.94 s [8.58–9.19] | **~38×** [33–41] |
| Build with `gc.disable()` | 0.256 s | **1.45 s** [1.35–1.63] | 5.7× |
| Rebuild in place on an already-built matcher | ~0.000 s (no-op) | ~0.95 s | — |
| **Search, whole 156.9 KB corpus, end-to-end** | 0.373 s [0.342–0.423] · 421K chars/s | **0.051 s** [0.040–0.055] · 3.1M chars/s | **0.13×** (Python **7.3× faster**) |
| Like-for-like (walk + the *same* sweep resolver) | **0.0240 s** | 0.0499 s | **~2.1×** [1.3–2.4] |
| **Search, per document** (median doc, 8,253 chars) | **1.60 ms** [1.45–1.90] | 2.10 ms [1.54–2.29] | **1.28×** [0.98–1.38] |
| Search, aggregate over all 22 docs | **36.7 ms** [34.4–42.4] | 45.3 ms [39.3–50.0] | 1.23× |

Read that carefully: **today, end-to-end, the pure-Python matcher is ~7.3× faster than the
C-backed one on a whole-corpus search.** Not because Python beat C — because a linear post-pass
beat a quadratic one. Engine against engine on equal footing, the C automaton walk is **~2×**
faster, and at the per-document scale folio-enrich actually runs, the gap is **0.5 ms per call**.

### GC tail latency — a real, reproducible pure-Python liability

The pure-Python trie is **4,132,015 GC-tracked objects** (vs 565,471 for the C engine, which
keeps its trie outside the Python heap). In **every one of the 4 runs**, exactly one of the 11
corpus searches took ~4.4 s instead of ~0.05 s — a **~90× tail spike** from a gen-2 collection
walking the trie:

| | median | **max** |
|---|---|---|
| Python, default GC | 0.051 s | **4.44 s** [4.25–4.49] |
| Python, `gc.freeze()` after build | 0.048 s | **0.054 s** [0.040–0.068] |
| C engine, default GC | 0.373 s | 0.443 s [0.359–0.551] |

`gc.freeze()` after build removes it completely — the tail collapses by ~80×, at no cost to the
median. The same lever explains the build numbers: `gc.disable()` cuts the pure-Python build from
3.5–8.9 s to **1.45 s**. Both are one-line mitigations, but they are mitigations the C engine
simply does not need.

---

## 3. Memory (built structure, 68,693 patterns)

Measured in an isolated subprocess: load patterns → `gc.collect()` → read current RSS from
`/proc/self/statm` → build → `gc.collect()` → read again. Current RSS rather than `ru_maxrss`,
so the delta is not distorted by an earlier high-water mark or by the allocator serving the build
from arenas freed during ontology loading.

| | C engine | Pure Python | py ÷ C |
|---|---|---|---|
| **RSS delta for the built structure** | **46.3 MB** | **388.4 MB** | **8.4×** |
| RSS released when the matcher is dropped | 44.2 MB | 371.5 MB | — |
| GC-tracked objects after build | 565,471 | 4,132,015 | 7.3× |
| `tracemalloc` (Python heap only) | 41.7 MB | 356.7 MB | — |
| Peak process RSS at end of full run | ~528 MB | ~1,202 MB | — |

Identical to the tenth of a MB in all 4 runs. (`tracemalloc` sees more than expected for the C
engine because `pyahocorasick` allocates through `PyMem_Malloc`, which tracemalloc hooks; the
isolated RSS delta is the number to compare.)

---

## 4. The dependency trade-off, as a factual matter

**What folio-enrich pays today.** `pyahocorasick>=2.0.0` is a hard dependency in
`folio-enrich/backend/pyproject.toml` (2.3.0 installed, `ahocorasick.cpython-313-x86_64-linux-gnu.so`).
Its **only** import site in the whole repo is `backend/app/services/matching/aho_corasick.py`.

**What that costs to install.** From the PyPI JSON API for `pyahocorasick` 2.3.1 (latest):
**35 wheels + 1 sdist**, `requires_python >=3.10`.

- Interpreter tags: `cp310`, `cp311`, `cp312`, `cp313`, `cp314`
- Platform tags: `manylinux_2_17` x86_64 + aarch64, `musllinux_1_2` x86_64 + aarch64,
  `macosx_10_13/10_15/10_9_universal2`, `macosx_11_0_arm64`, `win_amd64`

So on every mainstream target folio-enrich runs on, this is a **binary wheel, no compiler
needed**. Honest gaps requiring a source build (C toolchain): **free-threaded builds**
(`cp313t`/`cp314t` — no wheels), **win32 / win_arm64**, and **PyPy**. None of those is a
folio-enrich target today.

**What the library costs.** `folio-resolve`'s declared dependencies are `pydantic>=2.7,<3.0` —
that is the entire list. The matcher itself uses only `collections.deque` and `dataclasses`:
**zero dependencies, installs everywhere Python runs, including free-threaded builds.**

**Would adopting the library's matcher let folio-enrich drop a dependency? Yes — plainly.**
`folio-resolve>=0.1.0` is *already* a hard dependency of folio-enrich. Swapping the matcher
would let `pyahocorasick>=2.0.0` be deleted outright: **net −1 dependency, zero new ones, one
fewer compiled artifact in the image.**

This also validates folio-resolve's own stance in the other direction: the library's
pydantic-only core is **not** paying a meaningful correctness or per-call speed penalty for
refusing the C extension. Exact parity, and within 1.3× per document.

---

## 5. Recommendation (yours to overrule)

**Keep the C extension in folio-enrich. Keep pure Python in folio-resolve. The biggest win on
the table is neither — it's the overlap resolver.**

1. **Keep `pyahocorasick` in folio-enrich.** The two numbers that matter for a long-lived
   FastAPI process are **build (0.24 s vs 3.5–8.9 s, ~16–38×)** and **memory (46 MB vs 388 MB,
   8.4×)**. Multiply 342 MB by your uvicorn worker count and that is the real bill. The install
   argument against a C extension mostly evaporates on inspection — wheels cover cp310–cp314
   across five platform families, so nobody on a supported target compiles anything. Search
   speed does *not* justify keeping it (0.5 ms per document); build and memory do.

2. **Port folio-resolve's active-interval `_resolve_overlaps` into folio-enrich's matcher —
   this is the actual find.** It is a **~15× end-to-end speedup** on corpus-scale searches
   (0.373 s → ~0.024 s), costs no dependency change, and carries no parity risk: the two
   resolvers are exactly what separated the engines in §1, and both engines produced 4,928 raw
   matches and then **identical 4,914 final spans** despite running different resolvers over
   them — plus identical output on every parity fixture. Damien's question was binary; the
   measurement says the largest available win is orthogonal to it.

3. **Keep folio-resolve's matcher pure Python.** Exact parity with a mature C extension, within
   ~2× on the walk, within 1.3× per document, and zero dependencies. The design stance holds.
   Worth adding to the library's own consumers as documented guidance: **call `gc.freeze()` after
   `load_patterns()`** at ontology scale — it removes a reproducible ~90× tail-latency spike, and
   `gc.disable()` around the build cuts it from ~3.5 s to ~1.4 s.

4. **Fix `case_sensitive=True` in folio-enrich's matcher to match the library's semantics.** Dead
   code today (zero call sites), so it is not urgent — but it is silently inverted, and the next
   person to reach for the flag will get wrong answers with no error.

**What would change this recommendation:** if folio-enrich ever needs to run where no
`pyahocorasick` wheel exists (free-threaded CPython, PyPy, win-arm64), or if the 342 MB delta
stops mattering (e.g. a single shared matcher process), item 1 flips and the swap becomes free —
because parity is already exact.

## Reproducing

```bash
cd ~/Coding\ Projects/folio-enrich/backend   # venv has pyahocorasick + folio-resolve (editable)
.venv/bin/python ../../folio-resolve/bench/ac_engine_shootout.py --repeat 4
```

`--all` does one full pass (~3 min) and writes `ac_engine_summary.json`; `--repeat 4` does four
and folds them into `ac_engine_runs.json`, which is where every median and `[min–max]` above
comes from. `--engine {c,py}` and `--memory {c,py}` run one leg in isolation.

Never `uv run` inside folio-enrich — it re-syncs from the lockfile and can silently replace the
editable library with the PyPI build, which would take the fixes out from under the benchmark.
The harness prints `folio_resolve.__file__` in the `py` capture so the resolution is auditable.
