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
    prepare_incumbent,
    write_comparison,
    write_stage_snapshots,
)
from .downstream import FOLIO_RESOLVE_ROOT, enrich_spec, mapper_spec
from .intake import sha256_bytes
from .leakcheck import load_manifest
from .selftest import assert_ontology_pin
from .synthesize import LoadedCorpus, load_corpus
from .synthetic_checkpoint import _atomic_create, fsync_directory

PILOT_CHECKPOINT_KIND = "synthetic-comparison-pilot-checkpoint"
PILOT_CHECKPOINT_VERSION = 1
DEFAULT_LIMIT = 30
INCUMBENT_VERSION = "0.4.0"

_CONSUMER_ENVIRONMENT_PROBE = r"""
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import sysconfig
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


distributions = []
installed_entries = []
editable_entries = []
owned_import_roots = []
resolved_digests = {}
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name", "")
    canonical_name = re.sub(r"[-_.]+", "-", name).lower()
    record = (distribution.read_text("RECORD") or "").encode()
    direct_url_text = distribution.read_text("direct_url.json") or ""
    direct_url = direct_url_text.encode()
    distribution_files = []
    pth_paths = []
    for relative in distribution.files or ():
        path = Path(distribution.locate_file(relative))
        if not path.is_file():
            distribution_files.append([str(relative), "missing", 0])
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        entry = [str(relative), resolved_digests[cache_key], resolved.stat().st_size]
        distribution_files.append(entry)
        installed_entries.append([canonical_name, *entry])
        if path.suffix == ".pth":
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(("#", "import ")):
                    candidate = Path(stripped)
                    if not candidate.is_absolute():
                        candidate = path.parent / candidate
                    if candidate.is_dir():
                        pth_paths.append(candidate.resolve())

    editable_sources = []
    excluded_source_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
    if direct_url_text:
        direct_url_payload = json.loads(direct_url_text)
        if direct_url_payload.get("dir_info", {}).get("editable") is True:
            parsed = urlparse(direct_url_payload.get("url", ""))
            editable_root = Path(unquote(parsed.path)).resolve()
            source_roots = []
            for candidate in pth_paths:
                try:
                    candidate.relative_to(editable_root)
                except ValueError as exc:
                    raise RuntimeError("editable .pth import root is outside its project") from exc
                source_roots.append(candidate)
                owned_import_roots.append(candidate)
            if not source_roots:
                pyproject = editable_root / "pyproject.toml"
                if pyproject.is_file():
                    project_config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                    hatch_packages = (
                        project_config.get("tool", {})
                        .get("hatch", {})
                        .get("build", {})
                        .get("targets", {})
                        .get("wheel", {})
                        .get("packages", [])
                    )
                    source_roots.extend(
                        (editable_root / package).resolve()
                        for package in hatch_packages
                        if isinstance(package, str) and (editable_root / package).is_dir()
                    )
            if not source_roots and (editable_root / "src").is_dir():
                source_roots.append((editable_root / "src").resolve())
            if not source_roots:
                package_names = {
                    canonical_name.replace("-", "_"),
                    canonical_name.split("-", 1)[0],
                }
                source_roots.extend(
                    candidate.resolve()
                    for package_name in package_names
                    for candidate in (editable_root / package_name,)
                    if candidate.is_dir()
                )
            for source_root in sorted(set(source_roots)):
                for path in sorted(source_root.rglob("*")):
                    if (
                        not path.is_file()
                        or any(part in excluded_source_parts for part in path.parts)
                        or path.suffix in {".pyc", ".pyo"}
                    ):
                        continue
                    resolved = path.resolve()
                    cache_key = str(resolved)
                    if cache_key not in resolved_digests:
                        resolved_digests[cache_key] = digest_file(resolved)
                    entry = [
                        path.relative_to(editable_root).as_posix(),
                        resolved_digests[cache_key],
                        resolved.stat().st_size,
                    ]
                    editable_sources.append(entry)
                    editable_entries.append([canonical_name, *entry])
    elif pth_paths:
        raise RuntimeError("unowned .pth import root is not allowed")
    distributions.append([
        canonical_name,
        distribution.version,
        digest_bytes(record),
        digest_bytes(direct_url),
        digest_bytes(json.dumps(distribution_files, separators=(",", ":")).encode()),
        digest_bytes(json.dumps(editable_sources, separators=(",", ":")).encode()),
    ])
distributions.sort()
installed_entries.sort()
editable_entries.sort()
distribution_payload = json.dumps(
    distributions, ensure_ascii=True, separators=(",", ":")
).encode()
installed_payload = json.dumps(
    installed_entries, ensure_ascii=True, separators=(",", ":")
).encode()
editable_payload = json.dumps(
    editable_entries, ensure_ascii=True, separators=(",", ":")
).encode()

stdlib_roots = {
    Path(path).resolve()
    for key in ("stdlib", "platstdlib")
    if (path := sysconfig.get_path(key))
}
site_roots = {
    Path(path).resolve()
    for key in ("purelib", "platlib")
    if (path := sysconfig.get_path(key))
}
stdlib_zips = {
    root.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    for root in stdlib_roots
}
effective_import_paths = []
for entry in sys.path:
    if not entry:
        raise RuntimeError("unsafe empty import root is not allowed")
    path = Path(entry).resolve()
    allowed = (
        path in site_roots
        or path in stdlib_zips
        or any(path == root or root in path.parents for root in stdlib_roots)
        or any(path == root or root in path.parents for root in owned_import_roots)
    )
    if not allowed:
        raise RuntimeError("unowned effective import root is not allowed")
    effective_import_paths.append(str(path))
effective_import_paths.sort()
import_path_payload = json.dumps(
    effective_import_paths, ensure_ascii=True, separators=(",", ":")
).encode()

try:
    from huggingface_hub.constants import HF_HUB_CACHE
    model_root = Path(HF_HUB_CACHE) / "models--sentence-transformers--all-MiniLM-L6-v2"
except Exception:
    model_root = Path("/__missing_huggingface_cache__")

model_entries = []
if model_root.is_dir():
    for path in sorted(model_root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        model_entries.append([
            path.relative_to(model_root).as_posix(),
            resolved_digests[cache_key],
            resolved.stat().st_size,
        ])
model_payload = json.dumps(model_entries, ensure_ascii=True, separators=(",", ":")).encode()
model_assets_complete = False
model_embedding_dimension = 0
model_snapshot_revision = ""
if os.environ.get("FOLIO_PROBE_REQUIRE_MODEL_ASSETS") == "1":
    ref_path = model_root / "refs" / "main"
    if not ref_path.is_file():
        raise RuntimeError("embedding model cache has no pinned main revision")
    model_snapshot_revision = ref_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", model_snapshot_revision):
        raise RuntimeError("embedding model cache revision is malformed")
    snapshot_root = model_root / "snapshots" / model_snapshot_revision
    if not snapshot_root.is_dir():
        raise RuntimeError("embedding model cache snapshot is missing")
    if any(path.name.endswith(".incomplete") for path in model_root.rglob("*")):
        raise RuntimeError("embedding model cache contains an incomplete download")
    locks_root = model_root.parent / ".locks" / model_root.name
    if locks_root.is_dir() and any(path.is_file() for path in locks_root.rglob("*")):
        raise RuntimeError("embedding model cache has an active download lock")
    if any(path.is_symlink() and not path.exists() for path in snapshot_root.rglob("*")):
        raise RuntimeError("embedding model cache snapshot contains a broken link")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2",
        revision=model_snapshot_revision,
        local_files_only=True,
    )
    dimension = model.get_sentence_embedding_dimension()
    if not isinstance(dimension, int) or dimension < 1:
        raise RuntimeError("embedding model cache did not load a valid dimension")
    model_embedding_dimension = dimension
    model_assets_complete = True
interpreter = Path(sys.executable).resolve()
print(json.dumps({
    "schema_version": 1,
    "interpreter_path_sha256": digest_bytes(str(interpreter).encode()),
    "interpreter_sha256": digest_file(interpreter),
    "python_version": sys.version,
    "distribution_count": len(distributions),
    "distributions_sha256": digest_bytes(distribution_payload),
    "installed_file_count": len(installed_entries),
    "installed_file_bytes": sum(entry[3] for entry in installed_entries),
    "installed_files_sha256": digest_bytes(installed_payload),
    "editable_source_files": len(editable_entries),
    "editable_source_bytes": sum(entry[3] for entry in editable_entries),
    "editable_sources_sha256": digest_bytes(editable_payload),
    "import_path_entries": len(effective_import_paths),
    "import_path_sha256": digest_bytes(import_path_payload),
    "model_asset_files": len(model_entries),
    "model_asset_bytes": sum(entry[2] for entry in model_entries),
    "model_assets_present": bool(model_entries),
    "model_assets_complete": model_assets_complete,
    "model_assets_sha256": digest_bytes(model_payload),
    "model_embedding_dimension": model_embedding_dimension,
    "model_snapshot_revision_sha256": digest_bytes(model_snapshot_revision.encode()),
}, sort_keys=True, separators=(",", ":")))
"""


class PilotCheckpointError(RuntimeError):
    """A pilot shard or its fingerprint is missing, corrupt, or incompatible."""


def _runtime_environment() -> dict[str, str]:
    """Return the inherited environment without mutable Python import overrides."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def _assert_clean_import_environment() -> None:
    overrides = [key for key in ("PYTHONPATH", "PYTHONHOME") if os.environ.get(key)]
    if overrides:
        raise PilotCheckpointError(
            "comparison pilot refuses mutable Python import overrides: "
            + ", ".join(overrides)
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item_key(item_id: str) -> str:
    return hashlib.sha256(item_id.encode()).hexdigest()


def _consumer_environment_fingerprint(
    venv_python: Path, *, require_model_assets: bool = False
) -> dict[str, object]:
    """Hash the actual consumer interpreter, distributions, and cached embedding model."""
    environment = _runtime_environment()
    if require_model_assets:
        environment.update(
            {
                "FOLIO_PROBE_REQUIRE_MODEL_ASSETS": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
    try:
        completed = subprocess.run(
            [str(venv_python), "-P", "-c", _CONSUMER_ENVIRONMENT_PROBE],
            capture_output=True,
            env=environment,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PilotCheckpointError("consumer environment probe could not run") from exc
    if completed.returncode:
        raise PilotCheckpointError(
            f"consumer environment probe failed (rc={completed.returncode})"
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("consumer environment probe was malformed") from exc
    required_digests = {
        "interpreter_path_sha256",
        "interpreter_sha256",
        "distributions_sha256",
        "installed_files_sha256",
        "editable_sources_sha256",
        "import_path_sha256",
        "model_assets_sha256",
        "model_snapshot_revision_sha256",
    }
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or any(
            not isinstance(payload.get(key), str)
            or len(payload[key]) != 64
            or any(char not in "0123456789abcdef" for char in payload[key])
            for key in required_digests
        )
    ):
        raise PilotCheckpointError("consumer environment probe was malformed")
    for key in (
        "distribution_count",
        "installed_file_count",
        "installed_file_bytes",
        "editable_source_files",
        "editable_source_bytes",
        "import_path_entries",
        "model_asset_files",
        "model_asset_bytes",
        "model_embedding_dimension",
    ):
        if not isinstance(payload.get(key), int) or payload[key] < 0:
            raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("python_version"), str) or not payload["python_version"]:
        raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("model_assets_present"), bool):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("model_assets_complete"), bool):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if require_model_assets and (
        payload.get("model_assets_present") is not True
        or payload.get("model_assets_complete") is not True
    ):
        raise PilotCheckpointError(
            "consumer embedding model cache must load completely offline before pilot initialization"
        )
    payload["venv_path_sha256"] = sha256_bytes(
        str(venv_python.absolute()).encode()
    )
    return payload


def _durably_sync_file(path: Path) -> None:
    """Make an already atomically replaced checkpoint file survive sudden power loss."""
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except OSError as exc:
        raise PilotCheckpointError(f"could not durably publish pilot shard: {path}") from exc


def _durably_create_directory(path: Path) -> None:
    """Create a directory chain and persist every new ancestor entry."""
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    if cursor.exists() and not cursor.is_dir():
        raise PilotCheckpointError(f"pilot checkpoint ancestor is not a directory: {cursor}")
    try:
        for directory in reversed(missing):
            directory.mkdir()
            fsync_directory(directory.parent)
    except OSError as exc:
        raise PilotCheckpointError(
            f"could not durably create pilot checkpoint directory: {path}"
        ) from exc


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
    salt_file_path: Path,
    public_metadata_path: Path,
    mapper_root: Path,
    enrich_root: Path,
    limit: int,
) -> dict[str, object]:
    config = load_config(config_path)
    ontology_pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    mapper = mapper_spec(mapper_root)
    enrich = enrich_spec(enrich_root)
    for consumer in (mapper, enrich):
        prepare_incumbent(consumer, INCUMBENT_VERSION)
    candidate_environment = _consumer_environment_fingerprint(Path(sys.executable))
    mapper_environment = _consumer_environment_fingerprint(
        mapper.venv_python, require_model_assets=True
    )
    enrich_environment = _consumer_environment_fingerprint(enrich.venv_python)
    return {
        "answer_rule_config_sha256": config.content_sha256(),
        "candidate_environment": candidate_environment,
        "candidate_repository": _git_repository_state(FOLIO_RESOLVE_ROOT),
        "corpus_content_sha256": corpus.manifest.content_sha256,
        "enrich_environment": enrich_environment,
        "enrich_lock_sha256": _sha256_file(enrich.repo_root / "backend" / "uv.lock"),
        "enrich_repository": _git_repository_state(enrich_root),
        "folio_python_lock_sha256": _sha256_file(FOLIO_RESOLVE_ROOT / "uv.lock"),
        "folio_python_version": importlib.metadata.version("folio-python"),
        "folio_resolve_version": importlib.metadata.version("folio-resolve"),
        "incumbent_version": INCUMBENT_VERSION,
        "leak_manifest_sha256": _sha256_file(leak_manifest_path),
        "mapper_environment": mapper_environment,
        "mapper_lock_sha256": _sha256_file(mapper.repo_root / "backend" / "uv.lock"),
        "mapper_repository": _git_repository_state(mapper_root),
        "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        "ontology_cache_sha256": ontology_pin.sha256,
        "public_metadata_sha256": _sha256_file(public_metadata_path),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED", ""),
        "python_version": platform.python_version(),
        "salt_file_sha256": _sha256_file(salt_file_path),
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
    _durably_create_directory(stages)
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
        "--incumbent-version",
        INCUMBENT_VERSION,
        "--item-id",
        item_id,
    ]
    completed = subprocess.run(
        command,
        cwd=FOLIO_RESOLVE_ROOT,
        check=False,
        env=_runtime_environment(),
        stdout=subprocess.DEVNULL,
    )
    if completed.returncode:
        raise PilotCheckpointError(
            f"comparison shard failed for {item_id!r} (rc={completed.returncode})"
        )
    _durably_sync_file(report)
    _load_shard(report, item_id, fingerprint)


def _merge_stack_runs(shards: Sequence[Mapping[str, Any]]) -> list[StackRun]:
    runs: list[StackRun] = []
    for key in sorted(shards[0]["stacks"]):
        first = shards[0]["stacks"][key]
        rows: dict[str, frozenset[str]] = {}
        stages: dict[str, Mapping[str, object]] = {}
        invocation_receipts: list[dict[str, object]] = []
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
            invocation = stack.get("invocation")
            if (
                not isinstance(invocation, dict)
                or not isinstance(invocation.get("kind"), str)
                or not isinstance(invocation.get("working_directory"), str)
                or not isinstance(invocation.get("argv"), list)
                or not all(isinstance(value, str) for value in invocation["argv"])
            ):
                raise PilotCheckpointError(f"pilot shard {key} invocation is malformed")
            invocation_receipts.append(
                {
                    "argv": invocation["argv"],
                    "kind": invocation["kind"],
                    "working_directory": invocation["working_directory"],
                }
            )
        invocation_sha256 = sha256_bytes(
            json.dumps(
                invocation_receipts,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        first_invocation = invocation_receipts[0]
        first_argv = first_invocation["argv"]
        if not isinstance(first_argv, list) or not first_argv:
            raise PilotCheckpointError(f"pilot shard {key} invocation argv is empty")
        aggregate_invocation: tuple[str, ...]
        if first["lane"] == "incumbent":
            if len(first_argv) < 2 or not isinstance(first_argv[1], str):
                raise PilotCheckpointError(f"pilot shard {key} runner path is missing")
            aggregate_invocation = (
                "folio_eval.comparison_pilot.aggregate_consumer_stack",
                first_argv[1],
                "--source-shard-count",
                str(len(shards)),
                "--source-invocations-sha256",
                invocation_sha256,
            )
        else:
            aggregate_invocation = (
                "folio_eval.comparison_pilot.aggregate_local_stack",
                "--source-shard-count",
                str(len(shards)),
                "--source-invocations-sha256",
                invocation_sha256,
            )
        runs.append(
            StackRun(
                stack=first["stack"],
                lane=first["lane"],
                folio_resolve_version=first["versions"]["folio-resolve"],
                folio_python_version=first["versions"]["folio-python"],
                config=first["config"],
                rows=rows,
                stages=stages,
                invocation=aggregate_invocation,
                invocation_working_directory=str(first_invocation["working_directory"]),
                invocation_kind="equivalent_checkpoint_aggregate",
                repository=first["repository"],
            )
        )
    return runs


def _finalization_invocation(
    args: argparse.Namespace, combined_items: Path
) -> dict[str, object]:
    """Record the exact supplied inputs used by equivalent checkpoint finalization."""
    return {
        "kind": "equivalent_checkpoint_finalization",
        "argv": [
            sys.executable,
            "eval/run_downstream.py",
            "run_synthetic_comparison",
            "--corpus-manifest",
            str(args.corpus_manifest),
            "--config",
            str(args.config),
            "--out",
            str(args.out),
            "--items",
            str(combined_items),
            "--row-snapshot-dir",
            str(args.checkpoint_dir / "final-stages"),
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
            "--incumbent-version",
            INCUMBENT_VERSION,
            "--limit",
            str(args.limit),
        ],
        "working_directory": str(FOLIO_RESOLVE_ROOT),
        "environment": {"PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "")},
    }


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
    comparison_invocation = _finalization_invocation(args, combined_items)
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
    _assert_clean_import_environment()
    corpus = load_corpus(args.corpus_manifest)
    item_ids = _pilot_ids(corpus, args.limit)
    fingerprint = _fingerprint(
        corpus=corpus,
        config_path=args.config,
        leak_manifest_path=args.leak_manifest,
        salt_file_path=args.salt_file,
        public_metadata_path=args.public_metadata,
        mapper_root=args.mapper_root,
        enrich_root=args.enrich_root,
        limit=args.limit,
    )
    manifest = _checkpoint_manifest(fingerprint=fingerprint, item_ids=item_ids)
    _durably_create_directory(args.checkpoint_dir)
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
