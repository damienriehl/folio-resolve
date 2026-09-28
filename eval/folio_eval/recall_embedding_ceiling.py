"""Offline local embedding recovery ceiling; emits counts and provenance, never passages."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from folio_resolve.embedding import BruteForceIndex, EmbeddingProvider, LocalEmbeddingProvider
from folio_resolve.ontology import Concept

from .leakcheck import Manifest, load_manifest, scan_json_value
from .resolve_labels import load_folio_index
from .selftest import assert_ontology_pin
from .synthesize import load_corpus

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "embedding_recall", ROOT / "benchmarks/embedding_recall.py"
)
assert _SPEC is not None and _SPEC.loader is not None
baseline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(baseline)
verify_model_files = baseline.verify_model_files
DEPTHS = (10, 25, 50, 100)
ARMS = ("whole_passage", "sentence_windows")
TokenCount = Callable[[str], int]


def load_attribution(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Bind the parsed U2 artifact to the exact bytes the caller approved."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("attribution SHA-256 mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported attribution schema")
    if not isinstance(value.get("relations"), list):
        raise ValueError("attribution relations must be a list")
    return value


def load_local_provider(model_path: Path) -> tuple[LocalEmbeddingProvider, TokenCount]:
    """Verify offline settings and every pinned file before constructing the model."""
    if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        raise ValueError("Local model requires HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")
    model_pin = json.loads(baseline.MODEL_FILES_PATH.read_text())
    verify_model_files(model_path, model_pin)
    if not model_path.is_dir():
        raise ValueError("model must be an existing local directory")
    provider = LocalEmbeddingProvider(str(model_path.resolve()))

    def count(text: str) -> int:
        return len(
            provider._model.tokenizer.encode(text, add_special_tokens=True, truncation=False)
        )

    return provider, count


def sentence_windows(text: str, token_count: TokenCount) -> list[str]:
    """Keep sentence windows separate; split oversized sentences without losing the tail.

    The count includes model special tokens. Every emitted window is strictly
    below 256 word pieces. Character splitting also handles unbroken long words.
    """
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.strip()):
        remaining = sentence.strip()
        while remaining:
            if token_count(remaining) < 256:
                pieces.append(remaining)
                break
            low, high = 0, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if token_count(remaining[:middle]) < 256:
                    low = middle
                else:
                    high = middle - 1
            if not low:
                raise ValueError("tokenizer cannot fit a character below the window limit")
            # Prefer a word boundary, while still permitting long single words.
            boundary = remaining.rfind(" ", 0, low + 1)
            cut = boundary if boundary > 0 else low
            piece = remaining[:cut].strip()
            if token_count(piece) >= 256:
                raise ValueError("sentence window exceeds tokenizer limit")
            pieces.append(piece)
            remaining = remaining[cut:].lstrip()
    return pieces


def measure_ceiling(
    concepts: Sequence[Concept],
    passages: Mapping[str, str],
    attribution: Mapping[str, Any],
    provider: EmbeddingProvider,
    token_count: TokenCount,
) -> dict[str, Any]:
    """Use one all-class index; count only U2's never-produced gold as recovered."""
    ordered = sorted(concepts, key=lambda concept: concept.iri)
    ontology_iris = {concept.iri for concept in ordered}
    if len(ontology_iris) != len(ordered):
        raise ValueError("duplicate ontology IRI")
    gold: dict[str, set[str]] = {}
    missing: dict[str, set[str]] = {}
    for row in attribution["relations"]:
        item, iri, stage = row["item_id"], row["iri"], row["stage"]
        if not all(isinstance(value, str) and value for value in (item, iri, stage)):
            raise ValueError("invalid attribution relation")
        if iri in gold.setdefault(item, set()):
            raise ValueError("duplicate attribution relation")
        gold[item].add(iri)
        if stage == "never_produced":
            if iri not in ontology_iris or item not in passages or not passages[item].strip():
                raise ValueError("never-produced relation lacks ontology class or passage")
            missing.setdefault(item, set()).add(iri)
    index = BruteForceIndex(provider)
    index.build(
        [c.iri for c in ordered], [c.label for c in ordered], [c.definition for c in ordered]
    )
    totals = {
        arm: {str(k): dict(recovered_count=0, non_gold_count=0, suggestion_count=0) for k in DEPTHS}
        for arm in ARMS
    }
    per_passage: dict[str, Any] = {}
    for item in sorted(missing):
        whole = [iri for iri, _, _ in index.query(passages[item], top_k=100)]
        scores: dict[str, float] = {}
        windows = sentence_windows(passages[item], token_count)
        for window in windows:
            for iri, _, score in index.query(window, top_k=100):
                scores[iri] = max(scores.get(iri, float("-inf")), score)
        ranked_windows = sorted(scores, key=lambda iri: (-scores[iri], iri))[:100]
        per_passage[item] = {
            "never_produced_count": len(missing[item]),
            "window_count": len(windows),
        }
        for arm, ranking in zip(ARMS, (whole, ranked_windows), strict=True):
            curve = {}
            for depth in DEPTHS:
                proposed = set(ranking[:depth])
                metrics = dict(
                    recovered_count=len(proposed & missing[item]),
                    non_gold_count=len(proposed - gold[item]),
                    suggestion_count=len(proposed),
                )
                curve[str(depth)] = metrics
                for key, value in metrics.items():
                    totals[arm][str(depth)][key] += value
            per_passage[item][arm] = curve
    denominator = sum(map(len, missing.values()))
    best = max(totals[arm]["50"]["recovered_count"] for arm in ARMS)
    return {
        "schema_version": 1,
        "ontology_class_count": len(ordered),
        "affected_passage_count": len(missing),
        "never_produced_count": denominator,
        "passages": per_passage,
        "rankings": totals,
        "r9": {
            "depth": 50,
            "threshold": 0.25,
            "best_recovered_at_50": best,
            "best_recovery_fraction_at_50": best / denominator if denominator else 0.0,
            "qualifies": denominator > 0 and 4 * best >= denominator,
        },
    }


def residual_item_ids(report: Mapping[str, Any]) -> list[str]:
    """Use the globally better depth-50 ranking, breaking ties in ARMS order."""
    arm = max(ARMS, key=lambda name: report["rankings"][name]["50"]["recovered_count"])
    return sorted(
        item
        for item, row in report["passages"].items()
        if row[arm]["100"]["recovered_count"] < row["never_produced_count"]
    )


def write_report(path: Path, report: Mapping[str, Any], manifest: Manifest, salt: bytes) -> None:
    """Leak-scan before creating or replacing any output."""
    if scan_json_value(report, manifest, salt):
        raise ValueError("leak check failed for embedding ceiling JSON")
    payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attribution", type=Path, required=True)
    parser.add_argument("--attribution-sha256", required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--residual-item-ids", type=Path, help="Write JSON passage IDs for U6 --item-ids"
    )
    args = parser.parse_args(argv)
    from .recall_report import preflight

    manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    preflight(manifest, salt)
    attribution = load_attribution(args.attribution, args.attribution_sha256)
    corpus = load_corpus(args.corpus_manifest)
    fingerprint = attribution.get("fingerprint", {})
    for key, expected in (
        ("corpus_content_sha256", corpus.manifest.content_sha256),
        ("ontology_cache_sha256", corpus.manifest.ontology_cache_sha256),
    ):
        if fingerprint.get(key) != expected:
            raise ValueError("attribution corpus or ontology fingerprint mismatch")
    if not corpus.manifest.scoreable:
        raise ValueError("corpus manifest is not scoreable")
    pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    dictionary, ontology_sha256, _ = load_folio_index()
    concepts = baseline.load_corpus(pin.path, pin.sha256)
    if ontology_sha256 != pin.sha256 or not dictionary.iris <= {c.iri for c in concepts}:
        raise ValueError("embedding ontology differs from eval ontology")
    eval_concepts = [c for c in concepts if c.iri in dictionary.iris]
    excluded_concept_count = len(concepts) - len(eval_concepts)
    provider, count = load_local_provider(args.model_path)
    report = measure_ceiling(
        eval_concepts,
        {item.item_id: item.text for item in corpus.scoreable_items},
        attribution,
        provider,
        count,
    )
    model_pin = json.loads(baseline.MODEL_FILES_PATH.read_text())
    report["attribution_sha256"] = args.attribution_sha256
    report["ontology_sha256"] = ontology_sha256
    report["excluded_concept_count"] = excluded_concept_count
    report["model_revision"] = model_pin["revision"]
    report["model_files_sha256"] = model_pin["files"]
    residual = residual_item_ids(report)
    if args.residual_item_ids:
        if args.residual_item_ids.resolve() == args.output.resolve():
            raise ValueError("residual IDs and report need distinct output paths")
        if scan_json_value(residual, manifest, salt):
            raise ValueError("leak check failed for residual item IDs; inputs are intact")
    write_report(args.output, report, manifest, salt)
    if args.residual_item_ids:
        from .leakcheck import _atomic_write_text

        _atomic_write_text(args.residual_item_ids, json.dumps(residual) + "\n")
    return 0
