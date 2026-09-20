"""Replay frozen retrievals through real ranking; no new retrieval measurement."""

from __future__ import annotations

import argparse
import importlib.util
import json
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

from folio_resolve import InMemoryOntology, MatchCandidate, MatchPipeline
from folio_resolve.gates import GateDecision, ShortLabelGate

_SPEC = importlib.util.spec_from_file_location(
    "embedding_gate_ablation", Path(__file__).with_name("embedding_gate_ablation.py")
)
assert _SPEC is not None and _SPEC.loader is not None
ablation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ablation)
baseline = ablation.baseline
ROOT = Path(__file__).parents[1]
INPUT_SHA256 = {
    "disabled": "92a9bedd1564c7b41743e99cb4e2f41a852aabe39ba2977d38d49421a2374e9f",
    "hashing": "123aea07faaf993ce856a72e8e0252544c568177ac5866d872b74799e3deaa1b",
    "local": "8d605aba45356a40a131298b721eae9b03c5c60ad39a7f8dcf2dfbced5a2cd92",
}
_CURRENT_PATH: ContextVar[str | None] = ContextVar("replay_candidate_path", default=None)
ARMS = ("baseline", "bypass", "selective")


class SemanticOnlyShortGate(ShortLabelGate):
    def evaluate(self, *, query: str, label: str, score: float) -> GateDecision:
        if _CURRENT_PATH.get() == "semantic":
            return GateDecision(score, False, "semantic short-label gate bypassed")
        return super().evaluate(query=query, label=label, score=score)


class SelectivePipeline(MatchPipeline):
    def _rank(self, candidates, **kwargs):
        # The real ranker consumes one complete stream. Set path before it sees each
        # candidate, including blocked candidates, and clear it even on exceptions.
        def scoped_candidates():
            for candidate in candidates:
                _CURRENT_PATH.set(candidate.extraction_path)
                yield candidate

        token = _CURRENT_PATH.set(None)
        try:
            return super()._rank(scoped_candidates(), **kwargs)
        finally:
            _CURRENT_PATH.reset(token)


def selective_pipeline():
    return SelectivePipeline(InMemoryOntology([]), short_gate=SemanticOnlyShortGate())


def pipeline_for(arm):
    if arm == "selective":
        return selective_pipeline()
    pipe = MatchPipeline(InMemoryOntology([]))
    if arm == "bypass":
        return ablation.bypass_pipeline(pipe)
    if arm != "baseline":
        raise ValueError(f"Unknown arm: {arm}")
    return pipe


def rank_snapshot(pipe, inputs, targets=()):
    candidates = [MatchCandidate(**item) for item in inputs]
    ranked = pipe._rank(candidates, domains=[], heading_terms=set(), context_text=None)
    return {
        "candidates": [asdict(c) for c in ranked],
        "candidate_inputs": inputs,
        "candidates_after": [asdict(c) for c in candidates],
        "targets": [
            {
                "iri": iri,
                "final_rank": next((i for i, c in enumerate(ranked, 1) if c.iri == iri), None),
            }
            for iri in targets
        ],
    }


def source_identity():
    source = baseline.verified_library_source()
    return baseline.stable_digest(
        {
            str(p.relative_to(source.parent.parent)): baseline.file_digest(p)
            for p in sorted(source.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        }
    )


def verify_saved(saved, fixture, variant, library_sha):
    """Validate identities even after the outer immutable-file check."""
    provenance = saved["provenance"]
    if saved["configuration_sha256"] != baseline.stable_digest(provenance):
        raise ValueError("Ablation configuration digest differs")
    expected = {
        "variant": variant,
        "fixture_sha256": baseline.stable_digest(fixture),
        "library_source_sha256": library_sha,
        "benchmark_source_sha256": baseline.file_digest(Path(baseline.__file__)),
        "diagnostic_source_sha256": baseline.file_digest(Path(ablation.diagnostics.__file__)),
        "ablation_source_sha256": baseline.file_digest(Path(ablation.__file__)),
        "baseline_sha256": baseline.file_digest(
            ROOT / f"docs/benchmarks/embedding-baseline-{variant}.json"
        ),
        "pipeline": {
            "score_floor": 45.0,
            "label_search_limit": 10,
            "semantic_top_k": 5,
            "entity_ruler": None,
            "recall_engine": None,
            "judge": False,
            "context": None,
        },
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ValueError(f"Ablation {key} differs")
    if len(saved["results"]) != len(fixture["cases"]):
        raise ValueError("Ablation case count differs")
    for actual, case in zip(saved["results"], fixture["cases"], strict=True):
        if any(actual.get(k) != v for k, v in case.items()):
            raise ValueError(f"Ablation case identity differs: {case['id']}")
        if [r["before"] for r in actual["baseline"]["retrievals"]] != [
            r["before"] for r in actual["bypass"]["retrievals"]
        ]:
            raise ValueError(f"Paired inputs differ: {case['id']}")


def replay_controls(saved):
    """Require every complete original/full snapshot before selective comparisons."""
    results = []
    for case in saved["results"]:
        inputs = [r["before"] for r in case["baseline"]["retrievals"]]
        result = {k: case[k] for k in ("id", "kind", "query", "acceptable_iris", "expected_labels")}
        for arm in ("baseline", "bypass"):
            result[arm] = rank_snapshot(pipeline_for(arm), inputs, case["acceptable_iris"])
            if result[arm]["candidates"] != case[arm]["candidates"]:
                raise ValueError(f"{arm} control candidates differ: {case['id']}")
            if result[arm]["candidates_after"] != [r["after"] for r in case[arm]["retrievals"]]:
                raise ValueError(f"{arm} control mutations differ: {case['id']}")
        results.append(result)
    return results


def guard_checks():
    results = []
    for probe in ablation.guard_checks():
        for path in ("label_search", "semantic"):
            inputs = [{**probe["input"], "extraction_path": path}]
            arms = {}
            for arm in ARMS:
                pipe = pipeline_for(arm)
                if probe["name"] == "blocked_action_auction":
                    pipe.blocklist.block(inputs[0]["surface_term"], inputs[0]["iri"])
                arms[arm] = rank_snapshot(pipe, inputs)
            results.append(
                {
                    "name": f"{probe['name']}:{path}",
                    "reference": probe["reference"],
                    "input": inputs[0],
                    "arms": arms,
                    "selective_matches_baseline_survival": bool(arms["selective"]["candidates"])
                    == bool(arms["baseline"]["candidates"]),
                    "selective_protection_lost": not arms["baseline"]["candidates"]
                    and bool(arms["selective"]["candidates"]),
                }
            )
    return results


def metrics(results):
    output = {}
    for arm in ARMS:
        ranked = {c["id"]: [v["iri"] for v in c[arm]["candidates"]] for c in results}
        measured = baseline.recall_metrics(results, ranked)
        output[arm] = {
            "positive_queries": measured["positive_queries"],
            "hit_at_1": measured["recall_at_1"],
            "hit_at_5": measured["recall_at_5"],
            "negative_candidate_counts": {
                c["id"]: len(c[arm]["candidates"]) for c in results if c["kind"] == "negative"
            },
        }
    return output


def run_replay():
    library_sha = source_identity()
    fixture = baseline.load_fixtures()
    saved_inputs = {}
    for variant, digest in INPUT_SHA256.items():
        path = ROOT / f"docs/benchmarks/embedding-gate-ablation-{variant}.json"
        if baseline.file_digest(path) != digest:
            raise ValueError(f"Frozen artifact SHA-256 differs: {variant}")
        saved_inputs[variant] = json.loads(path.read_text())
        verify_saved(saved_inputs[variant], fixture, variant, library_sha)
    # Validate all variants before any selective verdict can be produced.
    variants = {name: {"results": replay_controls(saved)} for name, saved in saved_inputs.items()}
    for data in variants.values():
        for case in data["results"]:
            case["selective"] = rank_snapshot(
                selective_pipeline(), case["baseline"]["candidate_inputs"], case["acceptable_iris"]
            )
            case["deltas"] = {
                arm: ablation.candidate_deltas(
                    case[arm]["candidates"], case["selective"]["candidates"]
                )
                for arm in ("baseline", "bypass")
            }
        data["metrics"] = metrics(data["results"])
    guards = guard_checks()
    local = {c["id"]: c for c in variants["local"]["results"]}

    def hit(identity, cutoff):
        return any(
            t["final_rank"] is not None and t["final_rank"] <= cutoff
            for t in local[identity]["selective"]["targets"]
        )

    preserved = hit("P1", 5) and all(
        hit(c["id"], 1) for c in local.values() if c["kind"] == "exact"
    )
    preserved = preserved and all(
        g["selective_matches_baseline_survival"]
        for g in guards
        if g["input"]["extraction_path"] == "label_search"
    )
    provenance = {
        "measurement": "ranking replay of frozen retrievals; no model/corpus retrieval or timing",
        "input_artifacts_sha256": INPUT_SHA256,
        "library_source_sha256": library_sha,
        "fixture_sha256": baseline.stable_digest(fixture),
        "replay_source_sha256": baseline.file_digest(Path(__file__)),
        "input_provenance": {name: saved["provenance"] for name, saved in saved_inputs.items()},
        "ranking_context": {"domains": [], "heading_terms": [], "context_text": None},
    }
    return {
        "schema_version": 1,
        "provenance": provenance,
        "configuration_sha256": baseline.stable_digest(provenance),
        "variants": variants,
        "guard_checks": guards,
        "decision": {
            "preserves_observed_benefit_and_lexical_protection": preserved,
            "interpretation": "Hypothesis test only; semantic admissions and weak negative controls "
            "do not establish general precision or production adoption readiness.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "metrics": {v: d["metrics"] for v, d in result["variants"].items()},
                "decision": result["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
