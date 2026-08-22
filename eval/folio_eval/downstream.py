"""Downstream consumer validation — snapshot/diff (U9; R13; KTD10).

KTD10's contract, implemented here:

1. **Editable-install the LOCAL library** into each consumer's own venv, via
   ``uv pip install --python <consumer venv> -e <folio-resolve root> --no-deps`` (or the
   ``pip install -e`` equivalent) — never bare ``uv run`` inside the consumer repo, which
   re-syncs from that repo's lockfile and silently reverts the editable install.
2. **Assert the install landed** — ``folio_resolve.__file__`` must resolve *inside* the local
   checkout — before running anything; abort that consumer's run otherwise
   (:class:`EditableInstallMismatch`).
3. **Run entry points via the consumer's own venv interpreter** (``.venv/bin/python``): the demo
   probe driver and test suite for folio-mapper, the migration harness and test suite for
   folio-enrich.
4. **Normalize into a snapshot** — a gitignored row-level record per consumer under
   ``eval/data/reports/downstream_baseline/<consumer>/`` (surface strings live here; consumer
   demo/test data is synthetic and public, but the split still follows KTD1's committed/row-level
   line) and a committed aggregate at ``eval/reports/downstream-baseline-v1.json`` holding counts,
   statuses, and content hashes only.
5. **Diff mode** (:func:`diff_snapshots`) classifies deltas between two row-level snapshots per
   the KTD10 rule: a previously-correct probe hit disappearing, or a previously-passing test now
   failing, is **blocking**; every other delta (a new hit, a changed ranking, a newly added test)
   is **advisory**.

**Working-tree hygiene.** Nothing is committed in a consumer repo, and nothing is left dirty
there either. The demo-probe candidates files are consumer-local and gitignored in folio-mapper;
this module still restores their prior bytes so a snapshot run has zero side effects on disk.
folio-enrich's ``migration/captures/`` files ARE git-tracked, so the harness is invoked with a
throwaway ``--out`` label and the resulting file is deleted after its content is copied into the
snapshot — the tracked ``baseline.json``/``candidate.json`` captures are never touched. Every
runner call brackets itself with a ``git status --porcelain`` snapshot and asserts the two match
byte-for-byte; a mismatch raises :class:`ConsumerTreeDirty` rather than reporting a result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# eval/folio_eval/downstream.py -> eval/folio_eval -> eval -> folio-resolve root
_EVAL_ROOT = Path(__file__).resolve().parent.parent
FOLIO_RESOLVE_ROOT = _EVAL_ROOT.parent
_SIBLING_ROOT = FOLIO_RESOLVE_ROOT.parent

#: Committed: counts, statuses, content hashes only (KTD1's committed/row-level line).
DEFAULT_SUMMARY_PATH = _EVAL_ROOT / "reports" / "downstream-baseline-v1.json"
#: Gitignored: the actual row-level snapshot content the diff mode reads (KTD1).
DEFAULT_ROW_REPORT_DIR = _EVAL_ROOT / "data" / "reports" / "downstream_baseline"

DEFAULT_MAPPER_ROOT = _SIBLING_ROOT / "folio-mapper"
DEFAULT_ENRICH_ROOT = _SIBLING_ROOT / "folio-enrich"

DEFAULT_TEST_TIMEOUT_S = 1800.0
DEFAULT_PROBE_TIMEOUT_S = 120.0
DEFAULT_HARNESS_TIMEOUT_S = 300.0


class DownstreamError(RuntimeError):
    """Base for every abort condition this module raises."""


class EditableInstallMismatch(DownstreamError):
    """``folio_resolve.__file__`` did not resolve inside the local checkout (KTD10)."""


class ConsumerTreeDirty(DownstreamError):
    """A consumer repo's working tree changed status across a run — refuses to report a result."""


class ConsumerRunError(DownstreamError):
    """A subprocess invocation in a consumer repo failed unexpectedly (non-timeout, non-test)."""


# --------------------------------------------------------------------------------------
# Git hygiene
# --------------------------------------------------------------------------------------


def git_status_porcelain(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def assert_same_status(repo_root: Path, before: str, *, when: str) -> None:
    """Raise unless the repo's status is byte-identical to ``before`` (KTD10: leave trees clean)."""
    after = git_status_porcelain(repo_root)
    if after != before:
        raise ConsumerTreeDirty(
            f"{repo_root} working tree changed {when} — refusing to report a result.\n"
            f"--- before ---\n{before or '(clean)'}\n--- after ---\n{after or '(clean)'}"
        )


class clean_tree_guard:  # used as a context manager, lowercase by convention
    """Snapshot a consumer repo's git status on entry; assert it is unchanged on exit.

    Wraps a whole snapshot run (install + every subprocess call) rather than each call
    individually, since editable install and entry-point runs are meant to be side-effect-free
    together, not merely in isolation.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.before = ""

    def __enter__(self) -> clean_tree_guard:
        self.before = git_status_porcelain(self.repo_root)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            assert_same_status(self.repo_root, self.before, when="after the snapshot run")


# --------------------------------------------------------------------------------------
# Consumer spec + editable install + the __file__ assertion
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsumerSpec:
    name: str
    repo_root: Path
    venv_python: Path

    def exists(self) -> bool:
        return self.repo_root.is_dir() and self.venv_python.is_file()


def mapper_spec(repo_root: Path | None = None) -> ConsumerSpec:
    root = (repo_root or DEFAULT_MAPPER_ROOT).resolve()
    return ConsumerSpec(
        name="folio-mapper",
        repo_root=root,
        venv_python=root / "backend" / ".venv" / "bin" / "python",
    )


def enrich_spec(repo_root: Path | None = None) -> ConsumerSpec:
    root = (repo_root or DEFAULT_ENRICH_ROOT).resolve()
    return ConsumerSpec(
        name="folio-enrich",
        repo_root=root,
        venv_python=root / "backend" / ".venv" / "bin" / "python",
    )


def editable_install(spec: ConsumerSpec, folio_resolve_root: Path = FOLIO_RESOLVE_ROOT) -> str:
    """``uv pip install --python <venv> -e <folio-resolve> --no-deps`` — touches only the venv.

    Deliberately NOT ``uv run`` / ``uv sync`` (those consult the consumer's own lockfile and
    would revert this install) and NOT ``uv add`` (that rewrites the consumer's pyproject.toml).
    Targeting the venv interpreter directly with ``--python`` keeps this out of project-file
    territory entirely.
    """
    completed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(spec.venv_python),
            "-e",
            str(folio_resolve_root),
            "--no-deps",
        ],
        cwd=str(folio_resolve_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise ConsumerRunError(
            f"editable install into {spec.name} failed (rc={completed.returncode}):\n"
            f"{completed.stderr.strip()[-4000:]}"
        )
    return completed.stdout + completed.stderr


def _resolved_module_file(venv_python: Path, module: str, *, timeout: float = 30.0) -> Path:
    completed = subprocess.run(
        [
            str(venv_python),
            "-c",
            f"import {module}, os; print(os.path.realpath({module}.__file__))",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise EditableInstallMismatch(
            f"could not import {module!r} via {venv_python}: {completed.stderr.strip()[-2000:]}"
        )
    line = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    if not line:
        raise EditableInstallMismatch(
            f"{module}.__file__ resolved to an empty path via {venv_python}"
        )
    return Path(line)


def assert_within_root(
    resolved: Path, expected_root: Path, *, module: str = "folio_resolve"
) -> Path:
    """Pure containment check — the assertion's logic, separated so it is testable without a venv."""
    root = expected_root.resolve()
    if not resolved.is_relative_to(root):
        raise EditableInstallMismatch(
            f"{module}.__file__ resolved to {resolved}, which is NOT inside {root} — "
            "the editable install did not land (or something re-synced over it). Aborting "
            "rather than validating against the wrong library (KTD10)."
        )
    return resolved


def assert_editable_install(
    spec: ConsumerSpec, folio_resolve_root: Path = FOLIO_RESOLVE_ROOT
) -> Path:
    """Resolve ``folio_resolve.__file__`` inside ``spec``'s venv and assert it is the local checkout."""
    resolved = _resolved_module_file(spec.venv_python, "folio_resolve")
    return assert_within_root(resolved, folio_resolve_root)


# --------------------------------------------------------------------------------------
# Shared: pytest runner (junitxml parse)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TestSuiteResult:
    __test__ = False  # not a pytest test class -- the name collision is coincidental

    consumer: str
    command: tuple[str, ...]
    returncode: int
    elapsed_s: float
    outcomes: Mapping[str, str] = field(
        default_factory=dict
    )  # test nodeid -> passed/failed/error/skipped
    collect_error: str = ""

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def content_sha256(self) -> str:
        pairs = sorted(self.outcomes.items())
        return hashlib.sha256(
            json.dumps(pairs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def to_summary_json(self) -> dict[str, object]:
        """Committed-eligible: counts and a hash, never a test name (KTD1 -- consumer test names
        are permitted per the plan, but the aggregate stays counts+hashes for consistency)."""
        return {
            "consumer": self.consumer,
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "total": len(self.outcomes),
            "counts": dict(sorted(self.counts.items())),
            "outcomes_sha256": self.content_sha256(),
            "collect_error": bool(self.collect_error),
        }

    def to_row_json(self) -> dict[str, object]:
        return {
            "consumer": self.consumer,
            "command": list(self.command),
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "outcomes": dict(sorted(self.outcomes.items())),
            "collect_error": self.collect_error,
        }


_JUNIT_OUTCOME_TAGS = {"failure": "failed", "error": "error", "skipped": "skipped"}


def parse_junitxml(path: Path) -> dict[str, str]:
    """Per-test outcome from a junitxml report. A testcase with no child tag passed."""
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    outcomes: dict[str, str] = {}
    for suite in suites:
        for case in suite.iter("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            nodeid = f"{classname}::{name}" if classname else name
            outcome = "passed"
            for child in case:
                tag = child.tag
                if tag in _JUNIT_OUTCOME_TAGS:
                    outcome = _JUNIT_OUTCOME_TAGS[tag]
                    break
            outcomes[nodeid] = outcome
    return outcomes


def run_pytest(
    spec: ConsumerSpec,
    *,
    cwd: Path,
    test_args: Sequence[str] = ("tests",),
    timeout: float = DEFAULT_TEST_TIMEOUT_S,
) -> TestSuiteResult:
    """Run the consumer's own test suite via its own venv interpreter, read-only.

    Uses ``--tb=no -q`` (cheap console output) plus ``--junitxml`` to a tempfile OUTSIDE the
    consumer repo, so per-test outcomes parse reliably without writing anything into the repo.
    """
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
        junit_path = Path(handle.name)
    try:
        cmd = [
            str(spec.venv_python),
            "-m",
            "pytest",
            *test_args,
            "--tb=no",
            "-q",
            f"--junitxml={junit_path}",
        ]
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            elapsed = time.perf_counter() - started
            return TestSuiteResult(
                consumer=spec.name,
                command=tuple(cmd),
                returncode=124,
                elapsed_s=elapsed,
                outcomes={},
                collect_error=f"timed out after {timeout}s: {error}",
            )
        elapsed = time.perf_counter() - started
        outcomes = parse_junitxml(junit_path)
        collect_error = ""
        if not outcomes and completed.returncode not in (0, 1):
            # rc 0/1 = suite ran (all passed / some failed); anything else usually means
            # collection blew up before junitxml had anything to write.
            collect_error = (completed.stdout + completed.stderr).strip()[-4000:]
        return TestSuiteResult(
            consumer=spec.name,
            command=tuple(cmd),
            returncode=completed.returncode,
            elapsed_s=elapsed,
            outcomes=outcomes,
            collect_error=collect_error,
        )
    finally:
        junit_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------
# folio-mapper: demo probes
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeItemResult:
    index: int
    item: str
    level: str
    top_relevant_iris: tuple[str, ...]
    total_candidates: int
    relevant_candidates: int
    high_score_relevant: int

    def to_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "item": self.item,
            "level": self.level,
            "top_relevant_iris": list(self.top_relevant_iris),
            "total_candidates": self.total_candidates,
            "relevant_candidates": self.relevant_candidates,
            "high_score_relevant": self.high_score_relevant,
        }


@dataclass(frozen=True, slots=True)
class ProbeAreaResult:
    area: str
    items: tuple[ProbeItemResult, ...]
    elapsed_s: float
    returncode: int
    error: str = ""

    def content_sha256(self) -> str:
        pairs = [(item.index, item.item, sorted(item.top_relevant_iris)) for item in self.items]
        return hashlib.sha256(
            json.dumps(pairs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def to_summary_json(self) -> dict[str, object]:
        return {
            "area": self.area,
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "items": len(self.items),
            "relevant_candidates_total": sum(item.relevant_candidates for item in self.items),
            "high_score_relevant_total": sum(item.high_score_relevant for item in self.items),
            "content_sha256": self.content_sha256(),
            "error": bool(self.error),
        }

    def to_row_json(self) -> dict[str, object]:
        return {
            "area": self.area,
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
            "items": [item.to_json() for item in self.items],
        }


def discover_mapper_areas(spec: ConsumerSpec) -> tuple[str, ...]:
    """Every practice area ``run_probe.py`` can drive — those carrying a ``*-probe-items.json``."""
    demos = spec.repo_root / "scripts" / "demos"
    if not demos.is_dir():
        return ()
    suffix = "-probe-items.json"
    return tuple(sorted(p.name[: -len(suffix)] for p in demos.glob(f"*{suffix}")))


def run_mapper_probe(
    spec: ConsumerSpec, area: str, *, limit: int = 20, timeout: float = DEFAULT_PROBE_TIMEOUT_S
) -> ProbeAreaResult:
    """Run ``run_probe.py --area <area>`` and restore the (gitignored) candidates file after."""
    demos = spec.repo_root / "scripts" / "demos"
    candidates_path = demos / f"{area}-probe-candidates.json"
    original = candidates_path.read_bytes() if candidates_path.exists() else None
    cmd = [
        str(spec.venv_python),
        "scripts/demos/run_probe.py",
        "--area",
        area,
        "--limit",
        str(limit),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd, cwd=str(spec.repo_root), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as error:
        elapsed = time.perf_counter() - started
        _restore_bytes(candidates_path, original)
        return ProbeAreaResult(
            area=area, items=(), elapsed_s=elapsed, returncode=124, error=str(error)
        )
    elapsed = time.perf_counter() - started

    if completed.returncode != 0 or not candidates_path.exists():
        _restore_bytes(candidates_path, original)
        return ProbeAreaResult(
            area=area,
            items=(),
            elapsed_s=elapsed,
            returncode=completed.returncode,
            error=(completed.stdout + completed.stderr).strip()[-4000:],
        )

    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    _restore_bytes(candidates_path, original)

    items = tuple(
        ProbeItemResult(
            index=int(row.get("index", 0)),
            item=str(row.get("item", "")),
            level=str(row.get("level", "")),
            top_relevant_iris=tuple(
                str(cand.get("iri", "")) for cand in row.get("top_relevant", [])
            ),
            total_candidates=int(row.get("total_candidates", 0)),
            relevant_candidates=int(row.get("relevant_candidates", 0)),
            high_score_relevant=int(row.get("high_score_relevant", 0)),
        )
        for row in payload.get("results", [])
    )
    return ProbeAreaResult(area=area, items=items, elapsed_s=elapsed, returncode=0)


def _restore_bytes(path: Path, original: bytes | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)


# --------------------------------------------------------------------------------------
# folio-enrich: migration harness
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HarnessResult:
    label_resolution: tuple[tuple[str, str], ...]  # (item id, resolved iri or "")
    elapsed_s: float
    returncode: int
    error: str = ""
    environment: Mapping[str, object] = field(default_factory=dict)

    def content_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(list(self.label_resolution), ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()

    def to_summary_json(self) -> dict[str, object]:
        resolved = sum(1 for _, iri in self.label_resolution if iri)
        return {
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "items": len(self.label_resolution),
            "resolved": resolved,
            "content_sha256": self.content_sha256(),
            "folio_resolve_present": bool(self.environment.get("folio_resolve_present")),
            "error": bool(self.error),
        }

    def to_row_json(self) -> dict[str, object]:
        return {
            "returncode": self.returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
            "environment": dict(self.environment),
            "label_resolution": [
                {"id": item_id, "iri": iri} for item_id, iri in self.label_resolution
            ],
        }


def run_enrich_harness(
    spec: ConsumerSpec,
    *,
    out_label: str = "downstream-snapshot-tmp",
    timeout: float = DEFAULT_HARNESS_TIMEOUT_S,
) -> HarnessResult:
    """Run ``migration/harness.py --out <throwaway label>``; delete the file it creates after.

    Deliberately never writes ``--out baseline`` / ``--out candidate`` — those filenames ARE the
    git-tracked pre/post-swap migration captures. A distinct label writes a brand-new untracked
    file that this function deletes once its content is copied out, so the tracked captures are
    never at risk.
    """
    backend = spec.repo_root / "backend"
    captures_dir = backend / "migration" / "captures"
    out_path = captures_dir / f"{out_label}.json"
    if out_path.exists():
        raise ConsumerRunError(f"refusing to overwrite a pre-existing file: {out_path}")

    cmd = [str(spec.venv_python), "migration/harness.py", "--out", out_label]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd, cwd=str(backend), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as error:
        elapsed = time.perf_counter() - started
        out_path.unlink(missing_ok=True)
        return HarnessResult(
            label_resolution=(), elapsed_s=elapsed, returncode=124, error=str(error)
        )
    elapsed = time.perf_counter() - started

    if completed.returncode != 0 or not out_path.exists():
        message = (completed.stdout + completed.stderr).strip()[-4000:]
        out_path.unlink(missing_ok=True)
        return HarnessResult(
            label_resolution=(), elapsed_s=elapsed, returncode=completed.returncode, error=message
        )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    out_path.unlink()  # this file was never meant to persist -- keep the consumer tree clean

    label_resolution = tuple(
        (str(row.get("id", "")), str((row.get("primary") or {}).get("iri", "")))
        for row in payload.get("label_resolution", [])
    )
    return HarnessResult(
        label_resolution=label_resolution,
        elapsed_s=elapsed,
        returncode=0,
        environment=payload.get("environment", {}),
    )


# --------------------------------------------------------------------------------------
# Snapshot assembly (one consumer)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsumerSnapshot:
    consumer: str
    resolved_folio_resolve_file: str
    probes: Mapping[str, ProbeAreaResult] = field(default_factory=dict)
    harness: HarnessResult | None = None
    tests: TestSuiteResult | None = None

    def to_row_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "consumer": self.consumer,
            "resolved_folio_resolve_file": self.resolved_folio_resolve_file,
        }
        if self.probes:
            payload["probes"] = {area: r.to_row_json() for area, r in sorted(self.probes.items())}
        if self.harness is not None:
            payload["harness"] = self.harness.to_row_json()
        if self.tests is not None:
            payload["tests"] = self.tests.to_row_json()
        return payload

    def to_summary_json(self) -> dict[str, object]:
        payload: dict[str, object] = {"consumer": self.consumer}
        if self.probes:
            payload["probes"] = {
                area: r.to_summary_json() for area, r in sorted(self.probes.items())
            }
        if self.harness is not None:
            payload["harness"] = self.harness.to_summary_json()
        if self.tests is not None:
            payload["tests"] = self.tests.to_summary_json()
        return payload


def snapshot_mapper(
    spec: ConsumerSpec,
    *,
    folio_resolve_root: Path = FOLIO_RESOLVE_ROOT,
    test_timeout: float = DEFAULT_TEST_TIMEOUT_S,
) -> ConsumerSnapshot:
    with clean_tree_guard(spec.repo_root):
        editable_install(spec, folio_resolve_root)
        resolved = assert_editable_install(spec, folio_resolve_root)
        areas = discover_mapper_areas(spec)
        probes = {area: run_mapper_probe(spec, area) for area in areas}
        tests = run_pytest(
            spec, cwd=spec.repo_root / "backend", test_args=("tests",), timeout=test_timeout
        )
    return ConsumerSnapshot(
        consumer=spec.name,
        resolved_folio_resolve_file=str(resolved),
        probes=probes,
        tests=tests,
    )


def snapshot_enrich(
    spec: ConsumerSpec,
    *,
    folio_resolve_root: Path = FOLIO_RESOLVE_ROOT,
    test_timeout: float = DEFAULT_TEST_TIMEOUT_S,
) -> ConsumerSnapshot:
    with clean_tree_guard(spec.repo_root):
        editable_install(spec, folio_resolve_root)
        resolved = assert_editable_install(spec, folio_resolve_root)
        harness = run_enrich_harness(spec)
        tests = run_pytest(
            spec, cwd=spec.repo_root / "backend", test_args=("tests",), timeout=test_timeout
        )
    return ConsumerSnapshot(
        consumer=spec.name,
        resolved_folio_resolve_file=str(resolved),
        harness=harness,
        tests=tests,
    )


# --------------------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_row_snapshot(snapshot: ConsumerSnapshot, out_dir: Path = DEFAULT_ROW_REPORT_DIR) -> Path:
    path = out_dir / snapshot.consumer / "snapshot.json"
    _atomic_write_text(path, canonical_json(snapshot.to_row_json()))
    return path


def load_row_snapshot(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DownstreamError(f"snapshot at {path} is not a JSON object")
    return payload


def build_aggregate(
    snapshots: Sequence[ConsumerSnapshot], *, label: str = "baseline-v1"
) -> dict[str, object]:
    return {
        "label": label,
        "kind": "downstream_baseline",
        "consumers": {
            snap.consumer: snap.to_summary_json()
            for snap in sorted(snapshots, key=lambda s: s.consumer)
        },
    }


def write_aggregate(
    payload: Mapping[str, object],
    path: Path = DEFAULT_SUMMARY_PATH,
    *,
    leak_surfaces: Iterable[str] = (),
) -> Path:
    """Write the committed aggregate, leak-scanning it first against ``leak_surfaces`` (KTD1).

    Reuses :mod:`folio_eval.clusters`'s scan so the same rule U4 used for the baseline report
    governs this one: any gold/firm surface string present aborts the write.
    """
    text = canonical_json(payload)
    if leak_surfaces:
        from .clusters import assert_no_surfaces

        assert_no_surfaces(text, leak_surfaces, what=str(path))
    _atomic_write_text(path, text)
    return path


# --------------------------------------------------------------------------------------
# Diff mode (KTD10 blocking/advisory classification)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiffVerdict:
    blocking: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking)

    def to_json(self) -> dict[str, object]:
        return {
            "blocking": list(self.blocking),
            "advisory": list(self.advisory),
            "blocking_count": len(self.blocking),
            "advisory_count": len(self.advisory),
        }

    def __add__(self, other: DiffVerdict) -> DiffVerdict:
        return DiffVerdict(
            blocking=self.blocking + other.blocking, advisory=self.advisory + other.advisory
        )


def classify_probe_item_delta(
    key: str, before_iris: frozenset[str], after_iris: frozenset[str]
) -> DiffVerdict:
    """A gold-hit IRI dropping out of an item's candidate set is blocking; a new one is advisory."""
    dropped = sorted(before_iris - after_iris)
    added = sorted(after_iris - before_iris)
    return DiffVerdict(
        blocking=tuple(f"{key}: lost candidate {iri}" for iri in dropped),
        advisory=tuple(f"{key}: gained candidate {iri}" for iri in added),
    )


def classify_probe_diff(
    before: Mapping[str, frozenset[str]], after: Mapping[str, frozenset[str]]
) -> DiffVerdict:
    """Item-keyed candidate-set diff — the probe half of KTD10's rule."""
    verdict = DiffVerdict()
    for key in sorted(set(before) | set(after)):
        if key not in after:
            verdict += DiffVerdict(blocking=(f"{key}: item missing from the new run entirely",))
            continue
        if key not in before:
            verdict += DiffVerdict(advisory=(f"{key}: new item in the new run",))
            continue
        verdict += classify_probe_item_delta(key, before[key], after[key])
    return verdict


def classify_test_diff(before: Mapping[str, str], after: Mapping[str, str]) -> DiffVerdict:
    """previously-passing -> failing (or error) is blocking; every other delta is advisory."""
    blocking: list[str] = []
    advisory: list[str] = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name)
        a = after.get(name)
        if b == a:
            continue
        if b == "passed" and a is not None and a != "passed":
            blocking.append(f"{name}: passed -> {a}")
        else:
            advisory.append(f"{name}: {b or 'new'} -> {a or 'removed'}")
    return DiffVerdict(blocking=tuple(blocking), advisory=tuple(advisory))


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []


def _iri_sets_from_probes(probes_payload: object) -> dict[str, frozenset[str]]:
    """``{area}#{index}`` -> the item's committed candidate IRI set, from a row-snapshot payload."""
    out: dict[str, frozenset[str]] = {}
    for area, area_payload in _as_mapping(probes_payload).items():
        for item in _as_sequence(_as_mapping(area_payload).get("items")):
            item_map = _as_mapping(item)
            key = f"{area}#{item_map.get('index')}"
            iris = _as_sequence(item_map.get("top_relevant_iris"))
            out[key] = frozenset(str(iri) for iri in iris)
    return out


def _iri_sets_from_harness(harness_payload: object) -> dict[str, frozenset[str]]:
    """``item id`` -> its resolved IRI (as a singleton set, or empty when unresolved)."""
    out: dict[str, frozenset[str]] = {}
    for row in _as_sequence(_as_mapping(harness_payload).get("label_resolution")):
        row_map = _as_mapping(row)
        item_id = str(row_map.get("id", ""))
        iri = row_map.get("iri")
        out[item_id] = frozenset([str(iri)] if iri else [])
    return out


def _test_outcomes(tests_payload: object) -> Mapping[str, str]:
    outcomes = _as_mapping(tests_payload).get("outcomes")
    if not isinstance(outcomes, Mapping):
        return {}
    return {str(key): str(value) for key, value in outcomes.items()}


def diff_snapshots(before: Mapping[str, object], after: Mapping[str, object]) -> DiffVerdict:
    """Diff two row-level ``ConsumerSnapshot.to_row_json()`` payloads per KTD10."""
    verdict = DiffVerdict()
    if "probes" in before or "probes" in after:
        verdict += classify_probe_diff(
            _iri_sets_from_probes(before.get("probes")), _iri_sets_from_probes(after.get("probes"))
        )
    if "harness" in before or "harness" in after:
        verdict += classify_probe_diff(
            _iri_sets_from_harness(before.get("harness")),
            _iri_sets_from_harness(after.get("harness")),
        )
    if "tests" in before or "tests" in after:
        verdict += classify_test_diff(
            _test_outcomes(before.get("tests")), _test_outcomes(after.get("tests"))
        )
    return verdict


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _cmd_snapshot(args: argparse.Namespace) -> int:  # pragma: no cover -- real-run only
    snapshots: list[ConsumerSnapshot] = []
    leak_surfaces: tuple[str, ...] = ()
    if args.gold:
        from .clusters import surface_strings
        from .splits import load_gold

        gold = load_gold(args.gold)
        leak_surfaces = surface_strings(gold)

    if args.consumer in ("mapper", "all"):
        spec = mapper_spec(args.mapper_root)
        if not spec.exists():
            print(f"SKIP folio-mapper: venv not found at {spec.venv_python}", file=sys.stderr)
        else:
            print(f"snapshotting {spec.name}…", file=sys.stderr)
            snap = snapshot_mapper(spec, test_timeout=args.test_timeout)
            snapshots.append(snap)
            path = write_row_snapshot(snap, args.row_report_dir)
            print(f"  row snapshot: {path}", file=sys.stderr)

    if args.consumer in ("enrich", "all"):
        spec = enrich_spec(args.enrich_root)
        if not spec.exists():
            print(f"SKIP folio-enrich: venv not found at {spec.venv_python}", file=sys.stderr)
        else:
            print(f"snapshotting {spec.name}…", file=sys.stderr)
            snap = snapshot_enrich(spec, test_timeout=args.test_timeout)
            snapshots.append(snap)
            path = write_row_snapshot(snap, args.row_report_dir)
            print(f"  row snapshot: {path}", file=sys.stderr)

    if not snapshots:
        print("no consumers snapshotted", file=sys.stderr)
        return 1

    aggregate = build_aggregate(snapshots, label=args.label)
    out_path = write_aggregate(aggregate, args.out, leak_surfaces=leak_surfaces)
    print(canonical_json(aggregate))
    print(f"aggregate (committed): {out_path}")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:  # pragma: no cover -- real-run only
    before = load_row_snapshot(args.before)
    after = load_row_snapshot(args.after)
    verdict = diff_snapshots(before, after)
    print(canonical_json(verdict.to_json()))
    return 1 if verdict.has_blocking else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.downstream",
        description="Downstream consumer validation: snapshot at baseline, diff at check-ins (KTD10).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser(
        "snapshot", help="run the consumers and write a baseline/check-in snapshot"
    )
    snap.add_argument("--consumer", choices=["mapper", "enrich", "all"], default="all")
    snap.add_argument("--mapper-root", type=Path, default=None)
    snap.add_argument("--enrich-root", type=Path, default=None)
    snap.add_argument("--label", default="baseline-v1")
    snap.add_argument("--out", type=Path, default=DEFAULT_SUMMARY_PATH)
    snap.add_argument("--row-report-dir", type=Path, default=DEFAULT_ROW_REPORT_DIR)
    snap.add_argument("--gold", type=Path, default=None, help="optional -- enables the leak scan")
    snap.add_argument("--test-timeout", type=float, default=DEFAULT_TEST_TIMEOUT_S)

    diff = sub.add_parser(
        "diff", help="classify blocking/advisory deltas between two row snapshots"
    )
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)

    comparison = sub.add_parser(
        "run_synthetic_comparison",
        help="compare local folio-resolve with pinned downstream incumbent lanes",
    )
    comparison.add_argument("--corpus-manifest", type=Path, required=True)
    comparison.add_argument("--config", type=Path, required=True)
    comparison.add_argument("--out", type=Path, required=True)
    comparison.add_argument("--items", type=Path, required=True)
    comparison.add_argument("--row-snapshot-dir", type=Path, required=True)
    comparison.add_argument("--leak-manifest", type=Path, required=True)
    comparison.add_argument("--salt-file", type=Path, required=True)
    comparison.add_argument("--mapper-root", type=Path, default=None)
    comparison.add_argument("--enrich-root", type=Path, default=None)
    comparison.add_argument("--consumer", choices=["mapper", "enrich", "all"], default="all")
    comparison.add_argument("--incumbent-version", default="0.4.0")
    comparison.add_argument("--limit", type=int, default=None)
    comparison.add_argument(
        "--scoreable-only",
        action="store_true",
        help=(
            "omit no-match rows; use with --limit 1 for the U10 one-item live gate "
            "(pilot/final runs retain no-match rows by default)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    parser = _parser()

    args = parser.parse_args(raw_argv)
    if args.command == "snapshot":
        return _cmd_snapshot(args)
    if args.command == "run_synthetic_comparison":
        from folio import FOLIO

        from folio_resolve.ontology import FolioPythonProvider

        from .answer_rule import load_config
        from .comparison import run_synthetic_comparison, write_comparison
        from .leakcheck import load_manifest
        from .synthesize import load_corpus
        from .synthetic_score import DocumentAdapter

        corpus = load_corpus(args.corpus_manifest)
        config = load_config(args.config)
        leak_manifest = load_manifest(args.leak_manifest)
        salt = args.salt_file.read_bytes()
        adapter = DocumentAdapter(FolioPythonProvider(_folio=FOLIO()))
        specs = []
        if args.consumer in {"mapper", "all"}:
            specs.append(mapper_spec(args.mapper_root))
        if args.consumer in {"enrich", "all"}:
            specs.append(enrich_spec(args.enrich_root))
        payload = run_synthetic_comparison(
            corpus,
            adapter=adapter,
            config=config,
            consumers=specs,
            items_path=args.items,
            row_snapshot_dir=args.row_snapshot_dir,
            leak_manifest=leak_manifest,
            salt=salt,
            limit=args.limit,
            include_nomatch=not args.scoreable_only,
            incumbent_version=args.incumbent_version,
            comparison_invocation=(
                "python",
                "eval/run_downstream.py",
                *raw_argv,
            ),
        )
        write_comparison(
            args.out,
            payload,
            leak_manifest=leak_manifest,
            salt=salt,
        )
        print(canonical_json(payload))
        return 0
    return _cmd_diff(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
