"""Collect approved public queries once; prepare blinded, unjudged relevance pairs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

from folio_resolve import InMemoryOntology, MatchPipeline

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
    provenance = {
        "measurement": "one pinned local retrieval per query; paired real ranking of fresh copies",
        "source_sha256": source_hashes(),
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


def source_hashes():
    paths = [
        Path(__file__),
        Path(replay.__file__),
        Path(replay.ablation.__file__),
        Path(replay.ablation.diagnostics.__file__),
        Path(baseline.__file__),
        baseline.MODEL_FILES_PATH,
    ]
    return {str(path.relative_to(ROOT)): baseline.file_digest(path) for path in paths}


def validate_collection(collection):
    """Check frozen identities offline; no corpus load, model load, or retrieval."""
    fixture = load_fixtures()
    if collection.get("schema_version") != 1 or collection.get("fixture") != fixture:
        raise ValueError("Collection fixture/schema differs")
    if baseline.file_digest(CONTROL_PATH) != CONTROL_SHA256:
        raise ValueError("Frozen replay artifact differs")
    frozen_replay = json.loads(CONTROL_PATH.read_text())
    # This also validates the entire predecessor artifact chain and library source.
    if replay.run_replay() != frozen_replay:
        raise ValueError("Frozen replay/source identity differs")
    frozen = json.loads((ROOT / "docs/benchmarks/embedding-baseline-local.json").read_text())
    provenance = collection["provenance"]
    expected = {
        **{
            key: frozen["provenance"][key]
            for key in (
                "library_source_sha256",
                "corpus_sha256",
                "corpus_policy",
                "concept_count",
                "ontology",
                "model",
                "model_files_sha256",
                "pipeline",
            )
        },
        "measurement": "one pinned local retrieval per query; paired real ranking of fresh copies",
        "source_sha256": source_hashes(),
        "control_artifact_sha256": CONTROL_SHA256,
        "fixture_sha256": baseline.stable_digest(fixture),
        "approval_payload_sha256": fixture["approval"]["payload_sha256"],
        "ranking_context": {"domains": [], "heading_terms": [], "context_text": None},
        "pool_depth": 5,
    }
    if provenance != expected:
        raise ValueError("Collection provenance/source/configuration differs")
    if collection.get("configuration_sha256") != baseline.stable_digest(provenance):
        raise ValueError("Configuration digest differs")
    results = collection["results"]
    if len(results) != len(fixture["cases"]):
        raise ValueError("Collection query count differs")
    expected_pairs = []
    for result, case in zip(results, fixture["cases"], strict=True):
        if {key: result.get(key) for key in case} != case:
            raise ValueError("Collection query identity differs")
        if result["baseline"]["candidate_inputs"] != result["selective"]["candidate_inputs"]:
            raise ValueError("Paired retrieval inputs differ")
        # Ranking is deterministic with no context text and default pinned gates. Recheck
        # audit snapshots from saved inputs only; this never retrieves or loads a model.
        ranked = rank_pair(
            MatchPipeline(InMemoryOntology([])),
            result["baseline"]["candidate_inputs"],
            case["acceptable_iris"],
        )
        if any(result[arm] != ranked[arm] for arm in ARMS):
            raise ValueError("Collection ranked snapshot differs from saved inputs")
        iris = set()
        for arm in ARMS:
            candidates = result[arm]["candidates"]
            if len({c["iri"] for c in candidates}) != len(candidates):
                raise ValueError("Duplicate ranked IRI")
            iris.update(c["iri"] for c in candidates[:5])
        expected_pairs.extend((case["id"], case["query"], iri) for iri in sorted(iris))
    if collection.get("controls_reproduced") is not True:
        raise ValueError("Controls not reproduced")
    verify_controls(results[:8], frozen_replay["variants"]["local"]["results"])
    pool = collection["pool"]
    if [(p["query_id"], p["query"], p["iri"]) for p in pool] != expected_pairs:
        raise ValueError("Collection pool membership/order differs")
    for pair in pool:
        if set(pair) != {"query_id", "query", "iri", "label", "definition", "aliases", "parents"}:
            raise ValueError("Pool metadata schema differs")
    if collection.get("pool_sha256") != baseline.stable_digest(pool):
        raise ValueError("Pool digest differs")


def judgment_payload(sheet):
    """Exact review payload; the approval receipt itself is excluded."""
    return {key: value for key, value in sheet.items() if key != "approval"}


def validate_judgments(collection, sheet, approval_sha256=None):
    expected = prepare_judgments(collection)
    if set(sheet) != set(expected):
        raise ValueError("Judgment sheet schema differs")
    for key in ("schema_version", "collection_sha256", "pool_sha256", "rubric_sha256", "rubric"):
        if sheet[key] != expected[key]:
            raise ValueError(f"Judgment {key} differs")
    if len(sheet["judgments"]) != len(expected["judgments"]):
        raise ValueError("Missing/extra judgment pairs")
    labels = {}
    for row, original in zip(sheet["judgments"], expected["judgments"], strict=True):
        if set(row) != set(original) or any(
            row[key] != value
            for key, value in original.items()
            if key not in ("judgment", "rationale")
        ):
            raise ValueError("Judgment pair identity/metadata differs")
        label, rationale = row["judgment"], row["rationale"]
        if label not in (None, "relevant", "irrelevant", "uncertain"):
            raise ValueError("Invalid judgment label")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("Invalid judgment rationale")
        labels[row["query_id"], row["iri"]] = label
    approval = sheet["approval"]
    if any(value is not None for value in labels.values()) or approval is not None:
        if not isinstance(approval, dict) or not approval_sha256:
            raise ValueError("Recorded owner approval digest required for labeled judgments")
        if baseline.stable_digest(approval) != approval_sha256:
            raise ValueError("Owner approval receipt digest differs")
        if set(approval) != {"owner", "date", "decision", "decision_reference", "payload_sha256"}:
            raise ValueError("Owner approval receipt schema differs")
        if any(not isinstance(approval[key], str) or not approval[key].strip() for key in approval):
            raise ValueError("Owner approval evidence missing")
        if approval["payload_sha256"] != baseline.stable_digest(judgment_payload(sheet)):
            raise ValueError("Owner approval payload digest differs")
    elif approval_sha256 is not None:
        raise ValueError("Owner approval receipt missing")
    return labels


def relevance_counts(candidates, labels, query_id):
    top = candidates[:5]
    judgments = [labels.get((query_id, c["iri"])) for c in top]
    counts = {label: judgments.count(label) for label in ("relevant", "irrelevant", "uncertain")}
    counts.update(returned=len(top), missing=judgments.count(None))
    counts["unjudged"] = counts["uncertain"] + counts["missing"]
    counts["judged"] = counts["relevant"] + counts["irrelevant"]
    counts["coverage"] = counts["judged"] / len(top) if top else None
    return counts


def summarize(results, labels, original_ids):
    """Arithmetic on validated artifacts; deliberately usable with small test oracles."""
    queries = []
    for case in results:
        arms = {arm: relevance_counts(case[arm]["candidates"], labels, case["id"]) for arm in ARMS}
        paired_complete = all(arms[arm]["unjudged"] == 0 for arm in ARMS)
        positive = bool(case["acceptable_iris"])
        if not positive and any(arms[arm]["relevant"] for arm in ARMS):
            raise ValueError(
                f"Annotation conflict: relevant negative {case['id']}; owner resolution required"
            )
        for arm in ARMS:
            counts = arms[arm]
            if positive:
                counts["p_at_5_bounds"] = [
                    counts["relevant"] / 5,
                    (counts["relevant"] + counts["unjudged"]) / 5,
                ]
                counts["p_at_5"] = counts["relevant"] / 5 if paired_complete else None
                candidates = case[arm]["candidates"]
                for k in (1, 5):
                    counts[f"target_hit_at_{k}"] = any(
                        c["iri"] in case["acceptable_iris"] for c in candidates[:k]
                    )
        paths = {
            arm: {c["iri"]: c["extraction_path"] for c in case[arm]["candidates"][:5]}
            for arm in ARMS
        }
        added = sorted(paths["selective"].keys() - paths["baseline"].keys())
        queries.append(
            {
                "id": case["id"],
                "query": case["query"],
                "kind": case["kind"],
                "group": "original" if case["id"] in original_ids else "new",
                "positive": positive,
                "paired_complete": paired_complete,
                **arms,
                "added": added,
                "removed": sorted(paths["baseline"].keys() - paths["selective"].keys()),
                "winning_path_changes": [
                    {"iri": iri, **{arm: paths[arm][iri] for arm in ARMS}}
                    for iri in sorted(paths["baseline"].keys() & paths["selective"].keys())
                    if paths["baseline"][iri] != paths["selective"][iri]
                ],
                "irrelevant_pairs": {
                    arm: sorted(
                        iri for iri in paths[arm] if labels.get((case["id"], iri)) == "irrelevant"
                    )
                    for arm in ARMS
                },
                "new_irrelevant_pairs": [
                    iri for iri in added if labels.get((case["id"], iri)) == "irrelevant"
                ],
            }
        )
    groups = {}
    for name in ("combined", "original", "new", "exact", "paraphrase", "geographic"):
        rows = [
            q
            for q in queries
            if q["positive"] and (name == "combined" or q["group"] == name or q["kind"] == name)
        ]
        n = len(rows)
        group = {"query_count": n, "query_ids": [q["id"] for q in rows]}
        complete = all(q["paired_complete"] for q in rows)
        for arm in ARMS:
            bounds = (
                [sum(q[arm]["p_at_5_bounds"][i] for q in rows) / n for i in (0, 1)] if n else None
            )
            group[arm] = {
                "p_at_5": bounds[0] if n and complete else None,
                "p_at_5_bounds": bounds,
                **{
                    f"target_hits_at_{k}": sum(q[arm][f"target_hit_at_{k}"] for q in rows)
                    for k in (1, 5)
                },
                **{
                    key: sum(q[arm][key] for q in rows)
                    for key in (
                        "returned",
                        "relevant",
                        "irrelevant",
                        "uncertain",
                        "missing",
                        "unjudged",
                    )
                },
            }
        groups[name] = group
    return {
        "queries": queries,
        "positive_groups": groups,
        "negative_query_count": sum(not q["positive"] for q in queries),
    }


def run_score(collection, sheet, approval_sha256=None):
    validate_collection(collection)
    labels = validate_judgments(collection, sheet, approval_sha256)
    summary = summarize(
        collection["results"], labels, [case["id"] for case in baseline.load_fixtures()["cases"]]
    )
    return {
        "schema_version": 1,
        "collection_sha256": baseline.stable_digest(collection),
        "judgments_sha256": baseline.stable_digest(sheet),
        "approval_sha256": approval_sha256,
        "judgment_payload_sha256": baseline.stable_digest(judgment_payload(sheet)),
        "scoring_source_sha256": source_hashes(),
        "coverage": {
            "pooled_pairs": len(labels),
            "relevant": list(labels.values()).count("relevant"),
            "irrelevant": list(labels.values()).count("irrelevant"),
            "uncertain": list(labels.values()).count("uncertain"),
            "missing": list(labels.values()).count(None),
        },
        "unresolved_pairs": [
            row for row in sheet["judgments"] if row["judgment"] in (None, "uncertain")
        ],
        "limitations": [
            "Purposive small challenge set, not a blind holdout.",
            "Weak nonsense negatives do not establish general precision.",
            "Missing and uncertain judgments contribute only to upper bounds.",
            "No significance, production adoption, or tuning conclusion.",
        ],
        **summary,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--owl", type=Path, required=True)
    collect.add_argument("--model-path", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--judgments-output", type=Path, required=True)
    score = sub.add_parser("score")
    score.add_argument("--collection", type=Path, required=True)
    score.add_argument("--judgments", type=Path, required=True)
    score.add_argument("--approval-sha256", help="Digest of independently recorded owner receipt")
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        collection, judgments = run_collect(args.owl, args.model_path)
        outputs = ((args.output, collection), (args.judgments_output, judgments))
        status = {
            "queries": len(collection["results"]),
            "unjudged_pairs": len(judgments["judgments"]),
            "controls_reproduced": collection["controls_reproduced"],
        }
    else:
        result = run_score(
            json.loads(args.collection.read_text()),
            json.loads(args.judgments.read_text()),
            args.approval_sha256,
        )
        outputs = ((args.output, result),)
        status = result["coverage"]
    for path, value in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(status))


if __name__ == "__main__":
    main()
