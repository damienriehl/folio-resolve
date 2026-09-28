"""Offline uncapped retrieval collection for recall-loss attribution.

Collection and validated assembly are separate from the pure attribution math
added by U2. Checkpoints contain lifecycle evidence, never source passages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence, Set
from pathlib import Path
from typing import Any, Protocol

from .answer_rule import CandidateLike, load_config
from .grade import DEFAULT_FLOOR, GraderVote, _resolve_vote
from .leakcheck import load_manifest, scan_json_value
from .resolve_labels import LabelIndex, load_folio_index
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
from .verifier_depth import depth_curve

ROOT = Path(__file__).resolve().parents[2]


STAGES = (
    "top_100",
    "rank_101_200",
    "rank_below_200",
    "blocklist",
    "place_gate",
    "short_label_gate",
    "score_floor",
    "never_produced",
    "ontology_absent",
)


def resolve_grader_votes(
    votes: Sequence[GraderVote],
    dictionary: LabelIndex,
) -> dict[str, list[Mapping[str, float]]]:
    """Use grading's resolver, including its maximum confidence per IRI rule."""
    resolved: dict[str, list[Mapping[str, float]]] = {}
    seen: set[tuple[str, str]] = set()
    for vote in votes:
        key = (vote.item_id, vote.grader_id)
        if key in seen:
            raise ValueError("duplicate grader vote")
        seen.add(key)
        if any(isinstance(c, bool) or not 0 <= c <= 1 for c in vote.concepts.values()):
            raise ValueError("invalid grader confidence")
        resolved.setdefault(vote.item_id, []).append(_resolve_vote(vote, dictionary).confidences)
    return resolved


def attribute_relations(
    survivors: Mapping[str, Sequence[CandidateLike]],
    traces: Mapping[str, Sequence[AttributionTrace]],
    gold: Mapping[str, Set[str]],
    ontology_iris: Set[str],
    resolved_votes: Mapping[str, Sequence[Mapping[str, float]]],
    strata: Mapping[str, str],
) -> dict[str, Any]:
    """Attribute scoreable relations; empty gold and non-gold items add nothing.

    Survivor order is the adapter's order, not a new ranking. Shares use the
    whole gold population of each stratum (or overall), including ontology misses.
    Vote maps must come from independent recorded graders via resolve_grader_votes.
    """
    scoreable = {key: iris for key, iris in gold.items() if iris}
    relations: list[dict[str, Any]] = []
    for item_id, iris in sorted(scoreable.items()):
        ranks = {c.iri: rank for rank, c in enumerate(survivors[item_id], 1)}
        if len(ranks) != len(survivors[item_id]):
            raise ValueError("duplicate survivor IRI")
        evidence = {t.iri: t for t in traces.get(item_id, ())}
        if len(evidence) != len(traces.get(item_id, ())):
            raise ValueError("duplicate trace IRI")
        votes = resolved_votes.get(item_id, ())
        if len(votes) > 3:
            raise ValueError("more than three grader votes")
        for iri in sorted(iris):
            agreement = sum(vote.get(iri, 0.0) >= DEFAULT_FLOOR for vote in votes)
            if agreement < 2:
                raise ValueError(f"{item_id}: gold relation has fewer than two qualifying votes")
            rank = ranks.get(iri)
            trace = evidence.get(iri)
            if iri not in ontology_iris:
                if rank is not None or trace is not None:
                    raise ValueError("ontology-absent IRI has retrieval evidence")
                stage = "ontology_absent"
            elif rank is not None:
                if trace is not None and trace.gate_disposition != "survived":
                    raise ValueError("survivor has removed trace")
                stage = (
                    "top_100"
                    if rank <= 100
                    else "rank_101_200"
                    if rank <= 200
                    else "rank_below_200"
                )
            elif trace is not None:
                stage = trace.gate_disposition
                if stage not in STAGES[3:7]:
                    raise ValueError("non-survivor trace must name a removal gate")
            else:
                stage = "never_produced"
            relations.append(
                dict(
                    item_id=item_id,
                    iri=iri,
                    stratum_id=strata[item_id],
                    stage=stage,
                    rank=rank,
                    agreement=agreement,
                )
            )

    def counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            stage: {
                "count": sum(r["stage"] == stage for r in rows),
                "share": sum(r["stage"] == stage for r in rows) / len(rows) if rows else 0.0,
                "by_agreement": {
                    str(n): sum(r["stage"] == stage and r["agreement"] == n for r in rows)
                    for n in (2, 3)
                },
            }
            for stage in STAGES
        }

    distances = Counter(r["rank"] - 200 for r in relations if r["stage"] == "rank_below_200")
    return {
        "schema_version": 1,
        "relations": relations,
        "gold_relation_count": len(relations),
        "scoreable_item_count": len(scoreable),
        "overall": counts(relations),
        "by_stratum": {
            s: counts([r for r in relations if r["stratum_id"] == s])
            for s in sorted({r["stratum_id"] for r in relations})
        },
        "rank_distance_below_200": {str(d): distances[d] for d in sorted(distances)},
        "depth_curve": depth_curve(survivors, scoreable, depths=(100, 200)),
    }


def reconcile_depth(
    survivors: Mapping[str, Sequence[CandidateLike]],
    gold: Mapping[str, Set[str]],
    committed: Mapping[str, Any],
    fingerprint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed against the committed cumulative depth counts."""
    computed = depth_curve(survivors, {k: v for k, v in gold.items() if v}, depths=(100, 200))
    _verify_depth(computed, committed, fingerprint or {})
    return computed


def _verify_depth(
    computed: Mapping[str, Any], committed: Mapping[str, Any], fingerprint: Mapping[str, Any]
) -> None:
    for key in ("gold_relation_count", "scoreable_item_count"):
        if type(committed.get(key)) is not int or computed.get(key) != committed[key]:
            raise ValueError(f"depth reconciliation population mismatch: {key}")
    for key in (
        "corpus_content_sha256",
        "nomatch_content_sha256",
        "ontology_cache_sha256",
        "answer_rule_config_sha256",
        "adapter_sha256",
    ):
        if key in committed and fingerprint.get(key) != committed[key]:
            raise ValueError(f"depth reconciliation fingerprint mismatch: {key}")
    for depth in ("100", "200"):
        actual = computed["curve"][depth]["retrieved_gold_count"]
        expected = committed["curve"][depth]["retrieved_gold_count"]
        if type(expected) is not int or actual != expected:
            raise ValueError(f"depth reconciliation failed at {depth}: {actual} != {expected}")


def verify_finalized_attribution(
    path: Path, committed: Mapping[str, Any], expected_sha256: str | None = None
) -> None:
    """Read-only reconciliation of finalized bytes, including historical adapter provenance."""
    raw = path.read_bytes()
    expected = expected_sha256 or Path(str(path) + ".sha256").read_text().split()[0]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("attribution SHA-256 mismatch")
    report = json.loads(raw)
    rows = report["relations"]
    pairs = {(r["item_id"], r["iri"]) for r in rows}
    if len(pairs) != len(rows):
        raise ValueError("depth reconciliation duplicate relation")
    computed = dict(
        gold_relation_count=len(rows),
        scoreable_item_count=len({r["item_id"] for r in rows}),
        curve={
            str(n): {
                "retrieved_gold_count": sum(r["rank"] is not None and r["rank"] <= n for r in rows)
            }
            for n in (100, 200)
        },
    )
    for key in ("gold_relation_count", "scoreable_item_count"):
        if report.get(key) != computed[key]:
            raise ValueError("depth reconciliation artifact population mismatch")
    fingerprint = dict(report["fingerprint"])
    if "adapter_sha256" in committed and "adapter_sha256" not in fingerprint:
        # Legacy artifacts bind the adapter through the checkpoint commit; never
        # substitute the current checkout when verifying an earlier live run.
        head = fingerprint.get("git_head", "")
        if (
            not isinstance(head, str)
            or len(head) != 40
            or any(c not in "0123456789abcdef" for c in head)
        ):
            raise ValueError("depth reconciliation invalid adapter commit")
        adapter = subprocess.run(
            ["git", "show", f"{head}:eval/folio_eval/synthetic_score.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        fingerprint["adapter_sha256"] = hashlib.sha256(adapter).hexdigest()
    _verify_depth(computed, committed, fingerprint)
    _verify_depth(report["depth_curve"], committed, fingerprint)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def finalize_attribution(
    corpus: LoadedCorpus,
    store: AttributionCheckpointStore,
    assembled: Mapping[tuple[SyntheticItemKind, str], AttributionAdapterResult],
    dictionary: LabelIndex,
    committed: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble only scoreable gold and bind the numeric artifact to its checkpoint."""
    items = tuple(item for item in corpus.scoreable_items if not item.is_nomatch)
    ids = {item.item_id for item in items}
    records = tuple(row for row in corpus.gold_item_records() if row.item_id in ids)
    gold = {row.item_id: row.gold_iris for row in records}
    survivors = {key: assembled[("scoreable", key)].candidates for key in gold}
    provenance = dict(
        store.fingerprint.to_json(),
        adapter_sha256=hashlib.sha256(
            Path(__file__).with_name("synthetic_score.py").read_bytes()
        ).hexdigest(),
    )
    reconcile_depth(survivors, gold, committed, provenance)
    votes = []
    for item in items:
        raw_votes = item.provenance.get("grader_votes", [])
        if not isinstance(raw_votes, list):
            raise ValueError("malformed recorded grader votes")
        for raw in raw_votes:
            if not isinstance(raw, dict) or raw.get("item_id") != item.item_id:
                raise ValueError("recorded grader vote item mismatch")
            votes.append(GraderVote(**raw))
    report = attribute_relations(
        survivors,
        {key: assembled[("scoreable", key)].traces for key in gold},
        gold,
        dictionary.iris,
        resolve_grader_votes(votes, dictionary),
        {row.item_id: row.stratum_id for row in records},
    )
    report["checkpoint_fingerprint_sha256"] = store.fingerprint.content_sha256()
    report["fingerprint"] = provenance
    return report


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
    parser.add_argument("--corpus-manifest", type=Path)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "eval/synthetic/answer_rule_config_synthetic_v1.json"
    )
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--verify-finalized", type=Path)
    parser.add_argument("--attribution-sha256")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--output", type=Path, help="default: checkpoint-dir/attribution.json")
    args = parser.parse_args(argv)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard-index must be within the positive shard-count")
    from .recall_report import preflight

    manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    preflight(manifest, salt)
    if args.verify_finalized:
        committed = json.loads((ROOT / "docs/benchmarks/verifier-shortlist-depth.json").read_text())
        verify_finalized_attribution(args.verify_finalized, committed, args.attribution_sha256)
        return 0
    if not args.corpus_manifest or not args.checkpoint_dir:
        parser.error("collection requires --corpus-manifest and --checkpoint-dir")
    ensure_hash_seed()
    corpus = load_corpus(args.corpus_manifest)
    config = load_config(args.config)
    _assert_config(corpus, config)
    if not corpus.manifest.scoreable:
        raise ValueError("corpus manifest is not scoreable")
    fingerprint = build_checkpoint_fingerprint(corpus, config, repo_root=ROOT)
    store = AttributionCheckpointStore.create(
        args.checkpoint_dir,
        fingerprint=fingerprint,
        shard_count=args.shard_count,
        expected_item_count=len(corpus.scoreable_items) + len(corpus.nomatch_items),
    )
    assembled = collect_checkpoint(
        corpus,
        store,
        shard_index=args.shard_index,
        adapter_factory=lambda: make_adapter(corpus),
        finalize_only=args.finalize_only,
    )
    if assembled is None:
        return 0
    assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    dictionary, ontology_sha256, _ = load_folio_index()
    if ontology_sha256 != corpus.manifest.ontology_cache_sha256:
        raise ValueError("attribution ontology fingerprint mismatch")
    committed = json.loads((ROOT / "docs/benchmarks/verifier-shortlist-depth.json").read_text())
    report = finalize_attribution(corpus, store, assembled, dictionary, committed)
    if scan_json_value(report, manifest, salt):
        raise ValueError("leak check failed for attribution JSON")
    payload = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    output = args.output or args.checkpoint_dir / "attribution.json"
    _atomic_write(output, payload)
    _atomic_write(
        output.with_suffix(output.suffix + ".sha256"),
        (hashlib.sha256(payload).hexdigest() + "\n").encode(),
    )
    return 0
