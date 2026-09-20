"""Collect approved public queries once; prepare blinded, unjudged relevance pairs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

from folio_resolve import MatchPipeline

_SPEC = importlib.util.spec_from_file_location(
    "embedding_semantic_gate_replay", Path(__file__).with_name("embedding_semantic_gate_replay.py")
)
assert _SPEC is not None and _SPEC.loader is not None
replay = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay)
baseline = replay.baseline
ROOT = Path(__file__).parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures/embedding_precision.json"
FIXTURE_SHA256 = "5ac29ce9c90fcbeb8dd3b4e8e4e5f7dda8b13b95327bc2ae81fb198e8af88afe"
CONTROL_PATH = ROOT / "docs/benchmarks/embedding-semantic-gate-replay.json"
CONTROL_SHA256 = "97f061fe72b4b66b97800646c0e10f6bb2daac0c7f720edbd5ee97a556304201"
ARMS = ("baseline", "selective")


def approval_payload(fixture):
    return {key: value for key, value in fixture.items() if key != "approval"}


def load_fixtures(path=FIXTURE_PATH):
    fixture = baseline.load_fixtures(path)
    ids = set()
    for case in fixture["cases"]:
        targets = case["acceptable_iris"]
        if case["id"] in ids or len(targets) != len(set(targets)):
            raise ValueError("Duplicate case or target IRI")
        ids.add(case["id"])
        if any(
            not re.fullmatch(r"https://folio.openlegalstandard.org/[A-Za-z0-9]+", iri)
            for iri in targets
        ):
            raise ValueError("Invalid target IRI")
        if set(targets) != set(case["expected_labels"]):
            raise ValueError("Target label identities differ")
    if fixture.get("approval", {}).get("payload_sha256") != baseline.stable_digest(
        approval_payload(fixture)
    ):
        raise ValueError("Approval payload digest differs")
    # This frozen digest records the actual owner decision, not a caller-provided flag.
    if baseline.stable_digest(fixture) != FIXTURE_SHA256:
        raise ValueError("Approved fixture identity differs")
    if fixture["cases"][:8] != baseline.load_fixtures()["cases"]:
        raise ValueError("Existing case identity differs")
    return fixture


def selective_from(pipeline):
    config = {field.name: getattr(pipeline, field.name) for field in fields(MatchPipeline)}
    gate = pipeline.short_gate
    config["short_gate"] = replay.SemanticOnlyShortGate(
        min_chars=gate._min_chars,
        near_exact_threshold=gate._near_exact,
        demoted_score=gate._demoted_score,
    )
    return replay.SelectivePipeline(**config)


def rank_pair(pipeline, inputs, targets=()):
    return {
        "baseline": replay.rank_snapshot(pipeline, inputs, targets),
        "selective": replay.rank_snapshot(selective_from(pipeline), inputs, targets),
    }


def retrieve_once(pipeline, query):
    calls = []

    def capture(candidates, **kwargs):
        if kwargs != {"domains": [], "heading_terms": set(), "context_text": None}:
            raise ValueError("Ranking context differs")
        calls.append([asdict(c) for c in candidates])
        return []

    # Intercept only the rank boundary, preserving the real retrieval/filter/expand path.
    with patch.object(pipeline, "_rank", capture):
        pipeline.match(query)
    if len(calls) != 1:
        raise ValueError("Expected exactly one retrieval stream")
    return calls[0]


def verify_controls(results, controls):
    if len(results) != len(controls):
        raise ValueError("Existing control count differs")
    for actual, expected in zip(results, controls, strict=True):
        for key in ("id", "kind", "query", "acceptable_iris", "expected_labels"):
            if actual[key] != expected[key]:
                raise ValueError(f"Existing control case differs: {key}")
        for arm in ARMS:
            if actual[arm] != expected[arm]:
                raise ValueError(f"{arm} complete control snapshot differs: {actual['id']}")


def collect_cases(pipeline, fixture, controls):
    results = []
    for position, case in enumerate(fixture["cases"]):
        # Fail the old controls before retrieving any new query.
        if position == len(controls):
            verify_controls(results, controls)
        inputs = retrieve_once(pipeline, case["query"])
        results.append({**case, **rank_pair(pipeline, inputs, case["acceptable_iris"])})
    if len(results) < len(controls):
        raise ValueError("Missing existing controls")
    verify_controls(results[: len(controls)], controls)
    return results


def candidate_pool(results, concepts):
    by_iri = {concept.iri: concept for concept in concepts}
    if len(by_iri) != len(concepts):
        raise ValueError("Duplicate corpus IRIs")
    if len({case["id"] for case in results}) != len(results):
        raise ValueError("Duplicate result case IDs")
    pool = []
    for case in results:
        iris = set()
        for arm in ARMS:
            candidates = case[arm]["candidates"]
            if len({candidate["iri"] for candidate in candidates}) != len(candidates):
                raise ValueError("Duplicate ranked IRI")
            iris.update(candidate["iri"] for candidate in candidates[:5])
        for iri in sorted(iris):
            if iri not in by_iri:
                raise ValueError(f"Candidate missing from pinned corpus: {iri}")
            concept = by_iri[iri]
            pool.append(
                {
                    "query_id": case["id"],
                    "query": case["query"],
                    "iri": iri,
                    "label": concept.label,
                    "definition": concept.definition,
                    "aliases": list(concept.alternative_labels),
                    "parents": list(concept.parent_iris),
                }
            )
    return pool


def prepare_judgments(collection):
    return {
        "schema_version": 1,
        "collection_sha256": baseline.stable_digest(collection),
        "pool_sha256": collection["pool_sha256"],
        "rubric_sha256": baseline.stable_digest(collection["fixture"]["rubric"]),
        "rubric": collection["fixture"]["rubric"],
        "approval": None,
        "judgments": [{**pair, "judgment": None, "rationale": None} for pair in collection["pool"]],
    }


def verify_pins(fixture, concepts, frozen, model_files):
    expected = {
        "library_source_sha256": replay.source_identity(),
        "fixture_sha256": baseline.stable_digest(baseline.load_fixtures()),
        "corpus_sha256": baseline.corpus_digest(concepts),
        "corpus_policy": baseline.CORPUS_POLICY,
        "concept_count": len(concepts),
        "ontology": fixture["ontology"],
        "model": fixture["model"],
        "model_files_sha256": model_files,
    }
    for key, value in expected.items():
        if frozen["provenance"].get(key) != value:
            raise ValueError(f"Frozen {key} differs")
    baseline.validate_answers(fixture, concepts)


def run_collect(owl, model_path):
    fixture = load_fixtures()
    # Validate frozen replay and every consumed predecessor before expensive inference.
    if baseline.file_digest(CONTROL_PATH) != CONTROL_SHA256:
        raise ValueError("Frozen replay artifact differs")
    frozen_replay = json.loads(CONTROL_PATH.read_text())
    if replay.run_replay() != frozen_replay:
        raise ValueError("Frozen replay/source identity differs")
    frozen = json.loads((ROOT / "docs/benchmarks/embedding-baseline-local.json").read_text())
    concepts = baseline.load_corpus(owl, fixture["ontology"]["sha256"])
    model_files = baseline.verify_model_files(model_path, fixture["model"])
    verify_pins(fixture, concepts, frozen, model_files)
    pipeline = baseline.build_pipeline(concepts, "local", model_path, fixture["model"])
    controls = frozen_replay["variants"]["local"]["results"]
    results = collect_cases(pipeline, fixture, controls)
    pool = candidate_pool(results, concepts)
    source_paths = [
        Path(__file__),
        Path(replay.__file__),
        Path(replay.ablation.__file__),
        Path(replay.ablation.diagnostics.__file__),
        Path(baseline.__file__),
        baseline.MODEL_FILES_PATH,
    ]
    provenance = {
        "measurement": "one pinned local retrieval per query; paired real ranking of fresh copies",
        "source_sha256": {
            str(path.relative_to(ROOT)): baseline.file_digest(path) for path in source_paths
        },
        "library_source_sha256": replay.source_identity(),
        "control_artifact_sha256": CONTROL_SHA256,
        "fixture_sha256": baseline.stable_digest(fixture),
        "approval_payload_sha256": fixture["approval"]["payload_sha256"],
        "corpus_sha256": baseline.corpus_digest(concepts),
        "corpus_policy": baseline.CORPUS_POLICY,
        "concept_count": len(concepts),
        "ontology": fixture["ontology"],
        "model": fixture["model"],
        "model_files_sha256": model_files,
        "pipeline": frozen["provenance"]["pipeline"],
        "ranking_context": {"domains": [], "heading_terms": [], "context_text": None},
        "pool_depth": 5,
    }
    collection = {
        "schema_version": 1,
        "fixture": fixture,
        "provenance": provenance,
        "configuration_sha256": baseline.stable_digest(provenance),
        "results": results,
        "pool": pool,
        "pool_sha256": baseline.stable_digest(pool),
        "controls_reproduced": True,
    }
    return collection, prepare_judgments(collection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--owl", type=Path, required=True)
    collect.add_argument("--model-path", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--judgments-output", type=Path, required=True)
    args = parser.parse_args()
    collection, judgments = run_collect(args.owl, args.model_path)
    for path, value in ((args.output, collection), (args.judgments_output, judgments)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "queries": len(collection["results"]),
                "unjudged_pairs": len(judgments["judgments"]),
                "controls_reproduced": collection["controls_reproduced"],
            }
        )
    )


if __name__ == "__main__":
    main()
