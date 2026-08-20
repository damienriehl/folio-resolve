"""Cross-stack orchestration for the frozen synthetic corpus (U10; R15--R17).

The module is separate from :mod:`folio_eval.downstream`: U9's snapshot/diff harness remains
small, while this module owns the shared-items contract, pinned incumbent installation, exact
IRI scoring, paired verdicts, and leak-gated comparison artifact.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .answer_rule import AnswerRuleConfig, commit_from_ranked, rank_candidates
from .downstream import (
    ConsumerRunError,
    ConsumerSpec,
    clean_tree_guard,
)
from .leakcheck import Manifest, scan_text
from .report import DEFAULT_BOOTSTRAP_RESAMPLES, DEFAULT_BOOTSTRAP_SEED, bootstrap_ci
from .score import MicroCounts
from .synthesize import LoadedCorpus, SyntheticItem
from .synthetic_score import DocumentAdapter, PhraseExtractor, _assert_config, nounish_ngrams


class ComparisonError(RuntimeError):
    """Base class for a comparison contract violation."""


class StackContractError(ComparisonError):
    """A stack runner emitted malformed, duplicate, or incomplete JSONL."""


class IncumbentInstallMismatch(ComparisonError):
    """The pinned wheel did not resolve to the requested version in site-packages."""


class VersionSkewError(ComparisonError):
    """Stacks resolved different folio-python versions, invalidating comparison."""


DEFAULT_COMPARISON_TIMEOUT_S = 2 * 60 * 60


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

    @property
    def key(self) -> str:
        return f"{self.stack}:{self.lane}"


def _atomic_write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def _comparison_items(corpus: LoadedCorpus, limit: int | None) -> tuple[SyntheticItem, ...]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    scoreable = corpus.scoreable_items[:limit] if limit is not None else corpus.scoreable_items
    return tuple(scoreable) + tuple(corpus.nomatch_items)


def emit_items_file(
    corpus: LoadedCorpus,
    out_path: Path,
    *,
    limit: int | None = None,
    extractor: PhraseExtractor = nounish_ngrams,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
) -> Path:
    """Emit the common JSONL once, including the U8 extraction seam's segments.

    Every runner receives these materialized segments and must not independently segment text.
    No-match rows are retained in pilot mode so its false-positive rate remains meaningful.
    """
    lines: list[str] = []
    for item in _comparison_items(corpus, limit):
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


def run_consumer_stack(
    spec: ConsumerSpec,
    items_path: Path,
    *,
    version: str = "0.4.0",
    timeout: float = DEFAULT_COMPARISON_TIMEOUT_S,
) -> StackRun:
    """Prepare and run one incumbent consumer through its own interpreter."""
    relative_runner = {
        "folio-enrich": Path("backend/eval/synthetic_runner.py"),
        "folio-mapper": Path("backend/scripts/synthetic_runner.py"),
    }.get(spec.name)
    if relative_runner is None:
        raise ComparisonError(f"unknown comparison consumer: {spec.name}")
    with tempfile.TemporaryDirectory(prefix=f"{spec.name}-comparison-") as temporary:
        out_path = Path(temporary) / "stack.jsonl"
        with clean_tree_guard(spec.repo_root):
            prepare_incumbent(spec, version)
            try:
                completed = subprocess.run(
                    [
                        str(spec.venv_python),
                        str(relative_runner),
                        "--items",
                        str(items_path),
                        "--out",
                        str(out_path),
                        "--lane",
                        "deterministic",
                    ],
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
    return replace(run, lane="incumbent")


def run_local_stack(
    corpus: LoadedCorpus,
    adapter: DocumentAdapter,
    config: AnswerRuleConfig,
    *,
    limit: int | None = None,
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
    for item in _comparison_items(corpus, limit):
        segments = (
            materialized[item.item_id] if items_path is not None else tuple(extractor(item.text))
        )
        original_extractor = adapter.phrase_extractor
        try:
            # Preserve U8's whole-document exact sweep while injecting the materialized
            # extraction result, so the local lane cannot independently segment the text.
            adapter.phrase_extractor = _fixed_extractor(segments)
            candidates = list(adapter.adapt(item.text).candidates)
        finally:
            adapter.phrase_extractor = original_extractor
        committed = commit_from_ranked(rank_candidates(candidates, config), config)
        rows[item.item_id] = frozenset(candidate.iri for candidate in committed)
        stages[item.item_id] = {"segments": len(segments), "candidates": len(candidates)}
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
    )


def _fixed_extractor(segments: Sequence[str]) -> PhraseExtractor:
    def extract(_text: str) -> Sequence[str]:
        return segments

    return extract


def _selected_scoreable(corpus: LoadedCorpus, limit: int | None) -> tuple[SyntheticItem, ...]:
    return corpus.scoreable_items[:limit] if limit is not None else corpus.scoreable_items


def score_stack(
    corpus: LoadedCorpus, run: StackRun, *, limit: int | None = None
) -> tuple[dict[str, object], dict[str, float]]:
    """Join predictions to gold by item_id and compute strict set micro metrics."""
    counts = MicroCounts()
    item_f1: dict[str, float] = {}
    for item in _selected_scoreable(corpus, limit):
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
    for item in corpus.nomatch_items:
        if item.item_id not in run.rows:
            raise StackContractError(f"{run.key} omitted no-match item {item.item_id!r}")
        false_positives += bool(run.rows[item.item_id])
    metrics = counts.to_json()
    metrics["nomatch_items"] = len(corpus.nomatch_items)
    metrics["nomatch_false_positives"] = false_positives
    metrics["nomatch_fp_rate"] = round(
        false_positives / len(corpus.nomatch_items) if corpus.nomatch_items else 0.0, 6
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
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
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
    for run in sorted(runs, key=lambda value: value.key):
        metrics, per_item = score_stack(corpus, run, limit=limit)
        selected_ids = {item.item_id for item in _selected_scoreable(corpus, limit)}
        stage_fields: dict[str, int] = {}
        for stage in run.stages.values():
            for field in stage:
                stage_fields[field] = stage_fields.get(field, 0) + 1
        stack_payloads[run.key] = {
            "stack": run.stack,
            "lane": run.lane,
            "versions": {
                "folio-resolve": run.folio_resolve_version,
                "folio-python": run.folio_python_version,
            },
            "config": dict(run.config),
            "metrics": metrics,
            "items": {item_id: sorted(run.rows[item_id]) for item_id in sorted(selected_ids)},
            "stage_snapshot": {
                "items": len(run.stages),
                "field_counts": dict(sorted(stage_fields.items())),
            },
        }
        item_scores[run.key] = per_item
    candidate = next(
        (run for run in runs if run.stack == "folio-resolve" and run.lane == "candidate"), None
    )
    if candidate is None:
        raise ComparisonError("comparison is missing folio-resolve:candidate")
    ids = [item.item_id for item in _selected_scoreable(corpus, limit)]
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
    return {
        "kind": "synthetic_comparison",
        "corpus": {
            "version": corpus.manifest.version,
            "content_sha256": corpus.manifest.content_sha256,
            "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        },
        "pilot": limit is not None,
        "limit": limit,
        "scoreable_items": len(ids),
        "folio_python_version": common_folio_python,
        "stacks": stack_payloads,
        "verdicts": verdicts,
    }


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key)
            yield from _strings(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            yield from _strings(nested)


def write_comparison(
    path: Path, payload: Mapping[str, object], leak_manifest: Manifest, salt: bytes
) -> Path:
    """Leak-check every string in the artifact, then atomically write canonical JSON."""
    collisions = sum(scan_text(value, leak_manifest, salt) for value in _strings(payload))
    if collisions:
        raise ComparisonError(f"comparison leak check failed: collisions={collisions}")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _atomic_write_text(path, text)


def write_stage_snapshots(
    runs: Sequence[StackRun],
    out_dir: Path,
    *,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
) -> tuple[Path, ...]:
    """Write row-level stage data to a caller-selected gitignored directory."""
    keys = [run.key for run in runs]
    if len(set(keys)) != len(keys):
        raise StackContractError("duplicate stack/lane run")
    if leak_manifest is not None and salt is None:
        raise ComparisonError("salt is required with a leak manifest")
    checked_salt = salt
    paths: list[Path] = []
    for run in runs:
        path = out_dir / run.stack / run.lane / "stages.json"
        payload = {item_id: run.stages[item_id] for item_id in sorted(run.stages)}
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if leak_manifest is not None:
            assert checked_salt is not None
            collisions = scan_text(text, leak_manifest, checked_salt)
            if collisions:
                raise ComparisonError(f"stage snapshot leak check failed: collisions={collisions}")
        paths.append(_atomic_write_text(path, text))
    return tuple(paths)


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
    incumbent_version: str = "0.4.0",
) -> dict[str, object]:
    """Execute the local candidate and every pinned consumer incumbent, then aggregate."""
    _assert_config(corpus, config)
    emit_items_file(
        corpus, items_path, limit=limit, extractor=adapter.phrase_extractor,
        leak_manifest=leak_manifest, salt=salt,
    )
    runs = [
        run_consumer_stack(spec, items_path, version=incumbent_version) for spec in consumers
    ]
    runs.append(run_local_stack(corpus, adapter, config, limit=limit, items_path=items_path))
    write_stage_snapshots(runs, row_snapshot_dir, leak_manifest=leak_manifest, salt=salt)
    return build_comparison(corpus, runs, config, limit=limit)
