"""Cross-stack orchestration for the frozen synthetic corpus (U10; R15--R17).

The module is separate from :mod:`folio_eval.downstream`: U9's snapshot/diff harness remains
small, while this module owns the shared-items contract, pinned incumbent installation, exact
IRI scoring, paired verdicts, and leak-gated comparison artifact.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from string import Formatter
from types import MappingProxyType

from .answer_rule import AnswerRuleConfig, commit_from_ranked, rank_candidates
from .downstream import (
    FOLIO_RESOLVE_ROOT,
    ConsumerRunError,
    ConsumerSpec,
    clean_tree_guard,
    git_status_porcelain,
)
from .intake import sha256_bytes, sha256_text
from .leakcheck import Manifest, scan_text
from .report import DEFAULT_BOOTSTRAP_RESAMPLES, DEFAULT_BOOTSTRAP_SEED, bootstrap_ci
from .score import MicroCounts
from .synthesize import LoadedCorpus, SyntheticItem
from .synthetic_score import (
    CandidateTrace,
    DocumentAdapter,
    PhraseExtractor,
    _assert_config,
    nounish_ngrams,
)


class ComparisonError(RuntimeError):
    """Base class for a comparison contract violation."""


class StackContractError(ComparisonError):
    """A stack runner emitted malformed, duplicate, or incomplete JSONL."""


class IncumbentInstallMismatch(ComparisonError):
    """The pinned wheel did not resolve to the requested version in site-packages."""


class VersionSkewError(ComparisonError):
    """Stacks resolved different folio-python versions, invalidating comparison."""


DEFAULT_COMPARISON_TIMEOUT_S = 2 * 60 * 60
FOLIO_IRI_ROOT = "https://folio.openlegalstandard.org/"

MAPPER_DETERMINISTIC_CONFIG: Mapping[str, object] = {
    "threshold": 0.3,
    "max_per_branch": 10,
    "rerank_top_k": 20,
    "commit_top_n": 10,
    "keyword_weight": 0.6,
    "embedding_weight": 0.4,
    "embedding_rerank": "available",
    "llm_on": False,
}

ENRICH_DETERMINISTIC_CONFIG: Mapping[str, object] = {
    "embedding_disabled": True,
    "contextual_rerank_enabled": False,
    "individual_extraction_enabled": True,
    "individual_regex_only": True,
    "property_extraction_enabled": True,
    "property_regex_only": True,
    "triple_extraction_enabled": True,
    "pos_tagging_enabled": True,
    "pos_confidence_enabled": True,
    "ner_cross_validation_enabled": False,
    "translation_matching_enabled": False,
    "folio_auto_update": False,
    "backup_semantic_filter_enabled": False,
    "proposition_extraction_enabled": False,
    "max_candidates": 5,
    "skip_backups_for_exact_matches": True,
    "semantic_similarity_threshold": 0.8,
    "pos_concept_mismatch_penalty": 0.15,
    "pos_property_mismatch_penalty": 0.12,
    "llm_provider": None,
    "registry_embeddings": False,
}

CONSUMER_DETERMINISTIC_CONFIGS: Mapping[str, Mapping[str, object]] = {
    "folio-mapper": MAPPER_DETERMINISTIC_CONFIG,
    "folio-enrich": ENRICH_DETERMINISTIC_CONFIG,
}

COMPARISON_PUBLIC_METADATA_KIND = "synthetic-comparison-public-metadata"
COMPARISON_PUBLIC_METADATA_VERSION = 1
DEFAULT_COMPARISON_PUBLIC_METADATA_PATH = (
    Path(__file__).resolve().parents[1] / "synthetic" / "public_comparison_metadata_v1.json"
)
COMPARISON_PUBLIC_METADATA_PATHS = frozenset(
    {
        ("kind",),
        ("provenance", "comparison_invocation", "argv", "2"),
        ("provenance", "comparison_invocation", "argv", "--corpus-manifest"),
        ("provenance", "comparison_invocation", "argv", "--config"),
        ("provenance", "comparison_invocation", "argv", "--leak-manifest"),
        ("provenance", "config_selection", "answer_rule_config", "rationale"),
        ("stacks", "folio-enrich:incumbent", "invocation", "argv", "1"),
        ("stacks", "folio-mapper:incumbent", "invocation", "argv", "1"),
        ("stacks", "folio-resolve:candidate", "config", "rationale"),
    }
)


@dataclass(frozen=True, slots=True)
class PublicComparisonMetadata:
    """Versioned, path-bound public strings that the comparison gate may exempt."""

    source_path: Path
    version: int
    answer_rule_config_sha256: str
    fields: Mapping[tuple[str, ...], tuple[str, str]]


def load_public_comparison_metadata(path: Path) -> PublicComparisonMetadata:
    """Load the independently reviewed comparison public-string contract."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("kind") != COMPARISON_PUBLIC_METADATA_KIND:
        raise ComparisonError(f"invalid comparison public metadata contract: {path}")
    if payload.get("version") != COMPARISON_PUBLIC_METADATA_VERSION:
        raise ComparisonError(
            f"unsupported comparison public metadata version: {payload.get('version')!r}"
        )
    config_sha = payload.get("answer_rule_config_sha256")
    if not isinstance(config_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", config_sha):
        raise ComparisonError("comparison public metadata config hash must be lowercase SHA-256")
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise ComparisonError("comparison public metadata fields must be a list")
    fields: dict[tuple[str, ...], tuple[str, str]] = {}
    for entry in raw_fields:
        if not isinstance(entry, dict):
            raise ComparisonError("comparison public metadata field must be an object")
        raw_path = entry.get("path")
        if (
            not isinstance(raw_path, list)
            or not raw_path
            or not all(isinstance(part, str) and part for part in raw_path)
        ):
            raise ComparisonError("malformed comparison public metadata path")
        field_path = tuple(raw_path)
        value = entry.get("value")
        template = entry.get("value_template")
        if (isinstance(value, str)) == (isinstance(template, str)):
            raise ComparisonError(
                "comparison public metadata field requires exactly one value or value_template"
            )
        if template is not None:
            try:
                replacement_fields = [
                    (field_name, format_spec, conversion)
                    for _, field_name, format_spec, conversion in Formatter().parse(str(template))
                    if field_name is not None
                ]
            except ValueError as exc:
                raise ComparisonError(
                    "unsupported comparison public metadata template"
                ) from exc
            if replacement_fields != [("working_directory", "", None)]:
                raise ComparisonError("unsupported comparison public metadata template")
        if field_path in fields:
            raise ComparisonError(f"duplicate comparison public metadata path: {field_path!r}")
        fields[field_path] = (
            ("value", value) if isinstance(value, str) else ("template", str(template))
        )
    if frozenset(fields) != COMPARISON_PUBLIC_METADATA_PATHS:
        raise ComparisonError("comparison public metadata paths do not match the v1 contract")
    return PublicComparisonMetadata(
        source_path=path,
        version=COMPARISON_PUBLIC_METADATA_VERSION,
        answer_rule_config_sha256=config_sha,
        fields=MappingProxyType(fields),
    )


@dataclass(frozen=True, slots=True)
class StackRun:
    """Parsed shared stack-run contract."""

    stack: str
    lane: str
    folio_resolve_version: str
    folio_python_version: str
    config: Mapping[str, object]
    rows: Mapping[str, frozenset[str]]
    stages: Mapping[str, Mapping[str, object]]
    invocation: tuple[str, ...] = ()
    invocation_working_directory: str = ""
    invocation_kind: str = ""
    repository: Mapping[str, object] = dataclass_field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.stack}:{self.lane}"


def _atomic_write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def _selected_cohort(
    corpus: LoadedCorpus,
    limit: int | None,
    *,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
) -> tuple[tuple[SyntheticItem, ...], tuple[SyntheticItem, ...]]:
    if item_ids is not None and limit is not None:
        raise ValueError("item_ids and limit are mutually exclusive")
    if item_ids is not None:
        requested = tuple(item_ids)
        if not requested or len(requested) != len(set(requested)):
            raise ValueError("item_ids must be nonempty and unique")
        requested_set = set(requested)
        scoreable = tuple(
            item for item in corpus.scoreable_items if item.item_id in requested_set
        )
        nomatch = tuple(
            item for item in corpus.nomatch_items if item.item_id in requested_set
        )
        if not include_nomatch and nomatch:
            raise ValueError("item_ids includes no-match rows while include_nomatch is false")
        observed = {item.item_id for item in (*scoreable, *nomatch)}
        unknown = sorted(requested_set - observed)
        if unknown:
            raise ValueError(f"unknown comparison item_ids: {', '.join(unknown)}")
        return scoreable, nomatch
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if not include_nomatch and limit != 1:
        raise ValueError("scoreable-only comparison is reserved for the one-item live gate")
    scoreable = tuple(
        corpus.scoreable_items[:limit] if limit is not None else corpus.scoreable_items
    )
    nomatch = tuple(corpus.nomatch_items if include_nomatch else ())
    return scoreable, nomatch


def _comparison_items(
    corpus: LoadedCorpus,
    limit: int | None,
    *,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
) -> tuple[SyntheticItem, ...]:
    scoreable, nomatch = _selected_cohort(
        corpus, limit, include_nomatch=include_nomatch, item_ids=item_ids
    )
    return scoreable + nomatch


def emit_items_file(
    corpus: LoadedCorpus,
    out_path: Path,
    *,
    limit: int | None = None,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
    extractor: PhraseExtractor = nounish_ngrams,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
) -> Path:
    """Emit the common JSONL once, including the U8 extraction seam's segments.

    Every runner receives these materialized segments and must not independently segment text.
    No-match rows are retained by default so pilot/final false-positive rates remain meaningful.
    A live gate may explicitly omit them to exercise exactly one scoreable item end-to-end.
    """
    lines: list[str] = []
    for item in _comparison_items(
        corpus, limit, include_nomatch=include_nomatch, item_ids=item_ids
    ):
        payload = {
            "item_id": item.item_id,
            "text": item.text,
            "segments": list(extractor(item.text)),
        }
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    text = "\n".join(lines) + ("\n" if lines else "")
    if leak_manifest is not None:
        if salt is None:
            raise ComparisonError("salt is required with a leak manifest")
        collisions = scan_text(text, leak_manifest, salt)
        if collisions:
            raise ComparisonError(f"items leak check failed: collisions={collisions}")
    return _atomic_write_text(out_path, text)


def _git_repository_state(repo_root: Path) -> dict[str, object]:
    """Capture a reproducible Git identity, failing closed on uncommitted source state."""
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ComparisonError(
            f"could not resolve Git SHA for {repo_root}: {completed.stderr.strip()[-2000:]}"
        )
    status = git_status_porcelain(repo_root)
    if status:
        raise ComparisonError(
            f"comparison repository must be clean before execution: {repo_root} "
            f"({len(status.splitlines())} status entries)"
        )
    return {
        "git_sha": completed.stdout.strip(),
        "initial_status_clean": not status,
        "initial_status_entries": len(status.splitlines()),
        "initial_status_sha256": sha256_text(status),
        "initial_status_format": "git status --porcelain",
    }


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _file_fingerprint(path: Path, *, root: Path) -> dict[str, object]:
    """Return the portable path, exact byte hash, and size of an execution input."""
    content = path.read_bytes()
    return {
        "path": _relative_or_absolute(path, root),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def _probe_environment(spec: ConsumerSpec) -> dict[str, str]:
    code = """
import importlib.metadata, json, os, folio_resolve
print(json.dumps({
    "folio_resolve_version": folio_resolve.__version__,
    "folio_resolve_file": os.path.realpath(folio_resolve.__file__),
    "folio_python_version": importlib.metadata.version("folio-python"),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(spec.venv_python), "-c", code], capture_output=True, text=True, timeout=30
    )
    if completed.returncode != 0:
        raise IncumbentInstallMismatch(
            f"could not probe pinned incumbent in {spec.name}: {completed.stderr.strip()[-2000:]}"
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise IncumbentInstallMismatch(f"malformed incumbent probe for {spec.name}") from error
    if not isinstance(payload, dict):
        raise IncumbentInstallMismatch(f"malformed incumbent probe for {spec.name}")
    return {str(key): str(value) for key, value in payload.items()}


def assert_incumbent_probe(probe: Mapping[str, str], version: str) -> dict[str, str]:
    """Assert the import is the exact pin and lives in an installed-package directory."""
    actual = probe.get("folio_resolve_version", "")
    resolved = Path(probe.get("folio_resolve_file", "")).resolve()
    if actual != version:
        raise IncumbentInstallMismatch(
            f"incumbent folio-resolve version mismatch: expected={version} actual={actual}"
        )
    if not ({"site-packages", "dist-packages"} & set(resolved.parts)):
        raise IncumbentInstallMismatch(
            f"incumbent folio_resolve.__file__ is not inside site-packages: {resolved}"
        )
    if not probe.get("folio_python_version"):
        raise IncumbentInstallMismatch("incumbent probe did not resolve folio-python version")
    return dict(probe)


def prepare_incumbent(spec: ConsumerSpec, version: str = "0.4.0") -> dict[str, str]:
    """Replace an editable install with the released pinned wheel, then prove its identity."""
    completed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(spec.venv_python),
            f"folio-resolve=={version}",
            "--only-binary",
            ":all:",
            "--no-deps",
            "--reinstall",
        ],
        cwd=str(spec.repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise ConsumerRunError(
            f"pinned incumbent install into {spec.name} failed (rc={completed.returncode}):\n"
            f"{completed.stderr.strip()[-4000:]}"
        )
    return assert_incumbent_probe(_probe_environment(spec), version)


def parse_stack_output(path: Path) -> StackRun:
    """Parse and validate header + per-item rows from a runner's deterministic JSONL."""
    try:
        payloads = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise StackContractError(f"could not parse stack output {path}: {error}") from error
    if not payloads or not isinstance(payloads[0], dict):
        raise StackContractError(f"stack output {path} has no header")
    header = payloads[0]
    required = ("stack", "lane", "folio_resolve_version", "folio_python_version", "config")
    if header.get("kind") != "synthetic-stack-run" or any(key not in header for key in required):
        raise StackContractError(f"stack output {path} has an invalid header")
    if not isinstance(header["config"], dict):
        raise StackContractError(f"stack output {path} config is not an object")
    for field in ("stack", "lane", "folio_resolve_version", "folio_python_version"):
        if not isinstance(header[field], str) or not header[field].strip():
            raise StackContractError(f"stack output {path} has an empty {field}")
    for field in ("folio_resolve_version", "folio_python_version"):
        if header[field].strip().casefold() == "unknown":
            raise StackContractError(f"stack output {path} has unknown {field}")
    rows: dict[str, frozenset[str]] = {}
    stages: dict[str, Mapping[str, object]] = {}
    for payload in payloads[1:]:
        if not isinstance(payload, dict) or not isinstance(payload.get("item_id"), str):
            raise StackContractError(f"stack output {path} has a malformed item row")
        item_id = payload["item_id"]
        iris = payload.get("iris")
        raw_stages = payload.get("stages")
        if (
            item_id in rows
            or not isinstance(iris, list)
            or not all(isinstance(v, str) for v in iris)
        ):
            raise StackContractError(f"stack output {path} has invalid/duplicate item {item_id!r}")
        if iris != sorted(set(iris)):
            raise StackContractError(
                f"stack output {path} has duplicate or non-canonical IRIs for {item_id!r}"
            )
        if not isinstance(raw_stages, dict):
            raise StackContractError(f"stack output {path} stages are malformed for {item_id!r}")
        rows[item_id] = frozenset(iris)
        stages[item_id] = raw_stages
    return StackRun(
        stack=str(header["stack"]),
        lane=str(header["lane"]),
        folio_resolve_version=str(header["folio_resolve_version"]),
        folio_python_version=str(header["folio_python_version"]),
        config=header["config"],
        rows=rows,
        stages=stages,
    )


def _assert_consumer_config(run: StackRun) -> None:
    """Require the exact, typed deterministic behavioral config for a consumer runner."""
    expected = CONSUMER_DETERMINISTIC_CONFIGS.get(run.stack)
    if expected is None:
        return
    actual = dict(run.config)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise StackContractError(
            f"{run.stack} deterministic config keys differ: missing={missing} extra={extra}"
        )
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            raise StackContractError(
                f"{run.stack} deterministic config {key} mismatch: "
                f"expected={expected_value!r} actual={actual_value!r}"
            )


def _assert_string_list(value: object, *, context: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise StackContractError(f"{context} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise StackContractError(f"{context} contains duplicates")
    return value


def _assert_consumer_rows(run: StackRun, expected_ids: Sequence[str]) -> None:
    """Validate exact shared-item coverage and deterministic per-stage invariants."""
    if set(run.rows) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(run.rows))
        extra = sorted(set(run.rows) - set(expected_ids))
        raise StackContractError(
            f"{run.stack} item IDs differ from shared input: missing={missing} extra={extra}"
        )
    expected_stage_keys = {
        "folio-mapper": {"stage1_filter", "embedding_rerank", "committed"},
        "folio-enrich": {"EntityRuler", "Reconciliation", "Resolution", "StringMatch"},
    }.get(run.stack)
    if expected_stage_keys is None:
        return
    for item_id in expected_ids:
        stage = run.stages[item_id]
        if set(stage) != expected_stage_keys:
            raise StackContractError(
                f"{run.stack} stage keys differ for {item_id!r}: "
                f"expected={sorted(expected_stage_keys)} actual={sorted(stage)}"
            )
        lists = {
            key: _assert_string_list(value, context=f"{run.stack} {item_id!r} {key}")
            for key, value in stage.items()
        }
        if any(
            not iri.startswith(FOLIO_IRI_ROOT)
            for values in lists.values()
            for iri in values
        ) or any(not iri.startswith(FOLIO_IRI_ROOT) for iri in run.rows[item_id]):
            raise StackContractError(
                f"{run.stack} emitted a non-canonical FOLIO IRI for {item_id!r}"
            )
        if run.stack == "folio-mapper":
            stage1 = lists["stage1_filter"]
            reranked = lists["embedding_rerank"]
            committed = lists["committed"]
            if set(committed) != set(run.rows[item_id]):
                raise StackContractError(
                    f"{run.stack} committed stage differs from emitted IRIs for {item_id!r}"
                )
            if not set(reranked).issubset(stage1):
                raise StackContractError(
                    f"folio-mapper rerank is not a stage1 subset for {item_id!r}"
                )
            if stage1 and not reranked:
                raise StackContractError(
                    f"folio-mapper embedding rerank is empty for nonempty stage1 on {item_id!r}"
                )
            if committed != reranked[:10]:
                raise StackContractError(
                    f"folio-mapper committed stage is not rerank[:10] for {item_id!r}"
                )


def run_consumer_stack(
    spec: ConsumerSpec,
    items_path: Path,
    *,
    version: str = "0.4.0",
    timeout: float = DEFAULT_COMPARISON_TIMEOUT_S,
    prepare: bool = True,
) -> StackRun:
    """Prepare and run one incumbent consumer through its own interpreter."""
    relative_runner = {
        "folio-enrich": Path("backend/eval/synthetic_runner.py"),
        "folio-mapper": Path("backend/scripts/synthetic_runner.py"),
    }.get(spec.name)
    if relative_runner is None:
        raise ComparisonError(f"unknown comparison consumer: {spec.name}")
    repository = _git_repository_state(spec.repo_root)
    expected_ids: list[str] = []
    for line in items_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        item_id = payload.get("item_id") if isinstance(payload, dict) else None
        if not isinstance(item_id, str) or not item_id or item_id in expected_ids:
            raise StackContractError("comparison items contain a malformed/duplicate item_id")
        expected_ids.append(item_id)
    with tempfile.TemporaryDirectory(prefix=f"{spec.name}-comparison-") as temporary:
        out_path = Path(temporary) / "stack.jsonl"
        command = [
            str(spec.venv_python),
            str((spec.repo_root / relative_runner).resolve()),
            "--items",
            str(items_path.resolve()),
            "--out",
            str(out_path.resolve()),
            "--lane",
            "deterministic",
        ]
        with clean_tree_guard(spec.repo_root):
            if prepare:
                prepare_incumbent(spec, version)
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(spec.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConsumerRunError(
                    f"{spec.name} synthetic runner timed out after {timeout:.0f}s"
                ) from exc
            if completed.returncode != 0:
                raise ConsumerRunError(
                    f"{spec.name} synthetic runner failed (rc={completed.returncode}):\n"
                    f"{(completed.stdout + completed.stderr).strip()[-4000:]}"
                )
            run = parse_stack_output(out_path)
    if run.stack != spec.name or run.lane != "deterministic":
        raise StackContractError(
            f"{spec.name} runner identity mismatch: stack={run.stack!r} lane={run.lane!r}"
        )
    if run.folio_resolve_version != version:
        raise IncumbentInstallMismatch(
            f"{spec.name} runner reported folio-resolve {run.folio_resolve_version}, expected {version}"
        )
    _assert_consumer_config(run)
    _assert_consumer_rows(run, expected_ids)
    return replace(
        run,
        lane="incumbent",
        invocation=tuple(command),
        invocation_working_directory=str(spec.repo_root.resolve()),
        repository=repository,
    )


def run_local_stack(
    corpus: LoadedCorpus,
    adapter: DocumentAdapter,
    config: AnswerRuleConfig,
    *,
    limit: int | None = None,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
    extractor: PhraseExtractor = nounish_ngrams,
    items_path: Path | None = None,
) -> StackRun:
    """Run the candidate folio-resolve lane directly through U8's adapter."""
    materialized: dict[str, tuple[str, ...]] = {}
    if items_path is not None:
        for line in items_path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            materialized[str(payload["item_id"])] = tuple(
                str(value) for value in payload["segments"]
            )
    rows: dict[str, frozenset[str]] = {}
    stages: dict[str, Mapping[str, object]] = {}
    for item in _comparison_items(
        corpus, limit, include_nomatch=include_nomatch, item_ids=item_ids
    ):
        segments = (
            materialized[item.item_id] if items_path is not None else tuple(extractor(item.text))
        )
        # Preserve U8's whole-document exact sweep while injecting the materialized
        # extraction result, so the local lane cannot independently segment the text.
        adapted = adapter.adapt(item.text, segments=segments)
        candidates = list(adapted.candidates)
        ranked = rank_candidates(candidates, config)
        committed = commit_from_ranked(ranked, config)
        rows[item.item_id] = frozenset(candidate.iri for candidate in committed)
        ranked_by_iri = {candidate.iri: candidate for candidate in ranked}
        committed_iris = [candidate.iri for candidate in committed]
        traces = adapted.traces or tuple(
            CandidateTrace(
                iri=candidate.iri,
                label=candidate.label,
                branch=getattr(candidate, "branch", ""),
                extraction_path=candidate.extraction_path,
                surface_term=getattr(candidate, "surface_term", ""),
                pre_gate_score=candidate.score,
                post_gate_score=candidate.score,
                gate_disposition="survived",
                gated=candidate.gated,
                gate_reason=getattr(candidate, "gate_reason", ""),
            )
            for candidate in candidates
        )
        suppressed_count = sum(adapted.suppression_counters.values())
        if (
            len(traces) != adapted.raw_candidate_count
            or adapted.raw_candidate_count != len(candidates) + suppressed_count
        ):
            raise StackContractError(
                f"candidate lifecycle accounting failed for {item.item_id!r}"
            )
        candidate_rows: list[dict[str, object]] = []
        for trace in sorted(traces, key=lambda value: value.iri):
            ranked_candidate = ranked_by_iri.get(trace.iri)
            if trace.gate_disposition != "survived":
                commit_disposition = "suppressed"
            elif trace.iri in committed_iris:
                commit_disposition = "committed"
            elif ranked_candidate is not None and ranked_candidate.probability < config.threshold:
                commit_disposition = "below_threshold"
            else:
                commit_disposition = "top_k_cap"
            candidate_rows.append(
                {
                    "iri": trace.iri,
                    "branch": trace.branch,
                    "extraction_path": trace.extraction_path,
                    "pre_gate_score": trace.pre_gate_score,
                    "post_gate_score": trace.post_gate_score,
                    "gate_disposition": trace.gate_disposition,
                    "gated": trace.gated,
                    "gate_reason": trace.gate_reason,
                    "rank": ranked_candidate.rank if ranked_candidate is not None else None,
                    "probability": (
                        ranked_candidate.probability if ranked_candidate is not None else None
                    ),
                    "commit_disposition": commit_disposition,
                }
            )
        stages[item.item_id] = {
            "segments": list(segments),
            "counts": {
                "pre_gate_unique": adapted.raw_candidate_count,
                "survived": len(candidates),
                "suppressed": dict(sorted(adapted.suppression_counters.items())),
                "committed": len(committed),
            },
            "candidates": candidate_rows,
            "ranked_iris": [candidate.iri for candidate in ranked],
            "committed_iris": committed_iris,
        }
    try:
        folio_python_version = importlib.metadata.version("folio-python")
    except importlib.metadata.PackageNotFoundError:
        folio_python_version = "unknown"
    from folio_resolve import __version__ as folio_resolve_version

    return StackRun(
        stack="folio-resolve",
        lane="candidate",
        folio_resolve_version=folio_resolve_version,
        folio_python_version=folio_python_version,
        config=config.to_json(),
        rows=rows,
        stages=stages,
        invocation=(
            "folio_eval.comparison.run_local_stack",
            "--items",
            "$COMPARISON_ITEMS",
            "--answer-rule-config-sha256",
            config.content_sha256(),
        ),
        invocation_working_directory="$FOLIO_RESOLVE_REPOSITORY_ROOT",
    )


def score_stack(
    corpus: LoadedCorpus,
    run: StackRun,
    *,
    limit: int | None = None,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
) -> tuple[dict[str, object], dict[str, float]]:
    """Join predictions to gold by item_id and compute strict set micro metrics."""
    counts = MicroCounts()
    item_f1: dict[str, float] = {}
    selected_scoreable, selected_nomatch = _selected_cohort(
        corpus, limit, include_nomatch=include_nomatch, item_ids=item_ids
    )
    for item in selected_scoreable:
        if item.item_id not in run.rows:
            raise StackContractError(f"{run.key} omitted scoreable item {item.item_id!r}")
        predicted, gold = set(run.rows[item.item_id]), set(item.gold_iris)
        tp, fp, fn = len(predicted & gold), len(predicted - gold), len(gold - predicted)
        counts.items += 1
        counts.gold += len(gold)
        counts.predicted += len(predicted)
        counts.tp += tp
        counts.fp += fp
        counts.fn += fn
        counts.exact_items += predicted == gold
        counts.empty_prediction_items += not predicted
        denominator = 2 * tp + fp + fn
        item_f1[item.item_id] = 2 * tp / denominator if denominator else 0.0
    false_positives = 0
    for item in selected_nomatch:
        if item.item_id not in run.rows:
            raise StackContractError(f"{run.key} omitted no-match item {item.item_id!r}")
        false_positives += bool(run.rows[item.item_id])
    metrics = counts.to_json()
    metrics["nomatch_items"] = len(selected_nomatch)
    metrics["nomatch_false_positives"] = false_positives
    metrics["nomatch_fp_rate"] = round(
        false_positives / len(selected_nomatch) if selected_nomatch else 0.0, 6
    )
    return metrics, item_f1


def classify_verdict(
    candidate_f1: Sequence[float],
    incumbent_f1: Sequence[float],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, object]:
    """Classify a paired item-F1 delta CI: strictly positive/negative, else hold."""
    if len(candidate_f1) != len(incumbent_f1):
        raise ValueError("paired verdict inputs must have equal length")
    deltas = tuple(after - before for after, before in zip(candidate_f1, incumbent_f1, strict=True))
    ci = bootstrap_ci(
        deltas,
        lambda values: sum(values) / len(values) if values else 0.0,
        n_resamples=n_resamples,
        seed=seed,
    )
    verdict = "win" if ci.low > 0 else "loss" if ci.high < 0 else "hold"
    return {
        "verdict": verdict,
        "escalate": verdict == "hold",
        "metric": "paired_item_f1_delta",
        "ci": ci.to_json(),
    }


def _assert_no_version_skew(runs: Sequence[StackRun]) -> str:
    if any(not run.folio_python_version.strip() for run in runs):
        raise VersionSkewError("folio-python version is empty")
    versions = sorted({run.folio_python_version for run in runs})
    if len(versions) != 1:
        detail = ", ".join(f"{run.key}={run.folio_python_version}" for run in runs)
        raise VersionSkewError(f"folio-python version skew: {detail}")
    return versions[0]


def build_comparison(
    corpus: LoadedCorpus,
    runs: Sequence[StackRun],
    config: AnswerRuleConfig,
    *,
    limit: int | None = None,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    comparison_invocation: Mapping[str, object] | Sequence[str] = (),
    stage_snapshot_files: Mapping[str, Mapping[str, object]] | None = None,
    items_file: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the committed-eligible comparison dictionary from parsed stack runs."""
    _assert_config(corpus, config)
    if not runs:
        raise ComparisonError("comparison requires at least one stack run")
    common_folio_python = _assert_no_version_skew(runs)
    if len({run.key for run in runs}) != len(runs):
        raise ComparisonError("duplicate stack/lane run")
    stack_payloads: dict[str, object] = {}
    item_scores: dict[str, dict[str, float]] = {}
    scoreable, nomatch = _selected_cohort(
        corpus, limit, include_nomatch=include_nomatch, item_ids=item_ids
    )
    selected_ids = tuple(item.item_id for item in (*scoreable, *nomatch))
    for run in sorted(runs, key=lambda value: value.key):
        if run.lane == "incumbent" and run.invocation:
            _assert_consumer_config(run)
        metrics, per_item = score_stack(
            corpus,
            run,
            limit=limit,
            include_nomatch=include_nomatch,
            item_ids=item_ids,
        )
        missing_stages = sorted(set(selected_ids) - set(run.stages))
        if missing_stages:
            raise StackContractError(
                f"{run.key} omitted stage snapshots for items: {', '.join(missing_stages)}"
            )
        stage_by_item = {item_id: run.stages[item_id] for item_id in sorted(selected_ids)}
        stage_fields: dict[str, int] = {}
        for stage in stage_by_item.values():
            for field in stage:
                stage_fields[field] = stage_fields.get(field, 0) + 1
        snapshot_file = dict((stage_snapshot_files or {}).get(run.key, {}))
        stack_payloads[run.key] = {
            "stack": run.stack,
            "lane": run.lane,
            "versions": {
                "folio-resolve": run.folio_resolve_version,
                "folio-python": run.folio_python_version,
            },
            "config": dict(run.config),
            "invocation": {
                "kind": run.invocation_kind
                or ("executed_process" if run.lane == "incumbent" else "in_process"),
                "argv": list(run.invocation),
                "working_directory": run.invocation_working_directory,
            },
            "repository": dict(run.repository),
            "metrics": metrics,
            "items": {
                item_id: sorted(run.rows[item_id])
                for item_id in sorted(selected_ids)
            },
            "stage_snapshot": {
                "items": len(stage_by_item),
                "field_counts": dict(sorted(stage_fields.items())),
                "by_item": stage_by_item,
                "file": snapshot_file,
            },
        }
        item_scores[run.key] = per_item
    candidate = next(
        (run for run in runs if run.stack == "folio-resolve" and run.lane == "candidate"), None
    )
    if candidate is None:
        raise ComparisonError("comparison is missing folio-resolve:candidate")
    ids = [item.item_id for item in scoreable]
    verdicts: dict[str, object] = {}
    for incumbent in sorted(
        (run for run in runs if run.lane == "incumbent"), key=lambda value: value.stack
    ):
        verdicts[incumbent.stack] = classify_verdict(
            [item_scores[candidate.key][item_id] for item_id in ids],
            [item_scores[incumbent.key][item_id] for item_id in ids],
            n_resamples=n_resamples,
            seed=seed,
        )
    if isinstance(comparison_invocation, Mapping):
        comparison_receipt = dict(comparison_invocation)
    else:
        comparison_receipt = {
            "kind": "equivalent",
            "argv": list(comparison_invocation),
            "working_directory": "$FOLIO_RESOLVE_REPOSITORY_ROOT",
        }
    return {
        "kind": "synthetic_comparison",
        "corpus": {
            "version": corpus.manifest.version,
            "content_sha256": corpus.manifest.content_sha256,
            "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        },
        "pilot": limit is not None or item_ids is not None,
        "run_kind": (
            "shard"
            if item_ids is not None
            else "live_gate"
            if not include_nomatch
            else "pilot"
            if limit
            else "final"
        ),
        "limit": limit,
        "scoreable_items": len(ids),
        "nomatch_items": len(nomatch),
        "folio_python_version": common_folio_python,
        "provenance": {
            "comparison_invocation": comparison_receipt,
            "cohort_selection": {
                "rule": (
                    "explicit_item_ids"
                    if item_ids is not None
                    else "corpus_manifest_order_prefix"
                ),
                "scoreable_limit": limit,
                "include_nomatch": include_nomatch,
                "scoreable_item_ids": ids,
                "nomatch_item_ids": [item.item_id for item in nomatch],
            },
            "config_selection": {
                "answer_rule_config_sha256": config.content_sha256(),
                "answer_rule_config": config.to_json(),
            },
            "committed_set_rule": {
                "metric": "strict_item_level_iri_set",
                "candidate": "rank_candidates_then_commit_from_ranked_with_recorded_config",
                "incumbents": "runner_emitted_deterministic_iri_set",
                "gold_join_key": "item_id",
            },
            "gold_by_item": {
                item.item_id: sorted(item.gold_iris) for item in (*scoreable, *nomatch)
            },
            "items_file": dict(items_file or {}),
        },
        "stacks": stack_payloads,
        "verdicts": verdicts,
    }


def _comparison_value_and_resolved_path(
    payload: Mapping[str, object], path: tuple[str, ...]
) -> tuple[object, tuple[str, ...]]:
    value: object = payload
    resolved: list[str] = []
    for part in path:
        if isinstance(value, Mapping) and part in value:
            value = value[part]
            resolved.append(part)
        elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
            index = int(part)
            value = value[index]
            resolved.append(str(index))
        elif isinstance(value, (list, tuple)) and part.startswith("--"):
            separate_matches = [index for index, item in enumerate(value) if item == part]
            equals_prefix = f"{part}="
            equals_matches = [
                index
                for index, item in enumerate(value)
                if isinstance(item, str) and item.startswith(equals_prefix)
            ]
            if len(separate_matches) + len(equals_matches) != 1:
                raise ComparisonError(
                    f"comparison public metadata option is missing or duplicated: {part}"
                )
            if separate_matches:
                index = separate_matches[0] + 1
                if index >= len(value):
                    raise ComparisonError(
                        f"comparison public metadata option is missing or duplicated: {part}"
                    )
                value = value[index]
            else:
                index = equals_matches[0]
                option_token = value[index]
                assert isinstance(option_token, str)
                value = option_token.removeprefix(equals_prefix)
            resolved.append(str(index))
        else:
            raise ComparisonError(f"comparison public metadata path missing: {path!r}")
    return value, tuple(resolved)


def _comparison_value_at_path(
    payload: Mapping[str, object], path: tuple[str, ...]
) -> object:
    return _comparison_value_and_resolved_path(payload, path)[0]


def preflight_comparison_publication(
    payload: Mapping[str, object],
    leak_manifest: Manifest,
    salt: bytes,
    *,
    public_metadata: PublicComparisonMetadata | None = None,
) -> None:
    """Fail closed on comparison strings except independently approved public fields."""
    public_fields: dict[tuple[str, ...], str] = {}
    if public_metadata is not None:
        config_sha = _comparison_value_at_path(
            payload,
            ("provenance", "config_selection", "answer_rule_config_sha256"),
        )
        if config_sha != public_metadata.answer_rule_config_sha256:
            raise ComparisonError("comparison public metadata answer-rule config hash mismatch")
        for path, (kind, expected) in public_metadata.fields.items():
            if path[0] == "stacks":
                stacks = payload.get("stacks")
                if not isinstance(stacks, Mapping):
                    raise ComparisonError("comparison stacks are malformed")
                if path[1] not in stacks:
                    continue
            if kind == "template":
                if len(path) < 2 or path[0] != "stacks":
                    raise ComparisonError("comparison public metadata template is not stack-bound")
                working_directory = _comparison_value_at_path(
                    payload, ("stacks", path[1], "invocation", "working_directory")
                )
                if not isinstance(working_directory, str):
                    raise ComparisonError("comparison invocation working directory is not a string")
                expected = expected.format(working_directory=working_directory.rstrip("/"))
            actual, resolved_path = _comparison_value_and_resolved_path(payload, path)
            if actual != expected:
                raise ComparisonError(f"comparison public metadata value mismatch at path: {path!r}")
            resolved_value = _comparison_value_at_path(payload, resolved_path)
            if not isinstance(resolved_value, str):
                raise ComparisonError(
                    f"comparison public metadata value is not a string at path: {path!r}"
                )
            public_fields[resolved_path] = resolved_value

    def collisions(value: object, path: tuple[str, ...] = ()) -> int:
        if isinstance(value, str):
            if public_fields.get(path) == value:
                return 0
            return scan_text(value, leak_manifest, salt)
        has_public_descendant = any(
            field_path[: len(path)] == path for field_path in public_fields
        )
        if (
            isinstance(value, (Mapping, list, tuple, set, frozenset))
            and not has_public_descendant
        ):
            # Scan independently non-public subtrees in one pass. This preserves
            # fail-closed coverage (and can conservatively catch cross-field token
            # windows) without paying the HMAC setup cost for every repeated IRI.
            serialized_collisions = scan_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
                leak_manifest,
                salt,
            )
            escaped_collisions = 0
            pending: list[object] = [value]
            while pending:
                nested = pending.pop()
                if isinstance(nested, str):
                    if json.dumps(nested, ensure_ascii=False)[1:-1] != nested:
                        escaped_collisions += scan_text(nested, leak_manifest, salt)
                elif isinstance(nested, Mapping):
                    pending.extend(str(key) for key in nested)
                    pending.extend(nested.values())
                elif isinstance(nested, (list, tuple, set, frozenset)):
                    pending.extend(nested)
            return serialized_collisions + escaped_collisions
        if isinstance(value, Mapping):
            total = 0
            for key, nested in value.items():
                key_text = str(key)
                total += scan_text(key_text, leak_manifest, salt)
                total += collisions(nested, (*path, key_text))
            return total
        if isinstance(value, (list, tuple, set, frozenset)):
            return sum(
                collisions(nested, (*path, str(index)))
                for index, nested in enumerate(value)
            )
        return 0

    collision_count = collisions(payload)
    if collision_count:
        raise ComparisonError(f"comparison leak check failed: collisions={collision_count}")


def write_comparison(
    path: Path,
    payload: Mapping[str, object],
    leak_manifest: Manifest,
    salt: bytes,
    *,
    public_metadata: PublicComparisonMetadata | None = None,
) -> Path:
    """Leak-check every string in the artifact, then atomically write canonical JSON."""
    preflight_comparison_publication(
        payload,
        leak_manifest,
        salt,
        public_metadata=public_metadata,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _atomic_write_text(path, text)


def write_stage_snapshots(
    runs: Sequence[StackRun],
    out_dir: Path,
    *,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
) -> dict[str, dict[str, object]]:
    """Write full row-level stages and return committed-summary file fingerprints."""
    keys = [run.key for run in runs]
    if len(set(keys)) != len(keys):
        raise StackContractError("duplicate stack/lane run")
    if leak_manifest is not None and salt is None:
        raise ComparisonError("salt is required with a leak manifest")
    checked_salt = salt
    fingerprints: dict[str, dict[str, object]] = {}
    for run in runs:
        path = out_dir / run.stack / run.lane / "stages.json"
        payload = {item_id: run.stages[item_id] for item_id in sorted(run.stages)}
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if leak_manifest is not None:
            assert checked_salt is not None
            collisions = scan_text(text, leak_manifest, checked_salt)
            if collisions:
                raise ComparisonError(f"stage snapshot leak check failed: collisions={collisions}")
        content = text.encode("utf-8")
        _atomic_write_text(path, text)
        fingerprints[run.key] = {
            "path": _relative_or_absolute(path, out_dir),
            "sha256": sha256_bytes(content),
            "bytes": len(content),
        }
    return fingerprints


def run_synthetic_comparison(
    corpus: LoadedCorpus,
    *,
    adapter: DocumentAdapter,
    config: AnswerRuleConfig,
    consumers: Sequence[ConsumerSpec],
    items_path: Path,
    row_snapshot_dir: Path,
    leak_manifest: Manifest,
    salt: bytes,
    limit: int | None = None,
    include_nomatch: bool = True,
    item_ids: Sequence[str] | None = None,
    incumbent_version: str = "0.4.0",
    prepare_incumbents: bool = True,
    comparison_invocation: Mapping[str, object] | Sequence[str] = (),
) -> dict[str, object]:
    """Execute the local candidate and every pinned consumer incumbent, then aggregate."""
    _assert_config(corpus, config)
    candidate_repository = _git_repository_state(FOLIO_RESOLVE_ROOT)
    with clean_tree_guard(FOLIO_RESOLVE_ROOT):
        emit_items_file(
            corpus,
            items_path,
            limit=limit,
            include_nomatch=include_nomatch,
            item_ids=item_ids,
            extractor=adapter.phrase_extractor,
            leak_manifest=leak_manifest, salt=salt,
        )
        items_fingerprint = _file_fingerprint(items_path, root=FOLIO_RESOLVE_ROOT)
        runs: list[StackRun] = []
        for spec in consumers:
            if _file_fingerprint(items_path, root=FOLIO_RESOLVE_ROOT) != items_fingerprint:
                raise ComparisonError(
                    f"shared comparison items changed before {spec.name} execution"
                )
            runs.append(
                run_consumer_stack(
                    spec,
                    items_path,
                    version=incumbent_version,
                    prepare=prepare_incumbents,
                )
            )
            if _file_fingerprint(items_path, root=FOLIO_RESOLVE_ROOT) != items_fingerprint:
                raise ComparisonError(
                    f"shared comparison items changed during {spec.name} execution"
                )
        local_run = run_local_stack(
            corpus,
            adapter,
            config,
            limit=limit,
            include_nomatch=include_nomatch,
            item_ids=item_ids,
            items_path=items_path,
        )
        runs.append(replace(local_run, repository=candidate_repository))
        snapshot_files = write_stage_snapshots(
            runs, row_snapshot_dir, leak_manifest=leak_manifest, salt=salt
        )
        if _file_fingerprint(items_path, root=FOLIO_RESOLVE_ROOT) != items_fingerprint:
            raise ComparisonError("shared comparison items changed during candidate execution")
        return build_comparison(
            corpus,
            runs,
            config,
            limit=limit,
            include_nomatch=include_nomatch,
            item_ids=item_ids,
            comparison_invocation=comparison_invocation,
            stage_snapshot_files=snapshot_files,
            items_file=items_fingerprint,
        )
