"""Offline uncapped retrieval collection for recall-loss attribution.

Collection and validated assembly are separate from the pure attribution math
added by U2. Checkpoints contain lifecycle evidence, never source passages.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .answer_rule import load_config
from .leakcheck import load_manifest, scan_text
from .selftest import assert_ontology_pin, ensure_hash_seed
from .synthesize import LoadedCorpus, SyntheticItem, load_corpus
from .synthetic_checkpoint import (
    AttributionAdapterResult,
    AttributionCheckpointStore,
    AttributionTrace,
    CheckpointError,
    build_checkpoint_fingerprint,
    checkpoint_item_key,
    shard_for_item,
)
from .synthetic_contract import SyntheticItemKind
from .synthetic_score import AdapterResult, DocumentAdapter, _assert_config

ROOT = Path(__file__).resolve().parents[2]
FIXED_REPORT_PROSE = "# Recall attribution\n\nUncapped survivors and gate lifecycle evidence.\n"


class AttributionAdapter(Protocol):
    def adapt(self, passage: str) -> AdapterResult: ...


def collect_checkpoint(
    corpus: LoadedCorpus,
    store: AttributionCheckpointStore,
    *,
    shard_index: int,
    adapter_factory: Callable[[], AttributionAdapter],
    finalize_only: bool = False,
) -> dict[tuple[SyntheticItemKind, str], AttributionAdapterResult] | None:
    """Resume assigned items; explicit finalization loads all shards without adapting."""
    if not 0 <= shard_index < store.shard_count:
        raise ValueError("shard-index must be within shard-count")
    groups: tuple[tuple[SyntheticItemKind, Sequence[SyntheticItem]], ...] = (
        ("scoreable", corpus.scoreable_items),
        ("nomatch", corpus.nomatch_items),
    )
    items = [(kind, item) for kind, group in groups for item in group]
    keys = {checkpoint_item_key(kind, item.item_id) for kind, item in items}
    if len(keys) != len(items) or len(items) != store.expected_item_count:
        raise CheckpointError("attribution corpus item count or identity mismatch")
    if not finalize_only:
        adapter = None
        for kind, item in items:
            if (
                shard_for_item(checkpoint_item_key(kind, item.item_id), store.shard_count)
                != shard_index
            ):
                continue
            if store.maybe_load_item(kind, item.item_id) is not None:
                continue
            if adapter is None:
                adapter = adapter_factory()
            result = adapter.adapt(item.text)
            store.write_item(
                kind,
                item.item_id,
                candidates=result.candidates,
                raw_candidate_count=result.raw_candidate_count,
                suppression_counters=result.suppression_counters,
                traces=tuple(
                    AttributionTrace(
                        t.iri,
                        t.gate_disposition,
                        t.gate_reason,
                        t.pre_gate_score,
                        t.post_gate_score,
                    )
                    for t in result.traces
                ),
            )
        if store.shard_count > 1:
            return None
    assembled = {(kind, item.item_id): store.load_item(kind, item.item_id) for kind, item in items}
    if {path.stem for path in store.item_paths()} != keys:
        raise CheckpointError("attribution checkpoint contains unexpected items")
    return assembled


def make_adapter(corpus: LoadedCorpus) -> DocumentAdapter:
    assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    from folio import FOLIO

    from folio_resolve.ontology import FolioPythonProvider

    return DocumentAdapter(FolioPythonProvider(_folio=FOLIO()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "eval/synthetic/answer_rule_config_synthetic_v1.json"
    )
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args(argv)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard-index must be within the positive shard-count")
    ensure_hash_seed()
    corpus = load_corpus(args.corpus_manifest)
    config = load_config(args.config)
    _assert_config(corpus, config)
    if not corpus.manifest.scoreable:
        raise ValueError("corpus manifest is not scoreable")
    fingerprint = build_checkpoint_fingerprint(corpus, config, repo_root=ROOT)
    manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    if scan_text(FIXED_REPORT_PROSE, manifest, salt):
        raise ValueError("leak check failed for fixed report prose before collection")
    store = AttributionCheckpointStore.create(
        args.checkpoint_dir,
        fingerprint=fingerprint,
        shard_count=args.shard_count,
        expected_item_count=len(corpus.scoreable_items) + len(corpus.nomatch_items),
    )
    collect_checkpoint(
        corpus,
        store,
        shard_index=args.shard_index,
        adapter_factory=lambda: make_adapter(corpus),
        finalize_only=args.finalize_only,
    )
    return 0
