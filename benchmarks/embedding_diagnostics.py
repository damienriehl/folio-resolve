"""Trace frozen public benchmark retrieval/gates and observe component query time.

Run each variant in a fresh process. Full-neighbor queries and trace serialization
are outside timed matches. Semantic time includes embedding time; do not add both.
These observers depend on private pipeline/index methods and pin their source bytes.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from folio_resolve import MatchPipeline

_SPEC = importlib.util.spec_from_file_location(
    "embedding_recall_benchmark", Path(__file__).with_name("embedding_recall.py")
)
assert _SPEC is not None and _SPEC.loader is not None
baseline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(baseline)


def trace_query(pipeline: MatchPipeline, query: str, targets: list[str], count: int) -> dict:
    """Observe real rank mutations and blocklist decisions, preserving input snapshots."""
    neighbors = (
        pipeline.semantic_index.query(query, top_k=count)
        if pipeline.semantic_index is not None
        else []
    )
    semantic = {
        iri: {"rank": rank, "cosine": cosine} for rank, (iri, _, cosine) in enumerate(neighbors, 1)
    }
    retrievals = []
    original_rank = pipeline._rank
    original_blocked = pipeline.blocklist.is_blocked
    blocked = []

    def observe_blocked(*args, **kwargs):
        result = original_blocked(*args, **kwargs)
        blocked.append(result)
        return result

    def observe_rank(candidates, **kwargs):
        before = [asdict(c) for c in candidates]
        ranked = original_rank(candidates, **kwargs)
        survivors = {id(c) for c in ranked}
        if len(blocked) != len(candidates):
            raise ValueError("Pipeline blocklist observation no longer maps to candidates")
        for cand, prior, is_blocked in zip(candidates, before, blocked, strict=True):
            if is_blocked:
                outcome = "blocked"
            elif id(cand) in survivors:
                outcome = "survived"
            elif cand.score < pipeline.score_floor:
                outcome = (
                    "demoted_below_floor"
                    if prior["score"] >= pipeline.score_floor and cand.gated
                    else "below_floor"
                )
            else:
                outcome = "deduplicated"
            retrievals.append({"before": prior, "after": asdict(cand), "outcome": outcome})
        return ranked

    with (
        patch.object(pipeline, "_rank", observe_rank),
        patch.object(pipeline.blocklist, "is_blocked", observe_blocked),
    ):
        candidates = [asdict(c) for c in pipeline.match(query)]
    target_results = []
    final_ranks = {c["iri"]: rank for rank, c in enumerate(candidates, 1)}
    for iri in targets:
        paths = [r for r in retrievals if r["before"]["iri"] == iri]
        outcomes = sorted({r["outcome"] for r in paths})
        outcome = (
            "survived"
            if iri in final_ranks
            else "not_retrieved"
            if not paths
            else outcomes[0]
            if len(outcomes) == 1
            else "multiple_rejection_causes"
        )
        target_results.append(
            {
                "iri": iri,
                "semantic_rank": semantic.get(iri, {}).get("rank"),
                "semantic_cosine": semantic.get(iri, {}).get("cosine"),
                "final_rank": final_ranks.get(iri),
                "outcome": outcome,
                "retrievals": paths,
            }
        )
    return {
        "candidates": candidates,
        "retrievals": retrievals,
        "targets": target_results,
        "semantic_top_five": [
            {"iri": iri, "label": label, "cosine": cosine} for iri, label, cosine in neighbors[:5]
        ],
    }


def timed_match(pipeline: MatchPipeline, query: str) -> tuple[list[dict], dict]:
    """Time the actual match with observational wrappers, excluding patch setup."""
    times = {
        "lexical_seconds": 0.0,
        "semantic_seconds": 0.0,
        "embedding_seconds": 0.0,
        "lexical_calls": 0,
        "semantic_calls": 0,
        "embedding_calls": 0,
    }

    def observer(method, name):
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                times[f"{name}_seconds"] += time.perf_counter() - start
                times[f"{name}_calls"] += 1

        return measured

    with ExitStack() as stack:
        ontology = pipeline.ontology
        stack.enter_context(
            patch.object(ontology, "search_by_label", observer(ontology.search_by_label, "lexical"))
        )
        index = pipeline.semantic_index
        if index is not None:
            stack.enter_context(patch.object(index, "query", observer(index.query, "semantic")))
            provider = index._provider
            stack.enter_context(
                patch.object(provider, "embed", observer(provider.embed, "embedding"))
            )
        start = time.perf_counter()
        candidates = pipeline.match(query)
        times["total_seconds"] = time.perf_counter() - start
    times["other_seconds"] = (
        times["total_seconds"] - times["lexical_seconds"] - times["semantic_seconds"]
    )
    times["semantic_without_embedding_seconds"] = (
        times["semantic_seconds"] - times["embedding_seconds"]
    )
    return [asdict(c) for c in candidates], times


def run_diagnostics(owl: Path, variant: str, model_path: Path | None, repeats: int) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    library_source = baseline.verified_library_source()
    fixture = baseline.load_fixtures()
    concepts = baseline.load_corpus(owl, fixture["ontology"]["sha256"])
    baseline.validate_answers(fixture, concepts)
    baseline_path = Path(__file__).parents[1] / f"docs/benchmarks/embedding-baseline-{variant}.json"
    frozen = json.loads(baseline_path.read_text())
    corpus_sha = baseline.corpus_digest(concepts)
    fixture_sha = baseline.stable_digest(fixture)
    source_sha = baseline.stable_digest(
        {
            str(p.relative_to(library_source.parent.parent)): baseline.file_digest(p)
            for p in sorted(library_source.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        }
    )
    for key, expected in (
        ("fixture_sha256", fixture_sha),
        ("corpus_sha256", corpus_sha),
        ("library_source_sha256", source_sha),
    ):
        if frozen["provenance"][key] != expected:
            raise ValueError(f"Frozen baseline {key} differs")
    expected = {
        case["id"]: case["candidates"]
        for case in frozen["positive_results"] + frozen["negative_controls"]
    }
    start = time.perf_counter()
    pipeline = baseline.build_pipeline(concepts, variant, model_path, fixture["model"])
    build_seconds = time.perf_counter() - start
    results = []
    for case in fixture["cases"]:
        # One untimed uninstrumented warmup is also the observational equivalence oracle.
        reference = [asdict(c) for c in pipeline.match(case["query"])]
        if reference != expected[case["id"]]:
            raise ValueError(f"Final candidates differ from frozen baseline: {case['id']}")
        samples = []
        for _ in range(repeats):
            candidates, times = timed_match(pipeline, case["query"])
            if candidates != reference:
                raise ValueError(f"Timing instrumentation changed candidates: {case['id']}")
            samples.append(times)
        results.append({**case, "timing_samples": samples})
    # Trace and full-corpus ranking follow all timing, avoiding their cache effects.
    for case in results:
        trace = trace_query(pipeline, case["query"], case["acceptable_iris"], len(concepts))
        if trace["candidates"] != expected[case["id"]]:
            raise ValueError(f"Trace instrumentation changed candidates: {case['id']}")
        case.update(trace)
    provenance = {
        "fixture_sha256": fixture_sha,
        "ontology": fixture["ontology"],
        "corpus_sha256": corpus_sha,
        "corpus_policy": baseline.CORPUS_POLICY,
        "concept_count": len(concepts),
        "variant": variant,
        "model": fixture["model"] if variant == "local" else None,
        "model_files_sha256": baseline.verify_model_files(model_path, fixture["model"])
        if variant == "local"
        else None,
        "library_source_sha256": source_sha,
        "diagnostic_source_sha256": baseline.file_digest(Path(__file__)),
        "benchmark_source_sha256": baseline.file_digest(Path(baseline.__file__)),
        "baseline_sha256": baseline.file_digest(baseline_path),
        "pipeline": frozen["provenance"]["pipeline"],
        "git_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parents[1],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    }
    samples = [s for case in results for s in case["timing_samples"]]
    summary = {
        key: {
            "p50": baseline.percentile([s[key] for s in samples], 0.5),
            "p95": baseline.percentile([s[key] for s in samples], 0.95),
            "sum": sum(s[key] for s in samples),
        }
        for key in samples[0]
        if key.endswith("_seconds")
    }
    return {
        "schema_version": 1,
        "provenance": provenance,
        "configuration_sha256": baseline.stable_digest(provenance),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "threads": {key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")},
        },
        "packages": dict(
            sorted(
                (dist.metadata["Name"], dist.version)
                for dist in importlib.metadata.distributions()
                if dist.metadata["Name"]
            )
        ),
        "runtime": {
            "pipeline_build_seconds": build_seconds,
            "warm_repeats_per_query": repeats,
            "timing_policy": "one uninstrumented warmup per query; timed actual matches; lexical includes all label/decomposition searches; semantic includes encoding and exhaustive search; embedding is nested within semantic; other is total minus lexical minus semantic; patch setup and serialization excluded; trace/full-neighbor queries after all timing; pooled linear percentiles",
            "components": summary,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owl", type=Path, required=True)
    parser.add_argument("--variant", choices=("disabled", "hashing", "local"), required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = run_diagnostics(args.owl, args.variant, args.model_path, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"variant": args.variant, "runtime": result["runtime"]}, indent=2))


if __name__ == "__main__":
    main()
