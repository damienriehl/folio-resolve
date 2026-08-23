"""Power-loss-safe item sharding and finalization for the U10 comparison pilot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .answer_rule import load_config
from .comparison import (
    StackRun,
    _file_fingerprint,
    _git_repository_state,
    build_comparison,
    emit_items_file,
    load_public_comparison_metadata,
    write_comparison,
    write_stage_snapshots,
)
from .downstream import FOLIO_RESOLVE_ROOT
from .intake import sha256_bytes
from .leakcheck import load_manifest
from .synthesize import LoadedCorpus, load_corpus
from .synthetic_checkpoint import _atomic_create

PILOT_CHECKPOINT_KIND = "synthetic-comparison-pilot-checkpoint"
PILOT_CHECKPOINT_VERSION = 1
DEFAULT_LIMIT = 30


class PilotCheckpointError(RuntimeError):
    """A pilot shard or its fingerprint is missing, corrupt, or incompatible."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item_key(item_id: str) -> str:
    return hashlib.sha256(item_id.encode()).hexdigest()


def _pilot_ids(corpus: LoadedCorpus, limit: int) -> tuple[str, ...]:
    if limit < 1 or limit > len(corpus.scoreable_items):
        raise PilotCheckpointError("pilot scoreable limit is outside the corpus")
    return tuple(
        item.item_id for item in (*corpus.scoreable_items[:limit], *corpus.nomatch_items)
    )


def _fingerprint(
    *,
    corpus: LoadedCorpus,
    config_path: Path,
    leak_manifest_path: Path,
    public_metadata_path: Path,
    mapper_root: Path,
    enrich_root: Path,
    limit: int,
) -> dict[str, object]:
    config = load_config(config_path)
    return {
        "answer_rule_config_sha256": config.content_sha256(),
        "candidate_repository": _git_repository_state(FOLIO_RESOLVE_ROOT),
        "corpus_content_sha256": corpus.manifest.content_sha256,
        "enrich_repository": _git_repository_state(enrich_root),
        "folio_python_lock_sha256": _sha256_file(FOLIO_RESOLVE_ROOT / "uv.lock"),
        "folio_python_version": importlib.metadata.version("folio-python"),
        "folio_resolve_version": importlib.metadata.version("folio-resolve"),
        "leak_manifest_sha256": _sha256_file(leak_manifest_path),
        "mapper_repository": _git_repository_state(mapper_root),
        "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        "public_metadata_sha256": _sha256_file(public_metadata_path),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED", ""),
        "python_version": platform.python_version(),
        "scoreable_limit": limit,
    }


def _checkpoint_manifest(
    *, fingerprint: Mapping[str, object], item_ids: Sequence[str]
) -> dict[str, object]:
    return {
        "expected_item_count": len(item_ids),
        "fingerprint": dict(fingerprint),
        "fingerprint_sha256": sha256_bytes(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
        ),
        "item_ids": list(item_ids),
        "kind": PILOT_CHECKPOINT_KIND,
        "schema_version": PILOT_CHECKPOINT_VERSION,
    }


def _create_or_validate_manifest(path: Path, expected: Mapping[str, object]) -> None:
    _atomic_create(path, expected)
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("pilot checkpoint manifest is corrupt") from exc
    if observed != expected:
        raise PilotCheckpointError("pilot checkpoint fingerprint does not match this run")


def _load_shard(
    path: Path,
    item_id: str,
    fingerprint: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError(f"pilot shard is corrupt: {path}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "synthetic_comparison":
        raise PilotCheckpointError(f"pilot shard has the wrong contract: {path}")
    if payload.get("run_kind") != "shard":
        raise PilotCheckpointError(f"pilot shard has the wrong run_kind: {path}")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PilotCheckpointError(f"pilot shard provenance is malformed: {path}")
    cohort = provenance.get("cohort_selection")
    if not isinstance(cohort, dict):
        raise PilotCheckpointError(f"pilot shard cohort is malformed: {path}")
    observed_ids = [
        *cohort.get("scoreable_item_ids", []),
        *cohort.get("nomatch_item_ids", []),
    ]
    if observed_ids != [item_id]:
        raise PilotCheckpointError(f"pilot shard item mismatch: {path}")
    stacks = payload.get("stacks")
    if not isinstance(stacks, dict) or set(stacks) != {
        "folio-enrich:incumbent",
        "folio-mapper:incumbent",
        "folio-resolve:candidate",
    }:
        raise PilotCheckpointError(f"pilot shard stack set is incomplete: {path}")
    for key, stack in stacks.items():
        if not isinstance(stack, dict) or set(stack.get("items", {})) != {item_id}:
            raise PilotCheckpointError(f"pilot shard {key} item rows are incomplete: {path}")
        snapshot = stack.get("stage_snapshot", {})
        if not isinstance(snapshot, dict) or set(snapshot.get("by_item", {})) != {item_id}:
            raise PilotCheckpointError(f"pilot shard {key} stages are incomplete: {path}")
    if fingerprint is not None:
        corpus = payload.get("corpus", {})
        if not isinstance(corpus, dict):
            raise PilotCheckpointError(f"pilot shard corpus is malformed: {path}")
        config_selection = provenance.get("config_selection", {})
        if not isinstance(config_selection, dict):
            raise PilotCheckpointError(f"pilot shard config selection is malformed: {path}")
        candidate_versions = stacks["folio-resolve:candidate"].get("versions", {})
        if not isinstance(candidate_versions, dict):
            raise PilotCheckpointError(f"pilot shard versions are malformed: {path}")
        checks = {
            "corpus content": (
                corpus.get("content_sha256"), fingerprint["corpus_content_sha256"]
            ),
            "nomatch content": (
                corpus.get("nomatch_content_sha256"), fingerprint["nomatch_content_sha256"]
            ),
            "answer-rule config": (
                config_selection.get("answer_rule_config_sha256"),
                fingerprint["answer_rule_config_sha256"],
            ),
            "folio-python version": (
                payload.get("folio_python_version"), fingerprint["folio_python_version"]
            ),
            "folio-resolve version": (
                candidate_versions.get("folio-resolve"),
                fingerprint["folio_resolve_version"],
            ),
            "candidate repository": (
                stacks["folio-resolve:candidate"].get("repository"),
                fingerprint["candidate_repository"],
            ),
            "enrich repository": (
                stacks["folio-enrich:incumbent"].get("repository"),
                fingerprint["enrich_repository"],
            ),
            "mapper repository": (
                stacks["folio-mapper:incumbent"].get("repository"),
                fingerprint["mapper_repository"],
            ),
        }
        drifted = [name for name, (observed, expected) in checks.items() if observed != expected]
        if drifted:
            raise PilotCheckpointError(
                f"pilot shard fingerprint drifted ({', '.join(drifted)}): {path}"
            )
    return payload


def _shard_paths(root: Path, item_id: str) -> tuple[Path, Path, Path]:
    shard_root = root / "items" / _item_key(item_id)
    return (
        shard_root / "report.json",
        shard_root / "items.jsonl",
        shard_root / "stages",
    )


def _run_shard(
    args: argparse.Namespace, item_id: str, fingerprint: Mapping[str, object]
) -> None:
    report, items, stages = _shard_paths(args.checkpoint_dir, item_id)
    if report.exists():
        _load_shard(report, item_id, fingerprint)
        return
    command = [
        sys.executable,
        "eval/run_downstream.py",
        "run_synthetic_comparison",
        "--corpus-manifest",
        str(args.corpus_manifest),
        "--config",
        str(args.config),
        "--out",
        str(report),
        "--items",
        str(items),
        "--row-snapshot-dir",
        str(stages),
        "--leak-manifest",
        str(args.leak_manifest),
        "--salt-file",
        str(args.salt_file),
        "--public-metadata",
        str(args.public_metadata),
        "--mapper-root",
        str(args.mapper_root),
        "--enrich-root",
        str(args.enrich_root),
        "--item-id",
        item_id,
    ]
    completed = subprocess.run(
        command,
        cwd=FOLIO_RESOLVE_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
    )
    if completed.returncode:
        raise PilotCheckpointError(
            f"comparison shard failed for {item_id!r} (rc={completed.returncode})"
        )
    _load_shard(report, item_id, fingerprint)


def _merge_stack_runs(shards: Sequence[Mapping[str, Any]]) -> list[StackRun]:
    runs: list[StackRun] = []
    for key in sorted(shards[0]["stacks"]):
        first = shards[0]["stacks"][key]
        rows: dict[str, frozenset[str]] = {}
        stages: dict[str, Mapping[str, object]] = {}
        for shard in shards:
            stack = shard["stacks"][key]
            for field in ("stack", "lane", "versions", "config", "repository"):
                if stack[field] != first[field]:
                    raise PilotCheckpointError(f"pilot shard {key} {field} drifted")
            for item_id, iris in stack["items"].items():
                if item_id in rows:
                    raise PilotCheckpointError(f"duplicate pilot item in {key}: {item_id}")
                rows[item_id] = frozenset(iris)
                stages[item_id] = stack["stage_snapshot"]["by_item"][item_id]
        runs.append(
            StackRun(
                stack=first["stack"],
                lane=first["lane"],
                folio_resolve_version=first["versions"]["folio-resolve"],
                folio_python_version=first["versions"]["folio-python"],
                config=first["config"],
                rows=rows,
                stages=stages,
                invocation=tuple(first["invocation"]["argv"]),
                invocation_working_directory=first["invocation"]["working_directory"],
                repository=first["repository"],
            )
        )
    return runs


def _finalize(
    args: argparse.Namespace,
    corpus: LoadedCorpus,
    item_ids: Sequence[str],
    manifest: Mapping[str, object],
) -> None:
    fingerprint = manifest.get("fingerprint")
    if not isinstance(fingerprint, Mapping):
        raise PilotCheckpointError("pilot checkpoint fingerprint is malformed")
    shard_payloads = [
        _load_shard(
            _shard_paths(args.checkpoint_dir, item_id)[0],
            item_id,
            fingerprint,
        )
        for item_id in item_ids
    ]
    runs = _merge_stack_runs(shard_payloads)
    leak_manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    combined_items = args.checkpoint_dir / "final-items.jsonl"
    emit_items_file(
        corpus,
        combined_items,
        limit=args.limit,
        include_nomatch=True,
        leak_manifest=leak_manifest,
        salt=salt,
    )
    snapshot_files = write_stage_snapshots(
        runs,
        args.checkpoint_dir / "final-stages",
        leak_manifest=leak_manifest,
        salt=salt,
    )
    comparison_invocation = {
        "kind": "equivalent_checkpoint_finalization",
        "argv": [
            sys.executable,
            "eval/run_downstream.py",
            "run_synthetic_comparison",
            "--corpus-manifest",
            "eval/synthetic/corpus_v1.manifest.json",
            "--config",
            "eval/synthetic/answer_rule_config_synthetic_v1.json",
            "--out",
            str(args.out),
            "--items",
            str(combined_items),
            "--row-snapshot-dir",
            str(args.checkpoint_dir / "final-stages"),
            "--leak-manifest",
            "eval/synthetic/firm-surface-manifest-v1.json",
            "--salt-file",
            str(args.salt_file),
            "--limit",
            str(args.limit),
        ],
        "working_directory": str(FOLIO_RESOLVE_ROOT),
        "environment": {"PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "")},
    }
    payload = build_comparison(
        corpus,
        runs,
        load_config(args.config),
        limit=args.limit,
        include_nomatch=True,
        comparison_invocation=comparison_invocation,
        stage_snapshot_files=snapshot_files,
        items_file=_file_fingerprint(combined_items, root=FOLIO_RESOLVE_ROOT),
    )
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PilotCheckpointError("final comparison provenance is malformed")
    provenance["checkpoint"] = {
        "completed_shards": len(shard_payloads),
        "manifest_sha256": _sha256_file(args.checkpoint_dir / "manifest.json"),
        "shard_report_sha256": {
            _item_key(item_id): _sha256_file(_shard_paths(args.checkpoint_dir, item_id)[0])
            for item_id in item_ids
        },
    }
    write_comparison(
        args.out,
        payload,
        leak_manifest,
        salt,
        public_metadata=load_public_comparison_metadata(args.public_metadata),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--public-metadata", type=Path, required=True)
    parser.add_argument("--mapper-root", type=Path, required=True)
    parser.add_argument("--enrich-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--max-new-items", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise PilotCheckpointError("comparison pilot requires PYTHONHASHSEED=0")
    corpus = load_corpus(args.corpus_manifest)
    item_ids = _pilot_ids(corpus, args.limit)
    fingerprint = _fingerprint(
        corpus=corpus,
        config_path=args.config,
        leak_manifest_path=args.leak_manifest,
        public_metadata_path=args.public_metadata,
        mapper_root=args.mapper_root,
        enrich_root=args.enrich_root,
        limit=args.limit,
    )
    manifest = _checkpoint_manifest(fingerprint=fingerprint, item_ids=item_ids)
    _create_or_validate_manifest(args.checkpoint_dir / "manifest.json", manifest)
    if not args.finalize_only:
        completed_before = sum(
            _shard_paths(args.checkpoint_dir, item_id)[0].exists() for item_id in item_ids
        )
        allowance = args.max_new_items
        for item_id in item_ids:
            report = _shard_paths(args.checkpoint_dir, item_id)[0]
            if report.exists():
                _load_shard(report, item_id, fingerprint)
                continue
            if allowance is not None and allowance <= 0:
                break
            ordinal = sum(
                _shard_paths(args.checkpoint_dir, candidate_id)[0].exists()
                for candidate_id in item_ids
            ) + 1
            print(f"pilot shard {ordinal}/{len(item_ids)}: starting", flush=True)
            _run_shard(args, item_id, fingerprint)
            print(f"pilot shard {ordinal}/{len(item_ids)}: complete", flush=True)
            if allowance is not None:
                allowance -= 1
        completed_after = sum(
            _shard_paths(args.checkpoint_dir, item_id)[0].exists() for item_id in item_ids
        )
        print(f"pilot checkpoint: {completed_after}/{len(item_ids)} complete")
        if completed_after < len(item_ids):
            if args.max_new_items == 0:
                return 0
            if completed_after == completed_before:
                raise PilotCheckpointError("pilot checkpoint made no progress")
            return 0
    _finalize(args, corpus, item_ids, manifest)
    print(f"pilot report: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
