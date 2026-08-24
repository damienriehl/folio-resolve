"""Durable, privacy-minimal checkpoints for long synthetic scoring runs."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import platform
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from folio_resolve import __version__ as folio_resolve_version
from folio_resolve.pipeline import MatchCandidate

from .answer_rule import AnswerRuleConfig, CandidateLike
from .leakcheck import canonical_json
from .resolve_labels import folio_python_version
from .synthesize import LoadedCorpus
from .synthetic_contract import SUPPRESSION_CATEGORIES, SyntheticItemKind

CHECKPOINT_KIND = "synthetic-scoring-checkpoint"
CHECKPOINT_SCHEMA_VERSION = 2
SCORING_SEMANTICS_VERSION = 1
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class CheckpointError(RuntimeError):
    """Raised when a checkpoint is incomplete, corrupt, or from a different run."""


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return canonical_json(payload).encode("utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        raise CheckpointError(f"checkpoint fingerprint input is missing: {path}")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fsync_directory(path: Path) -> None:
    """Durably persist directory-entry changes after atomic publication."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_create(path: Path, payload: Mapping[str, object]) -> bool:
    """Publish a fully durable payload only when ``path`` does not already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # The winner's link may be visible before its own directory fsync completes.
            fsync_directory(path.parent)
            return False
        fsync_directory(path.parent)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _payload_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def checkpoint_item_key(kind: SyntheticItemKind, item_id: str) -> str:
    """Return a stable opaque key without persisting the source item id."""
    if kind not in {"scoreable", "nomatch"}:
        raise ValueError(f"unsupported checkpoint item kind: {kind!r}")
    return hashlib.sha256(f"{kind}\0{item_id}".encode()).hexdigest()


def shard_for_item(item_key: str, shard_count: int) -> int:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    return int(item_key, 16) % shard_count


@dataclass(frozen=True, slots=True)
class CheckpointFingerprint:
    """Every input capable of changing adapter or scorer output."""

    corpus_content_sha256: str
    nomatch_content_sha256: str
    answer_rule_config_sha256: str
    ontology_cache_sha256: str
    git_head: str
    python_hash_seed: str
    python_version: str
    folio_python_version: str
    folio_resolve_version: str
    lockfile_sha256: str
    scoring_semantics_version: int = SCORING_SEMANTICS_VERSION

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    def content_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_json())).hexdigest()


def build_checkpoint_fingerprint(
    corpus: LoadedCorpus,
    config: AnswerRuleConfig,
    *,
    repo_root: Path,
) -> CheckpointFingerprint:
    """Build a run fingerprint and reject source edits before an expensive run."""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise CheckpointError(
            "working tree is dirty; commit or remove scorer inputs before running"
        )
    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return CheckpointFingerprint(
        corpus_content_sha256=corpus.manifest.content_sha256,
        nomatch_content_sha256=corpus.manifest.nomatch_content_sha256,
        answer_rule_config_sha256=config.content_sha256(),
        ontology_cache_sha256=corpus.manifest.ontology_cache_sha256,
        git_head=git_head,
        python_hash_seed=os.environ.get("PYTHONHASHSEED", ""),
        python_version=platform.python_version(),
        folio_python_version=folio_python_version(),
        folio_resolve_version=folio_resolve_version,
        lockfile_sha256=_sha256_file(repo_root / "uv.lock"),
    )


@dataclass(frozen=True, slots=True)
class CheckpointAdapterResult:
    candidates: tuple[MatchCandidate, ...]
    raw_candidate_count: int
    survivor_count: int
    suppression_counters: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SyntheticCheckpointStore:
    """One fingerprint-bound directory of atomically published item results."""

    root: Path
    fingerprint: CheckpointFingerprint
    shard_count: int
    expected_item_count: int
    retained_limit: int

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def items_dir(self) -> Path:
        return self.root / "items"

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        fingerprint: CheckpointFingerprint,
        shard_count: int,
        expected_item_count: int,
        retained_limit: int,
    ) -> SyntheticCheckpointStore:
        if shard_count < 1:
            raise ValueError("shard_count must be positive")
        if expected_item_count < 0:
            raise ValueError("expected_item_count must be nonnegative")
        if retained_limit < 1:
            raise ValueError("retained_limit must be positive")
        store = cls(root, fingerprint, shard_count, expected_item_count, retained_limit)
        expected = store._manifest_payload()
        _atomic_create(store.manifest_path, expected)
        try:
            observed = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError("checkpoint manifest is corrupt") from exc
        if observed != expected:
            raise CheckpointError("checkpoint manifest or fingerprint does not match this run")
        store.items_dir.mkdir(parents=True, exist_ok=True)
        return store

    def _manifest_payload(self) -> dict[str, object]:
        return {
            "expected_item_count": self.expected_item_count,
            "fingerprint": self.fingerprint.to_json(),
            "fingerprint_sha256": self.fingerprint.content_sha256(),
            "kind": CHECKPOINT_KIND,
            "retained_limit": self.retained_limit,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "shard_count": self.shard_count,
        }

    def item_path(self, kind: SyntheticItemKind, item_id: str) -> Path:
        return self.items_dir / f"{checkpoint_item_key(kind, item_id)}.json"

    def item_paths(self) -> tuple[Path, ...]:
        return tuple(sorted(self.items_dir.glob("*.json")))

    def completed_count(self) -> int:
        return sum(1 for _path in self.items_dir.glob("*.json"))

    def write_item(
        self,
        kind: SyntheticItemKind,
        item_id: str,
        *,
        candidates: Sequence[CandidateLike],
        raw_candidate_count: int,
        suppression_counters: Mapping[str, int],
    ) -> CheckpointAdapterResult:
        key = checkpoint_item_key(kind, item_id)
        path = self.items_dir / f"{key}.json"
        existing = self.maybe_load_item(kind, item_id)
        if existing is not None:
            return existing
        canonical = sorted(candidates, key=lambda candidate: (-candidate.score, candidate.iri))
        payload_without_digest: dict[str, object] = {
            "candidates": [
                {"iri": candidate.iri, "score": candidate.score}
                for candidate in canonical[: self.retained_limit]
            ],
            "fingerprint_sha256": self.fingerprint.content_sha256(),
            "item_key": key,
            "kind": kind,
            "raw_candidate_count": raw_candidate_count,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "suppression_counters": dict(sorted(suppression_counters.items())),
            "survivor_count": len(canonical),
        }
        payload = {
            **payload_without_digest,
            "payload_sha256": _payload_sha256(payload_without_digest),
        }
        validated = self._validate_item(payload, expected_key=key, expected_kind=kind)
        _atomic_write(path, payload)
        return validated

    def maybe_load_item(
        self, kind: SyntheticItemKind, item_id: str
    ) -> CheckpointAdapterResult | None:
        key = checkpoint_item_key(kind, item_id)
        path = self.items_dir / f"{key}.json"
        try:
            serialized = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CheckpointError(f"checkpoint item cannot be read: {key}") from exc
        try:
            payload = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise CheckpointError(f"checkpoint item is corrupt: {key}") from exc
        return self._validate_item(payload, expected_key=key, expected_kind=kind)

    def load_item(self, kind: SyntheticItemKind, item_id: str) -> CheckpointAdapterResult:
        result = self.maybe_load_item(kind, item_id)
        if result is None:
            raise CheckpointError(
                f"checkpoint incomplete: missing item {checkpoint_item_key(kind, item_id)}"
            )
        return result

    def _validate_item(
        self, payload: object, *, expected_key: str, expected_kind: SyntheticItemKind
    ) -> CheckpointAdapterResult:
        if not isinstance(payload, dict):
            raise CheckpointError("checkpoint item must be an object")
        expected_fields = {
            "candidates",
            "fingerprint_sha256",
            "item_key",
            "kind",
            "payload_sha256",
            "raw_candidate_count",
            "schema_version",
            "suppression_counters",
            "survivor_count",
        }
        if set(payload) != expected_fields:
            raise CheckpointError("checkpoint item fields do not match the schema")
        claimed_digest = payload["payload_sha256"]
        if not isinstance(claimed_digest, str) or SHA256_RE.fullmatch(claimed_digest) is None:
            raise CheckpointError("checkpoint item payload digest is invalid")
        digest_payload = dict(payload)
        del digest_payload["payload_sha256"]
        if not hmac.compare_digest(claimed_digest, _payload_sha256(digest_payload)):
            raise CheckpointError("checkpoint item payload digest mismatch")
        if payload["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointError("checkpoint item schema version mismatch")
        if payload["fingerprint_sha256"] != self.fingerprint.content_sha256():
            raise CheckpointError("checkpoint item fingerprint mismatch")
        if payload["item_key"] != expected_key or payload["kind"] != expected_kind:
            raise CheckpointError("checkpoint item key or kind mismatch")
        raw_count = _nonnegative_int(payload["raw_candidate_count"], "raw_candidate_count")
        survivor_count = _nonnegative_int(payload["survivor_count"], "survivor_count")
        raw_counters = payload["suppression_counters"]
        if not isinstance(raw_counters, dict) or set(raw_counters) != set(SUPPRESSION_CATEGORIES):
            raise CheckpointError("checkpoint suppression categories do not match")
        counters = {
            category: _nonnegative_int(raw_counters[category], category)
            for category in SUPPRESSION_CATEGORIES
        }
        if raw_count != survivor_count + sum(counters.values()):
            raise CheckpointError("checkpoint candidate count invariant failed")
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, list):
            raise CheckpointError("checkpoint candidates must be a list")
        expected_retained = min(survivor_count, self.retained_limit)
        if len(raw_candidates) != expected_retained:
            raise CheckpointError("checkpoint retained candidate count is invalid")
        candidates: list[MatchCandidate] = []
        seen: set[str] = set()
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict) or set(raw_candidate) != {"iri", "score"}:
                raise CheckpointError("checkpoint candidate fields are invalid")
            iri = raw_candidate["iri"]
            score = raw_candidate["score"]
            if not isinstance(iri, str) or not iri:
                raise CheckpointError("checkpoint candidate IRI is invalid")
            if iri in seen:
                raise CheckpointError("checkpoint contains a duplicate candidate IRI")
            seen.add(iri)
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise CheckpointError("checkpoint candidate score must be finite")
            numeric_score = float(score)
            if not math.isfinite(numeric_score):
                raise CheckpointError("checkpoint candidate score must be finite")
            candidates.append(MatchCandidate(iri=iri, label="", score=numeric_score))
        if [(candidate.score, candidate.iri) for candidate in candidates] != [
            (candidate.score, candidate.iri)
            for candidate in sorted(
                candidates, key=lambda candidate: (-candidate.score, candidate.iri)
            )
        ]:
            raise CheckpointError("checkpoint candidates are not in canonical order")
        return CheckpointAdapterResult(
            candidates=tuple(candidates),
            raw_candidate_count=raw_count,
            survivor_count=survivor_count,
            suppression_counters=counters,
        )


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CheckpointError(f"checkpoint {field} must be a nonnegative integer")
    return value
