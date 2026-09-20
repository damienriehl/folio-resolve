"""Score owner relation categories offline against the frozen public candidate pool."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "embedding_precision", Path(__file__).with_name("embedding_precision.py")
)
assert _SPEC is not None and _SPEC.loader is not None
precision = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(precision)

CATEGORIES = ("direct_match", "child", "parent", "relationship", "irrelevant", "uncertain")
RUBRIC = (
    "Owner annotations: direct_match means a direct match; child, parent, and relationship "
    "describe the owner's query-specific classification, not verified ontology edges. "
    "Strict P@5 counts only direct_match; expanded P@5 counts direct_match, child, parent, "
    "and relationship. Explicit irrelevant counts in neither. Missing (null) and uncertain "
    "stay unjudged in both metrics. Unlisted candidates remain null because the owner found "
    "them too ambiguous. Both metrics use five slots and withhold paired point estimates "
    "and complete-group macros while returned candidates remain unjudged."
)


def prepare_judgments(collection):
    sheet = precision.prepare_judgments(collection)
    sheet.update(rubric=RUBRIC, rubric_sha256=precision.baseline.stable_digest(RUBRIC))
    return sheet


def validate_judgments(collection, sheet, approval_sha256=None):
    # Authenticate the categorical payload before interpreting it or validating the
    # collection. Projected metric labels below are calculations, never owner receipts.
    approval = sheet.get("approval")
    if approval is not None or any(
        row.get("judgment") is not None for row in sheet.get("judgments", [])
    ):
        if not isinstance(approval, dict) or not approval_sha256:
            raise ValueError("Recorded owner approval digest required for labeled judgments")
        if precision.baseline.stable_digest(approval) != approval_sha256:
            raise ValueError("Owner approval receipt digest differs")
        if set(approval) != {"owner", "date", "decision", "decision_reference", "payload_sha256"}:
            raise ValueError("Owner approval receipt schema differs")
        if any(not isinstance(value, str) or not value.strip() for value in approval.values()):
            raise ValueError("Owner approval evidence missing")
        if approval["payload_sha256"] != precision.baseline.stable_digest(
            precision.judgment_payload(sheet)
        ):
            raise ValueError("Owner approval payload digest differs")
    elif approval_sha256 is not None:
        raise ValueError("Owner approval receipt missing")
    expected = prepare_judgments(collection)
    if set(sheet) != set(expected):
        raise ValueError("Judgment sheet schema differs")
    for key in ("rubric", "rubric_sha256"):
        if sheet[key] != expected[key]:
            raise ValueError(f"Judgment {key} differs")
    for row in sheet["judgments"]:
        if set(row) != {
            "query_id",
            "query",
            "iri",
            "label",
            "definition",
            "aliases",
            "parents",
            "judgment",
            "rationale",
        }:
            raise ValueError("Judgment pair identity/metadata differs")
        if row.get("judgment") not in (None, *CATEGORIES):
            raise ValueError("Invalid category label")
    # Reuse the frozen metadata validator with labels cleared; no synthetic approval.
    original = precision.prepare_judgments(collection)
    projected = {
        **sheet,
        "rubric": original["rubric"],
        "rubric_sha256": original["rubric_sha256"],
        "approval": None,
        "judgments": [{**row, "judgment": None} for row in sheet["judgments"]],
    }
    precision.validate_judgments(collection, projected)
    return {(row["query_id"], row["iri"]): row["judgment"] for row in sheet["judgments"]}


def project_labels(labels, metric):
    if metric not in ("strict", "expanded"):
        raise ValueError("Unknown precision metric")
    relevant = {"direct_match"}
    if metric == "expanded":
        relevant.update(("child", "parent", "relationship"))
    return {
        key: value
        if value in (None, "uncertain")
        else ("relevant" if value in relevant else "irrelevant")
        for key, value in labels.items()
    }


def run_score(collection, sheet, approval_sha256=None):
    labels = validate_judgments(collection, sheet, approval_sha256)
    precision.validate_collection(collection)
    original_ids = [case["id"] for case in precision.baseline.load_fixtures()["cases"]]
    counts = list(labels.values())
    return {
        "schema_version": 1,
        "collection_sha256": precision.baseline.stable_digest(collection),
        "judgments_sha256": precision.baseline.stable_digest(sheet),
        "approval_sha256": approval_sha256,
        "judgment_payload_sha256": precision.baseline.stable_digest(
            precision.judgment_payload(sheet)
        ),
        "rubric": RUBRIC,
        "rubric_sha256": precision.baseline.stable_digest(RUBRIC),
        "scoring_source_sha256": {
            **precision.source_hashes(),
            str(Path(__file__).relative_to(precision.ROOT)): precision.baseline.file_digest(
                Path(__file__)
            ),
        },
        "coverage": {
            "pooled_pairs": len(labels),
            **{category: counts.count(category) for category in CATEGORIES},
            "missing": counts.count(None),
            "annotated": sum(value not in (None, "uncertain") for value in counts),
        },
        "category_counts_by_query": {
            case["id"]: {
                **{
                    category: sum(
                        value == category for (qid, _), value in labels.items() if qid == case["id"]
                    )
                    for category in CATEGORIES
                },
                "missing": sum(
                    value is None for (qid, _), value in labels.items() if qid == case["id"]
                ),
            }
            for case in collection["results"]
        },
        "unresolved_pairs": [
            row for row in sheet["judgments"] if row["judgment"] in (None, "uncertain")
        ],
        "limitations": [
            "Categories are owner annotations, not verified ontology edges.",
            "Strict metric exclusions include related concepts, not owner claims of irrelevance.",
            "Purposive small challenge set, not a blind holdout.",
            "Weak nonsense negatives do not establish general precision.",
            "Missing and uncertain judgments contribute only to upper bounds.",
            "No significance, production adoption, or tuning conclusion.",
        ],
        **{
            metric: precision.summarize(
                collection["results"], project_labels(labels, metric), original_ids
            )
            for metric in ("strict", "expanded")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--approval-sha256", help="Digest of independently recorded owner receipt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_score(
        json.loads(args.collection.read_text()),
        json.loads(args.judgments.read_text()),
        args.approval_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["coverage"]))


if __name__ == "__main__":
    main()
