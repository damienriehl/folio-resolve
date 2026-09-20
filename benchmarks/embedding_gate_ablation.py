"""Paired, benchmark-only ShortLabelGate ablation; no production policy changes.

Reuse the frozen public corpus/model and run each variant in a fresh process.
Guard probes are separate controlled ranking inputs, not new relevance fixtures.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import platform
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from folio_resolve import Concept, InMemoryOntology, MatchCandidate, MatchPipeline
from folio_resolve.gates import GateDecision, ShortLabelGate

_SPEC = importlib.util.spec_from_file_location(
    "embedding_diagnostics", Path(__file__).with_name("embedding_diagnostics.py")
)
assert _SPEC is not None and _SPEC.loader is not None
diagnostics = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(diagnostics)
baseline = diagnostics.baseline
ROOT = Path(__file__).parents[1]


class PassThroughShortGate(ShortLabelGate):
    """Deliberately broad ablation, including lexical protections."""

    def evaluate(self, *, query: str, label: str, score: float) -> GateDecision:
        return GateDecision(score=score, demoted=False, reason="short-label gate bypassed")


def bypass_pipeline(pipeline: MatchPipeline) -> MatchPipeline:
    # Shared read-only index/corpus: exactly one model/index build per variant.
    return replace(pipeline, short_gate=PassThroughShortGate())


def candidate_deltas(control: list[dict], experimental: list[dict]) -> list[dict]:
    """Changes in eligibility, score, or final rank keyed by IRI and extraction path."""
    arms = [
        {(c["iri"], c["extraction_path"]): (rank, c) for rank, c in enumerate(arm, 1)}
        for arm in (control, experimental)
    ]
    changes = []
    for iri, path in sorted(arms[0].keys() | arms[1].keys()):
        old_rank, old = arms[0].get((iri, path), (None, None))
        new_rank, new = arms[1].get((iri, path), (None, None))
        if old and new and old_rank == new_rank and old["score"] == new["score"]:
            continue
        changes.append(
            {
                "iri": iri,
                "extraction_path": path,
                "change": "added" if old is None else "removed" if new is None else "changed",
                "baseline_rank": old_rank,
                "bypass_rank": new_rank,
                "baseline": old,
                "bypass": new,
                "score_delta": new["score"] - old["score"] if old and new else None,
            }
        )
    return changes


def compare_case(
    control: MatchPipeline, experimental: MatchPipeline, case: dict, count: int
) -> dict:
    traces = {
        name: diagnostics.trace_query(pipe, case["query"], case["acceptable_iris"], count)
        for name, pipe in (("baseline", control), ("bypass", experimental))
    }
    return {
        **case,
        **traces,
        "deltas": candidate_deltas(
            traces["baseline"]["candidates"], traces["bypass"]["candidates"]
        ),
    }


def guard_checks() -> list[dict]:
    """Run named regression inputs through actual blocklist, gates, floor and dedup.

    Deliberate raw candidate scores reproduce the existing gate unit scenarios;
    they do not pretend the frozen public corpus retrieves these inputs at 88/90.
    """
    probes = [
        (
            "short_fuzzy_law",
            "law of the sea",
            "law",
            "",
            88.0,
            False,
            "tests/test_new_capabilities.py::test_short_label_fuzzy_demoted",
        ),
        (
            "short_exact_tax",
            "tax",
            "Tax",
            "",
            99.0,
            True,
            "tests/test_new_capabilities.py::test_short_label_near_exact_allowed",
        ),
        (
            "uncorroborated_place",
            "Presumptions",
            "Northern Mariana Islands",
            "Location",
            90.0,
            False,
            "tests/test_new_capabilities.py::test_place_name_demoted_without_corroboration",
        ),
        (
            "blocked_action_auction",
            "Action",
            "Auction",
            "",
            90.0,
            False,
            "tests/test_pipeline.py::test_action_not_auction_blocked",
        ),
    ]
    results = []
    for name, query, label, branch, score, expected_survival, reference in probes:
        pipe = MatchPipeline(
            InMemoryOntology([Concept(iri="guard:target", label=label, branch=branch)])
        )
        if name == "blocked_action_auction":
            pipe.blocklist.block(query, "guard:target", reason="Action != Auction")
        raw = MatchCandidate(
            iri="guard:target",
            label=label,
            branch=branch,
            score=score,
            surface_term=query,
            extraction_path="label_search",
        )
        result = {
            "name": name,
            "reference": reference,
            "input": asdict(raw),
            "expected_baseline_survival": expected_survival,
            "arms": {},
        }
        for arm, candidate_pipe in (("baseline", pipe), ("bypass", bypass_pipeline(pipe))):
            candidate = replace(raw)
            survivors = candidate_pipe._rank([candidate], domains=[], heading_terms=set())
            result["arms"][arm] = {
                "candidate_after": asdict(candidate),
                "candidates": [asdict(c) for c in survivors],
            }
        survived = bool(result["arms"]["baseline"]["candidates"])
        if survived != expected_survival:
            raise ValueError(f"Normal-mode guard failed: {name}")
        result["protection_lost"] = not survived and bool(result["arms"]["bypass"]["candidates"])
        results.append(result)
    return results


def verify_control(
    fixture: dict,
    concepts: list,
    frozen: dict,
    source_sha: str,
    variant: str,
    model_files: dict | None,
) -> dict:
    expected = {
        "fixture_sha256": baseline.stable_digest(fixture),
        "corpus_sha256": baseline.corpus_digest(concepts),
        "library_source_sha256": source_sha,
        "corpus_policy": baseline.CORPUS_POLICY,
        "concept_count": len(concepts),
        "variant": variant,
        "ontology": fixture["ontology"],
        "model": fixture["model"] if variant == "local" else None,
        "model_files_sha256": model_files,
        "hashing_dimension": 256 if variant == "hashing" else None,
        "pipeline": {
            "score_floor": 45.0,
            "label_search_limit": 10,
            "semantic_top_k": 5,
            "entity_ruler": None,
            "recall_engine": None,
            "judge": False,
            "context": None,
        },
        "benchmark_source_sha256": baseline.file_digest(Path(baseline.__file__)),
    }
    if frozen["configuration_sha256"] != baseline.stable_digest(frozen["provenance"]):
        raise ValueError("Frozen baseline configuration digest differs")
    for key, value in expected.items():
        if frozen["provenance"].get(key) != value:
            raise ValueError(f"Frozen baseline {key} differs")
    frozen_cases = frozen["positive_results"] + frozen["negative_controls"]
    if len(frozen_cases) != len(fixture["cases"]):
        raise ValueError("Frozen baseline case count differs")
    by_id = {c["id"]: c for c in frozen_cases}
    if len(by_id) != len(frozen_cases):
        raise ValueError("Frozen baseline duplicate case IDs")
    for case in fixture["cases"]:
        if any(by_id.get(case["id"], {}).get(k) != v for k, v in case.items()):
            raise ValueError(f"Frozen baseline case identity differs: {case['id']}")
    return expected


def summarize(results: list[dict], guards: list[dict]) -> dict:
    metrics = {}
    for arm in ("baseline", "bypass"):
        ranked = {case["id"]: [c["iri"] for c in case[arm]["candidates"]] for case in results}
        measured = baseline.recall_metrics(results, ranked)
        metrics[arm] = {
            "positive_queries": measured["positive_queries"],
            "hit_at_1": measured["recall_at_1"],
            "hit_at_5": measured["recall_at_5"],
            "negative_candidate_counts": {
                c["id"]: len(c[arm]["candidates"]) for c in results if c["kind"] == "negative"
            },
        }

    def hit(case, arm):
        return any(
            t["final_rank"] is not None and t["final_rank"] <= 5 for t in case[arm]["targets"]
        )

    recovered = [
        c["id"]
        for c in results
        if c["kind"] == "paraphrase" and not hit(c, "baseline") and hit(c, "bypass")
    ]
    lost_exact = [c["id"] for c in results if c["kind"] == "exact" and not hit(c, "bypass")]
    return {
        "metrics": metrics,
        "recovered_paraphrases_at_5": recovered,
        "exact_targets_missing_at_5": lost_exact,
        "lost_guards": [g["name"] for g in guards if g["protection_lost"]],
        "further_investigation_warranted": bool(recovered) and not lost_exact,
        "interpretation": "Experiment selection only; not production adoption or general precision evidence.",
    }


def run_ablation(owl: Path, variant: str, model_path: Path | None = None) -> dict:
    if variant not in {"disabled", "hashing", "local"}:
        raise ValueError(f"Unknown variant: {variant}")
    source = baseline.verified_library_source()
    source_sha = baseline.stable_digest(
        {
            str(p.relative_to(source.parent.parent)): baseline.file_digest(p)
            for p in sorted(source.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        }
    )
    fixture = baseline.load_fixtures()
    concepts = baseline.load_corpus(owl, fixture["ontology"]["sha256"])
    baseline.validate_answers(fixture, concepts)
    frozen_path = ROOT / f"docs/benchmarks/embedding-baseline-{variant}.json"
    frozen = json.loads(frozen_path.read_text())
    if variant == "local" and model_path is None:
        raise ValueError("Local model requires the existing pinned snapshot directory")
    model_files = (
        baseline.verify_model_files(model_path, fixture["model"]) if variant == "local" else None
    )
    provenance = verify_control(fixture, concepts, frozen, source_sha, variant, model_files)
    expected = {
        c["id"]: c["candidates"] for c in frozen["positive_results"] + frozen["negative_controls"]
    }
    control = baseline.build_pipeline(concepts, variant, model_path, fixture["model"])
    # Check all uninstrumented controls before interpreting any experimental arm.
    for case in fixture["cases"]:
        if [asdict(c) for c in control.match(case["query"])] != expected[case["id"]]:
            raise ValueError(f"Final candidates differ from frozen baseline: {case['id']}")
    experimental = bypass_pipeline(control)
    results = []
    for case in fixture["cases"]:
        result = compare_case(control, experimental, case, len(concepts))
        if result["baseline"]["candidates"] != expected[case["id"]]:
            raise ValueError(f"Trace candidates differ from frozen baseline: {case['id']}")
        results.append(result)
    guards = guard_checks()
    provenance.update(
        {
            "baseline_sha256": baseline.file_digest(frozen_path),
            "diagnostic_source_sha256": baseline.file_digest(Path(diagnostics.__file__)),
            "ablation_source_sha256": baseline.file_digest(Path(__file__)),
            "git_revision": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
            ).stdout.strip(),
            "experiment": "entire ShortLabelGate pass-through; shared corpus/index; other configuration unchanged",
            "hashing_role": "diagnostic provider, not a semantic quality model"
            if variant == "hashing"
            else None,
        }
    )
    return {
        "schema_version": 1,
        "provenance": provenance,
        "configuration_sha256": baseline.stable_digest(provenance),
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "packages": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
        "results": results,
        "guard_checks": guards,
        "summary": summarize(results, guards),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owl", type=Path, required=True)
    parser.add_argument("--variant", choices=("disabled", "hashing", "local"), required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_ablation(args.owl, args.variant, args.model_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"variant": args.variant, "summary": result["summary"]}, indent=2))


if __name__ == "__main__":
    main()
