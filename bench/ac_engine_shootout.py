#!/usr/bin/env python3
"""AC engine shootout: folio-enrich's C-extension matcher vs folio-resolve's pure-Python one.

Damien's question (2026-08-05): folio-enrich's Aho-Corasick matcher is backed by the compiled
``pyahocorasick`` C extension (a hard dependency in ``backend/pyproject.toml``). folio-resolve
ships a pure-Python reimplementation of the same contract. Keep the C extension, or take the
library's? His answer to the framing question was "measure first, then decide" — this script is
the measurement, not the decision.

Why every earlier timing is void: two real bugs in the pure-Python matcher were fixed on
2026-08-05 (``78a8ca4``). ``build()`` was documented idempotent but folded the failure chain
into ``outputs`` in place, so each rebuild grew the output lists superlinearly — any prior
benchmark that rebuilt the trie was timing a corrupted structure. And ``search(case_sensitive=
True)`` matched nothing at all. Numbers here are taken against ``main`` after that fix.

Distinct from ``bench/ruler_shootout.py`` (2026-07-16), which asked *spaCy vs Aho-Corasick*.
This asks *which Aho-Corasick*: both engines expose the identical ``add_pattern / add_patterns /
build / search -> [MatchResult(pattern, start, end, value)]`` contract, so they are swapped under
one identical pattern set and one identical corpus.

* ``c``   — ``app.services.matching.aho_corasick.AhoCorasickMatcher`` (folio-enrich, pyahocorasick)
* ``py``  — ``folio_resolve.matching.aho_corasick.AhoCorasickMatcher`` (pure Python, zero deps)

Correctness outranks speed, so parity runs first and is reported first:
1. Full-corpus match-set diff — (start, end, pattern id) sets must be identical.
2. Case-sensitivity parity (the flag whose bug was just fixed).
3. Duplicate lowercase-key behavior (``"Court"`` and ``"court"`` both key to ``court``).
4. Rebuild idempotence (the other bug just fixed).
5. Empty-pattern handling.

Then speed (cold build vs steady-state, per-call vs aggregate, median + spread over repetitions)
and memory (peak RSS delta per engine, in its own subprocess, plus tracemalloc where meaningful).

Run (folio-enrich's venv has BOTH engines: pyahocorasick, and folio-resolve editable-installed
onto this working tree). NEVER ``uv run`` inside folio-enrich — it re-syncs the lockfile and can
silently change the installed library:

    cd ~/Coding\\ Projects/folio-enrich/backend
    .venv/bin/python ../../folio-resolve/bench/ac_engine_shootout.py --repeat 4

``--all`` does one full pass; ``--repeat N`` does N of them and folds the medians. This box runs
several agents at once, so a single pass's absolute timings are noisy — every number in the
results doc is a median over 4 passes with the per-run values kept beside it.

Writes bench/results/ac-engine-*.json (gitignored) + bench/ac_engine_summary.json (last pass,
committed) + bench/ac_engine_runs.json (cross-run aggregate, committed). See
bench/AC-ENGINE-RESULTS.md for the verdict.
"""

from __future__ import annotations

import argparse
import gc
import json
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCH_DIR / "results"

ENRICH_BACKEND = (BENCH_DIR / ".." / ".." / "folio-enrich" / "backend").resolve()
DEMOS_DIR = ENRICH_BACKEND.parent / "frontend" / "demos"

BUILD_REPS = 5
CORPUS_SEARCH_REPS = 11
DOC_SEARCH_REPS = 5

ENGINES = ("c", "py")


def _now() -> float:
    return time.perf_counter()


def _rss_mb() -> float:
    """Peak RSS of this process, MB. ru_maxrss is a high-water mark, so deltas are peak growth."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _rss_now_mb() -> float:
    """*Current* RSS, MB, from /proc/self/statm. Unlike ru_maxrss this can go down, so a
    before/after delta around one build is not distorted by an earlier high-water mark or by the
    allocator serving the build out of arenas freed by ontology loading."""
    pages = int(Path("/proc/self/statm").read_text().split()[1])
    return pages * 4096 / 1024 / 1024


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 6),
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
        "spread_pct": round(100.0 * (ordered[-1] - ordered[0]) / ordered[0], 1) if ordered[0] else None,
        "samples": [round(x, 6) for x in samples],
    }


# --------------------------------------------------------------------------------------
# Inputs — identical pattern set and identical corpus for both engines
# --------------------------------------------------------------------------------------


def _load_docs() -> list[str]:
    """folio-enrich's synthetic demo documents (no matter data)."""
    texts: list[str] = []
    for f in sorted(DEMOS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        cache = d.get("cache") or {}
        t = cache.get("normalizedText") or cache.get("docInput") or ""
        if isinstance(t, str) and len(t) > 500:
            texts.append(t)
    return texts


def _load_patterns() -> tuple[dict[str, dict], int]:
    """The real FOLIO pattern set, via folio-resolve's own ``build_patterns``.

    Returns (patterns, distinct_concept_count). Using build_patterns keeps the stopword and
    minimum-length guards identical to production, so this is the pattern set a consumer would
    actually load — not a synthetic one.
    """
    sys.path.insert(0, str(ENRICH_BACKEND))
    from app.services.folio.folio_service import FolioService

    from folio_resolve.entity_ruler import build_patterns

    labels = FolioService.get_instance().get_all_labels()
    concepts = {info.concept.iri for info in labels.values()}
    return build_patterns(labels), len(concepts)


def _matcher_class(engine: str):
    if engine == "c":
        sys.path.insert(0, str(ENRICH_BACKEND))
        from app.services.matching.aho_corasick import AhoCorasickMatcher
    else:
        from folio_resolve.matching.aho_corasick import AhoCorasickMatcher
    return AhoCorasickMatcher


def _spans(matcher, text: str, case_sensitive: bool = False) -> list[tuple[int, int, str]]:
    """Normalize both engines' output to (start, end, pattern-id)."""
    return [
        (m.start, m.end, str(m.value.get("id", "")))
        for m in matcher.search(text, case_sensitive=case_sensitive)
    ]


# --------------------------------------------------------------------------------------
# Parity probes — small, self-contained, run identically against both engines
# --------------------------------------------------------------------------------------

CASE_TEXT = "The Court ruled. the court adjourned. THE COURT reconvened."
CASE_PATTERNS = {"Court": {"id": "P-Court"}, "ruled": {"id": "P-ruled"}}

DUP_PATTERNS = {"Court": {"id": "P-upper"}, "court": {"id": "P-lower"}}
DUP_TEXT = "The court met."

IDEM_PATTERNS = {"he": {"id": "1"}, "she": {"id": "2"}, "his": {"id": "3"}, "hers": {"id": "4"}}
IDEM_TEXT = "she said his hers he"


def _parity_probes(engine: str) -> dict:
    Matcher = _matcher_class(engine)
    out: dict = {}

    # 1. case-sensitivity
    m = Matcher()
    m.add_patterns(CASE_PATTERNS)
    m.build()
    out["case_insensitive"] = sorted(_spans(m, CASE_TEXT, case_sensitive=False))
    out["case_sensitive"] = sorted(_spans(m, CASE_TEXT, case_sensitive=True))

    # 2. duplicate lowercase key
    m = Matcher()
    m.add_patterns(DUP_PATTERNS)
    m.build()
    out["duplicate_key_matches"] = sorted(_spans(m, DUP_TEXT))
    out["duplicate_key_pattern_count"] = m.pattern_count

    # 3. rebuild idempotence — same results and stable internal size across 4 builds
    m = Matcher()
    m.add_patterns(IDEM_PATTERNS)
    rebuild_results = []
    for _ in range(4):
        m.build()
        rebuild_results.append(sorted(_spans(m, IDEM_TEXT)))
    out["rebuild_stable"] = all(r == rebuild_results[0] for r in rebuild_results)
    out["rebuild_first"] = rebuild_results[0]

    # 4. empty pattern
    m = Matcher()
    try:
        m.add_pattern("", {"id": "empty"})
        m.add_pattern("cat", {"id": "cat"})
        m.build()
        out["empty_pattern"] = {"ok": True, "matches": sorted(_spans(m, "a cat sat"))}
    except Exception as exc:
        out["empty_pattern"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    # 5. word-boundary + overlap sanity on a shared fixture
    m = Matcher()
    m.add_patterns(
        {
            "civil procedure": {"id": "A"},
            "procedure": {"id": "B"},
            "civil": {"id": "C"},
            "proc": {"id": "D"},
        }
    )
    m.build()
    out["overlap_fixture"] = sorted(_spans(m, "The civil procedure rules apply. proc is not a word."))
    return out


# --------------------------------------------------------------------------------------
# Engine run
# --------------------------------------------------------------------------------------


def run_memory(engine: str) -> dict:
    """Isolated memory probe: one fresh process, one build, current-RSS delta around it."""
    Matcher = _matcher_class(engine)
    patterns, _ = _load_patterns()
    gc.collect()
    before = _rss_now_mb()
    m = Matcher()
    m.add_patterns(patterns)
    m.build()
    gc.collect()
    after = _rss_now_mb()
    n_objects = len(gc.get_objects())
    del m
    gc.collect()
    released = after - _rss_now_mb()
    return {
        "engine": engine,
        "patterns": len(patterns),
        "rss_before_mb": round(before, 1),
        "rss_after_build_mb": round(after, 1),
        "rss_delta_mb": round(after - before, 1),
        "rss_released_on_del_mb": round(released, 1),
        "gc_tracked_objects_after_build": n_objects,
    }


def run_engine(engine: str) -> dict:
    Matcher = _matcher_class(engine)
    patterns, concept_count = _load_patterns()
    docs = _load_docs()
    corpus = "\n\n".join(docs)

    result: dict = {
        "engine": engine,
        "python": sys.version.split()[0],
        "pattern_count_input": len(patterns),
        "concept_count": concept_count,
        "corpus_docs": len(docs),
        "corpus_chars": len(corpus),
        "doc_chars_median": int(statistics.median(len(d) for d in docs)),
        "parity_probes": _parity_probes(engine),
    }
    if engine == "c":
        import importlib.metadata as md

        import ahocorasick

        result["backend"] = {
            "module": "pyahocorasick",
            "version": md.version("pyahocorasick"),
            "extension_file": ahocorasick.__file__,
            "compiled": ahocorasick.__file__.endswith(".so"),
        }
    else:
        result["backend"] = {"module": "pure-python (folio_resolve.matching.aho_corasick)"}
        import folio_resolve

        result["folio_resolve_file"] = folio_resolve.__file__

    # ---- cold build: first construction in a fresh process, add and build timed apart ----
    rss_baseline = _rss_mb()
    m = Matcher()
    t0 = _now()
    m.add_patterns(patterns)
    add_s = _now() - t0
    t0 = _now()
    m.build()
    build_s = _now() - t0
    rss_after = _rss_mb()
    result["cold_build"] = {
        "add_patterns_s": round(add_s, 4),
        "build_s": round(build_s, 4),
        "total_s": round(add_s + build_s, 4),
        "pattern_count_reported": m.pattern_count,
        "rss_baseline_mb": round(rss_baseline, 1),
        "rss_after_build_mb": round(rss_after, 1),
        "rss_delta_mb": round(rss_after - rss_baseline, 1),
    }

    # ---- steady-state build: rebuild from scratch, repeated ----
    add_samples: list[float] = []
    build_samples: list[float] = []
    for _ in range(BUILD_REPS):
        mm = Matcher()
        t0 = _now()
        mm.add_patterns(patterns)
        add_samples.append(_now() - t0)
        t0 = _now()
        mm.build()
        build_samples.append(_now() - t0)
        del mm
    result["steady_build"] = {
        "add_patterns_s": _stats(add_samples),
        "build_s": _stats(build_samples),
        "total_median_s": round(
            statistics.median(add_samples) + statistics.median(build_samples), 4
        ),
    }

    # ---- rebuild-in-place: the path the idempotence bug corrupted ----
    inplace = []
    for _ in range(4):
        t0 = _now()
        m.build()
        inplace.append(_now() - t0)
    result["rebuild_in_place_s"] = [round(x, 4) for x in inplace]

    # ---- search: whole corpus, repeated ----
    corpus_samples: list[float] = []
    corpus_n = 0
    for _ in range(CORPUS_SEARCH_REPS):
        t0 = _now()
        corpus_n = len(m.search(corpus))
        corpus_samples.append(_now() - t0)
    cs = _stats(corpus_samples)
    result["search_corpus"] = {
        **cs,
        "matches": corpus_n,
        "chars_per_s_median": int(len(corpus) / cs["median"]),
    }

    # ---- search: per-document (the realistic per-call unit) ----
    per_doc_median: list[float] = []
    doc_total_samples: list[float] = []
    for _ in range(DOC_SEARCH_REPS):
        t0 = _now()
        for d in docs:
            m.search(d)
        doc_total_samples.append(_now() - t0)
    for d in docs:
        samples = []
        for _ in range(DOC_SEARCH_REPS):
            t0 = _now()
            m.search(d)
            samples.append(_now() - t0)
        per_doc_median.append(statistics.median(samples))
    dt = _stats(doc_total_samples)
    result["search_per_doc"] = {
        "aggregate_all_docs_s": dt,
        "per_call_median_s": round(statistics.median(per_doc_median), 6),
        "per_call_min_s": round(min(per_doc_median), 6),
        "per_call_max_s": round(max(per_doc_median), 6),
        "docs": len(docs),
    }

    # ---- decomposition: automaton walk vs. overlap post-processing ----
    # Both engines do the SAME Python-level `_resolve_overlaps` step after the walk, but the two
    # implementations of that step differ: folio-enrich still has the full-rescan O(m^2) version;
    # folio-resolve replaced it with an active-interval sweep. Without splitting them, a corpus
    # timing conflates "C vs Python automaton" with "quadratic vs linear post-processing" — two
    # completely different findings. Instance-attribute shadowing only; no source is modified.
    real_resolver = type(m)._resolve_overlaps
    m._resolve_overlaps = lambda matches: matches  # type: ignore[method-assign]
    walk_samples: list[float] = []
    for _ in range(CORPUS_SEARCH_REPS):
        t0 = _now()
        raw = m.search(corpus)
        walk_samples.append(_now() - t0)
    raw_count = len(raw)
    del m._resolve_overlaps  # type: ignore[attr-defined]

    resolve_samples: list[float] = []
    for _ in range(CORPUS_SEARCH_REPS):
        payload = list(raw)
        t0 = _now()
        real_resolver(m, payload)
        resolve_samples.append(_now() - t0)

    # Cross-variant: this engine's raw matches through the OTHER implementation's resolver.
    from folio_resolve.matching.aho_corasick import AhoCorasickMatcher as _PyMatcher

    sweep_resolver = _PyMatcher._resolve_overlaps
    sweep_host = _PyMatcher()
    sweep_samples: list[float] = []
    for _ in range(CORPUS_SEARCH_REPS):
        payload = list(raw)
        t0 = _now()
        sweep_resolver(sweep_host, payload)
        sweep_samples.append(_now() - t0)

    result["decomposition"] = {
        "raw_matches_before_overlap_resolution": raw_count,
        "walk_only_s": _stats(walk_samples),
        "walk_only_chars_per_s_median": int(len(corpus) / statistics.median(walk_samples)),
        "own_resolver_s": _stats(resolve_samples),
        "sweep_resolver_s": _stats(sweep_samples),
        "note": (
            "walk_only = automaton traversal + word-boundary checks, overlap resolution stubbed "
            "out. own_resolver = the _resolve_overlaps this engine actually ships. "
            "sweep_resolver = folio-resolve's active-interval version fed this engine's raw "
            "matches, i.e. what this engine would cost with the library's post-processing."
        ),
    }

    # ---- GC sensitivity: the pure-Python trie is ~1M+ live objects on the GC heap ----
    gc.collect()
    gc.freeze()
    gc_search_samples: list[float] = []
    for _ in range(CORPUS_SEARCH_REPS):
        t0 = _now()
        m.search(corpus)
        gc_search_samples.append(_now() - t0)
    gc.unfreeze()
    result["search_corpus_gc_frozen"] = {
        **_stats(gc_search_samples),
        "note": "gc.freeze() after build moves the trie out of collection scope.",
    }

    gc.disable()
    t0 = _now()
    mg = Matcher()
    mg.add_patterns(patterns)
    mg.build()
    build_nogc = _now() - t0
    gc.enable()
    del mg
    result["build_gc_disabled_s"] = round(build_nogc, 4)

    # ---- full-corpus match dump for the parity diff ----
    result["corpus_spans"] = [list(s) for s in _spans(m, corpus)]

    del m
    gc.collect()

    # ---- tracemalloc: Python-heap cost of the built structure ----
    # Honest caveat recorded in the payload: tracemalloc only sees Python allocations, so for the
    # C engine it measures the wrapper/value objects, NOT the automaton itself. RSS is the
    # apples-to-apples number; tracemalloc is reported for the pure-Python engine's benefit.
    tracemalloc.start()
    base_cur, _ = tracemalloc.get_traced_memory()
    mt = Matcher()
    mt.add_patterns(patterns)
    mt.build()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result["tracemalloc"] = {
        "built_structure_mb": round((cur - base_cur) / 1024 / 1024, 1),
        "peak_mb": round(peak / 1024 / 1024, 1),
        "caveat": "Python-heap only; the C automaton is invisible to tracemalloc. Compare RSS.",
    }
    del mt
    result["rss_final_peak_mb"] = round(_rss_mb(), 1)
    return result


# --------------------------------------------------------------------------------------
# Summary + multi-run aggregate
# --------------------------------------------------------------------------------------

# Metrics folded into the cross-run aggregate: (name, path into a summary, with "{e}" as the
# engine slot). This is the source of every number in AC-ENGINE-RESULTS.md.
AGGREGATE_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cold_build_total_s", ("engines", "{e}", "cold_build", "total_s")),
    ("cold_add_patterns_s", ("engines", "{e}", "cold_build", "add_patterns_s")),
    ("cold_build_only_s", ("engines", "{e}", "cold_build", "build_s")),
    ("steady_add_median_s", ("engines", "{e}", "steady_build", "add_patterns_s", "median")),
    ("steady_build_median_s", ("engines", "{e}", "steady_build", "build_s", "median")),
    ("steady_total_median_s", ("engines", "{e}", "steady_build", "total_median_s")),
    ("build_gc_disabled_s", ("engines", "{e}", "build_gc_disabled_s")),
    ("search_corpus_median_s", ("engines", "{e}", "search_corpus", "median")),
    ("search_corpus_max_s", ("engines", "{e}", "search_corpus", "max")),
    ("search_corpus_gcfrozen_median_s", ("engines", "{e}", "search_corpus_gc_frozen", "median")),
    ("search_corpus_gcfrozen_max_s", ("engines", "{e}", "search_corpus_gc_frozen", "max")),
    ("per_doc_percall_median_s", ("engines", "{e}", "search_per_doc", "per_call_median_s")),
    (
        "per_doc_aggregate_median_s",
        ("engines", "{e}", "search_per_doc", "aggregate_all_docs_s", "median"),
    ),
    ("walk_only_median_s", ("engines", "{e}", "decomposition", "walk_only_s", "median")),
    ("own_resolver_median_s", ("engines", "{e}", "decomposition", "own_resolver_s", "median")),
    ("sweep_resolver_median_s", ("engines", "{e}", "decomposition", "sweep_resolver_s", "median")),
    ("isolated_rss_delta_mb", ("isolated_memory", "{e}", "rss_delta_mb")),
    ("gc_objects_after_build", ("isolated_memory", "{e}", "gc_tracked_objects_after_build")),
    ("tracemalloc_mb", ("engines", "{e}", "tracemalloc", "built_structure_mb")),
)


def aggregate(runs: list[dict]) -> dict:
    """Median / min / max of each metric across N independent ``--all`` runs.

    The machine is shared with other agents, so a single run's absolute timings are noisy. The
    per-run values are kept alongside each median precisely so the noise stays visible instead of
    being laundered into false precision.
    """
    agg: dict = {
        "runs": len(runs),
        "note": (
            "median/min/max across independent full --all runs on a machine also running several "
            "concurrent agents; per-run values retained so the spread stays visible."
        ),
        "metrics": {},
        "ratios_py_over_c": {},
        "parity": {},
    }
    for name, path in AGGREGATE_METRICS:
        entry = {}
        for e in ENGINES:
            vals = []
            for r in runs:
                cur = r
                for part in path:
                    cur = cur[e if part == "{e}" else part]
                vals.append(cur)
            entry[e] = {
                "median": statistics.median(vals),
                "min": min(vals),
                "max": max(vals),
                "runs": vals,
            }
        agg["metrics"][name] = entry
    for key in runs[0]["ratios_py_over_c"]:
        vals = [r["ratios_py_over_c"][key] for r in runs]
        agg["ratios_py_over_c"][key] = {"median": statistics.median(vals), "runs": vals}
    agg["parity"] = {
        "corpus_identical_all_runs": all(r["corpus_parity"]["identical"] for r in runs),
        "final_spans": [r["corpus_parity"]["c_total"] for r in runs],
        "raw_matches_c": [
            r["engines"]["c"]["decomposition"]["raw_matches_before_overlap_resolution"]
            for r in runs
        ],
        "raw_matches_py": [
            r["engines"]["py"]["decomposition"]["raw_matches_before_overlap_resolution"]
            for r in runs
        ],
        "probe_parity_all_runs": {
            k: all(r["probe_parity"][k] for r in runs) for k in runs[0]["probe_parity"]
        },
    }
    (BENCH_DIR / "ac_engine_runs.json").write_text(json.dumps(agg, indent=2) + "\n")
    return agg


def summarize() -> dict:
    data = {e: json.loads((RESULTS_DIR / f"ac-engine-{e}.json").read_text()) for e in ENGINES}
    sets = {e: {tuple(s) for s in d["corpus_spans"]} for e, d in data.items()}
    only_c = sets["c"] - sets["py"]
    only_py = sets["py"] - sets["c"]

    probes_equal = {}
    for key in data["c"]["parity_probes"]:
        probes_equal[key] = data["c"]["parity_probes"][key] == data["py"]["parity_probes"][key]

    c, p = data["c"], data["py"]
    ratios = {
        "cold_build_total_py_over_c": round(
            p["cold_build"]["total_s"] / c["cold_build"]["total_s"], 2
        ),
        "steady_build_total_py_over_c": round(
            p["steady_build"]["total_median_s"] / c["steady_build"]["total_median_s"], 2
        ),
        "corpus_search_py_over_c": round(
            p["search_corpus"]["median"] / c["search_corpus"]["median"], 2
        ),
        "per_doc_search_py_over_c": round(
            p["search_per_doc"]["per_call_median_s"] / c["search_per_doc"]["per_call_median_s"], 2
        ),
        "rss_delta_py_over_c": round(
            p["cold_build"]["rss_delta_mb"] / c["cold_build"]["rss_delta_mb"], 2
        )
        if c["cold_build"]["rss_delta_mb"]
        else None,
        "walk_only_py_over_c": round(
            p["decomposition"]["walk_only_s"]["median"] / c["decomposition"]["walk_only_s"]["median"],
            2,
        ),
        "own_resolver_py_over_c": round(
            p["decomposition"]["own_resolver_s"]["median"]
            / c["decomposition"]["own_resolver_s"]["median"],
            2,
        ),
        "like_for_like_corpus_py_over_c": round(
            (p["decomposition"]["walk_only_s"]["median"] + p["decomposition"]["sweep_resolver_s"]["median"])
            / (c["decomposition"]["walk_only_s"]["median"] + c["decomposition"]["sweep_resolver_s"]["median"]),
            2,
        ),
    }

    memory = {}
    for e in ENGINES:
        path = RESULTS_DIR / f"ac-engine-mem-{e}.json"
        if path.exists():
            memory[e] = json.loads(path.read_text())
    if len(memory) == len(ENGINES) and memory["c"]["rss_delta_mb"]:
        ratios["isolated_rss_delta_py_over_c"] = round(
            memory["py"]["rss_delta_mb"] / memory["c"]["rss_delta_mb"], 2
        )

    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "engines": {e: {k: v for k, v in d.items() if k != "corpus_spans"} for e, d in data.items()},
        "isolated_memory": memory,
        "corpus_parity": {
            "c_total": len(sets["c"]),
            "py_total": len(sets["py"]),
            "identical": sets["c"] == sets["py"],
            "both": len(sets["c"] & sets["py"]),
            "c_only": len(only_c),
            "py_only": len(only_py),
            "c_only_examples": sorted(only_c)[:15],
            "py_only_examples": sorted(only_py)[:15],
        },
        "probe_parity": probes_equal,
        "ratios_py_over_c": ratios,
    }
    (BENCH_DIR / "ac_engine_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=list(ENGINES))
    ap.add_argument("--memory", choices=list(ENGINES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--repeat",
        type=int,
        metavar="N",
        help="run --all N times and write the median/min/max aggregate to ac_engine_runs.json",
    )
    args = ap.parse_args()
    RESULTS_DIR.mkdir(exist_ok=True)

    if args.repeat:
        runs: list[dict] = []
        for i in range(1, args.repeat + 1):
            print(f"##### aggregate run {i}/{args.repeat} #####", flush=True)
            proc = subprocess.run(
                [sys.executable, str(__file__), "--all"],
                cwd=str(ENRICH_BACKEND),
                capture_output=True,
                text=True,
                timeout=7200,
            )
            if proc.returncode != 0:
                print(proc.stdout[-4000:])
                print(proc.stderr[-4000:])
                return proc.returncode
            runs.append(json.loads((BENCH_DIR / "ac_engine_summary.json").read_text()))
        agg = aggregate(runs)
        print(json.dumps({k: v for k, v in agg.items() if k != "metrics"}, indent=2))
        return 0

    if args.memory:
        result = run_memory(args.memory)
        (RESULTS_DIR / f"ac-engine-mem-{args.memory}.json").write_text(json.dumps(result) + "\n")
        print(json.dumps(result, indent=2))
        return 0

    if args.engine:
        result = run_engine(args.engine)
        (RESULTS_DIR / f"ac-engine-{args.engine}.json").write_text(json.dumps(result) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "corpus_spans"}, indent=2))
        return 0

    if args.all:
        for flag in ("--engine", "--memory"):
            for e in ENGINES:
                print(f"=== running {flag} {e} (subprocess) ===", flush=True)
                proc = subprocess.run(
                    [sys.executable, str(__file__), flag, e],
                    cwd=str(ENRICH_BACKEND),
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
                if proc.returncode != 0:
                    print(proc.stdout[-4000:])
                    print(proc.stderr[-4000:])
                    return proc.returncode
        summary = summarize()
        print(json.dumps({k: v for k, v in summary.items() if k != "engines"}, indent=2))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
