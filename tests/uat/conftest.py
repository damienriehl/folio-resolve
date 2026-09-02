from __future__ import annotations

import builtins
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import sysconfig
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from folio_resolve import (
    Concept,
    FolioPythonProvider,
    InMemoryOntology,
    LabelResolver,
    MatchPipeline,
    OntologyProvider,
)

UAT_ROOT = Path(__file__).resolve().parent


def _repository_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=UAT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path(__file__).resolve().parents[2]


_REPO_ROOT = _repository_root()
PROTECTED_ROOTS = ("eval/data/", "eval/reports/")
_REAL_ONTOLOGY_SKIP_REASON = "requires the folio extra and FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1"
_AUDIT_ALLOWED_ROOTS_ENV = "FOLIO_RESOLVE_UAT_AUDIT_ALLOWED_ROOTS"
_AUDIT_PROTECTED_ROOTS_ENV = "FOLIO_RESOLVE_UAT_AUDIT_PROTECTED_ROOTS"

_STORY_NAME = re.compile(r"(?:test_)?us_([a-z]{2})_(\d{2})(?:_|$)", re.IGNORECASE)
_STORY_TEXT = re.compile(r"\bUS-([A-Z]{2})-(\d{2})\b", re.IGNORECASE)
_EXTRA_MODULES = {
    "folio": ("folio",),
    "spacy": ("spacy",),
    "embedding": ("faiss", "sentence_transformers", "numpy"),
}


def _story_id(item: pytest.Item) -> str | None:
    if match := _STORY_NAME.search(item.name):
        return f"US-{match.group(1).upper()}-{match.group(2)}"
    function = getattr(item, "function", None)
    docstring = getattr(function, "__doc__", None)
    if isinstance(docstring, str) and (match := _STORY_TEXT.search(docstring)):
        return f"US-{match.group(1).upper()}-{match.group(2)}"
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if not Path(str(item.path)).resolve().is_relative_to(UAT_ROOT):
            continue
        item.add_marker(pytest.mark.uat)
        if story_id := _story_id(item):
            item.user_properties.append(("story_id", story_id))


def _commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


@pytest.fixture(scope="session", autouse=True)
def record_uat_testsuite_metadata(
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    extras = detect_extras()
    properties = {
        "python_version": sys.version.split()[0],
        "extras_folio": str(extras["folio"]).lower(),
        "extras_spacy": str(extras["spacy"]).lower(),
        "extras_embedding": str(extras["embedding"]).lower(),
        "real_ontology_enabled": str(
            os.environ.get("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY") == "1"
        ).lower(),
        "commit": _commit(),
    }
    for name, value in properties.items():
        record_testsuite_property(name, value)


@pytest.fixture
def readme_ontology(ontology: InMemoryOntology) -> InMemoryOntology:
    concepts_by_iri: dict[str, Concept] = {}
    for label_info in ontology.all_labels().values():
        concepts_by_iri.setdefault(label_info.concept.iri, label_info.concept)

    readme_concepts = [
        Concept(iri="R-delaware", label="Delaware", branch="Location"),
        Concept(iri="R-antitrust-law", label="Antitrust Law", branch="Area of Law"),
        Concept(iri="R-securities-law", label="Securities Law", branch="Area of Law"),
        Concept(
            iri="R-habitability",
            label="Breach of Warranty of Habitability",
            branch="Objectives",
        ),
        Concept(iri="R-litigation", label="Litigation", branch="Area of Law"),
        Concept(iri="R-personal-injury", label="Personal Injury", branch="Area of Law"),
        Concept(iri="R-deposition", label="Deposition", branch="Document Artifacts"),
        Concept(iri="R-agreements", label="Agreements", branch="Document Artifacts"),
    ]
    for concept in readme_concepts:
        concepts_by_iri.setdefault(concept.iri, concept)
    return InMemoryOntology(list(concepts_by_iri.values()))


@pytest.fixture
def quick_start_pipeline() -> MatchPipeline:
    ontology = InMemoryOntology(
        [
            Concept(iri="R-defenses", label="Litigation Defenses", branch="Objectives"),
            Concept(iri="R-arb", label="Arbitration Rules", branch="Service"),
        ]
    )
    return MatchPipeline(ontology=ontology)


@pytest.fixture
def law_delaware_resolver(readme_ontology: InMemoryOntology) -> LabelResolver:
    delaware = readme_ontology.get_concept("R-delaware")
    assert delaware is not None

    def search(label: str) -> list[tuple[object, float]]:
        if label == "law":
            return [(delaware, 90.0)]
        return list(readme_ontology.search_by_label(label))

    return LabelResolver(search_by_label=search)


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def detect_extras() -> dict[str, bool]:
    return {
        extra: all(_module_available(module) for module in modules)
        for extra, modules in _EXTRA_MODULES.items()
    }


@pytest.fixture(scope="session")
def extras_present() -> dict[str, bool]:
    return detect_extras()


def load_real_ontology() -> OntologyProvider:
    if importlib.util.find_spec("folio") is None:
        pytest.skip(_REAL_ONTOLOGY_SKIP_REASON)
    if os.environ.get("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY") != "1":
        pytest.skip(_REAL_ONTOLOGY_SKIP_REASON)
    importlib.import_module("folio")
    return FolioPythonProvider()


def real_ontology_audit_roots() -> tuple[Path, ...]:
    if os.environ.get("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY") != "1":
        return ()
    if not _module_available("folio"):
        return ()

    folio_graph = importlib.import_module("folio.graph")
    folio_config = importlib.import_module("folio.config")
    return (
        Path(folio_graph.DEFAULT_CACHE_DIR).expanduser().resolve().parent,
        Path(folio_config.DEFAULT_CONFIG_PATH).expanduser().resolve().parent,
    )


def _interpreter_audit_roots() -> tuple[Path, ...]:
    install_paths = sysconfig.get_paths()
    return tuple(
        Path(path).expanduser().resolve()
        for path in (
            sys.prefix,
            sys.base_prefix,
            install_paths["purelib"],
            install_paths["platlib"],
            install_paths["stdlib"],
        )
    )


def _resolved_audit_roots(allowed_roots: Iterable[Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for root in (
        *allowed_roots,
        *_interpreter_audit_roots(),
        _REPO_ROOT,
        *real_ontology_audit_roots(),
    ):
        path = root.expanduser().resolve()
        if path not in resolved:
            resolved.append(path)
    return tuple(resolved)


@pytest.fixture(scope="session")
def real_ontology() -> OntologyProvider:
    return load_real_ontology()


def _audit_categories(
    paths: Iterable[Path],
    *,
    tmp_path: Path,
    home_root: Path,
    allowed_roots: Iterable[Path],
) -> Counter[str]:
    tmp_root = tmp_path.resolve()
    home_root = home_root.resolve()
    allowed = tuple(path.resolve() for path in allowed_roots)
    protected = tuple((_REPO_ROOT / path.rstrip("/")).resolve() for path in PROTECTED_ROOTS)
    counts: Counter[str] = Counter()
    for observed in paths:
        path = observed.resolve()
        if any(path.is_relative_to(root) for root in protected):
            counts["eval-data"] += 1
        elif path.is_relative_to(tmp_root) or any(path.is_relative_to(root) for root in allowed):
            continue
        elif path.is_relative_to(home_root):
            counts["home"] += 1
        else:
            counts["other-outside-tmp"] += 1
    return counts


def assert_public_path_audit(
    paths: Iterable[Path],
    *,
    tmp_path: Path,
    home_root: Path,
    allowed_roots: Iterable[Path] = (),
) -> None:
    counts = _audit_categories(
        paths,
        tmp_path=tmp_path,
        home_root=home_root,
        allowed_roots=_resolved_audit_roots(allowed_roots),
    )
    assert not counts, _audit_failure_message(counts)


def _audit_failure_message(counts: Mapping[str, int]) -> str:
    return "path audit failed: " + ", ".join(
        f"{category}={counts[category]}" for category in ("home", "eval-data", "other-outside-tmp")
    )


@contextmanager
def audit_open_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    allowed_roots: Iterable[Path] = (),
) -> Iterator[list[Path]]:
    opened: list[Path] = []
    home_root = Path.home().resolve()
    original_open = cast(Callable[..., Any], builtins.open)
    original_path_open = cast(Callable[..., Any], Path.open)

    def record(file: object) -> None:
        if isinstance(file, (str, os.PathLike)):
            opened.append(Path(file).resolve())

    def audited_open(file: object, *args: object, **kwargs: object) -> Any:
        record(file)
        return original_open(file, *args, **kwargs)

    def audited_path_open(path: Path, *args: object, **kwargs: object) -> Any:
        record(path)
        return original_path_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", audited_open)
        patch.setattr(Path, "open", audited_path_open)
        yield opened

    assert_public_path_audit(
        opened,
        tmp_path=tmp_path,
        home_root=home_root,
        allowed_roots=allowed_roots,
    )


@dataclass(frozen=True)
class AuditedPythonProcess:
    _command: tuple[str, ...]
    _env: Mapping[str, str]
    audit_log: Path
    tmp_path: Path
    home_root: Path
    allowed_roots: tuple[Path, ...]

    def run(self, *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                self._command,
                capture_output=True,
                text=True,
                env=self._env,
                timeout=timeout,
                check=False,
            )
        finally:
            self.assert_safe()

    def assert_safe(self) -> None:
        installed = False
        observed: list[Path] = []
        malformed = 0
        for line in self.audit_log.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                malformed += 1
                continue
            if payload == {"installed": True}:
                installed = True
            elif isinstance(payload, dict) and isinstance(payload.get("path"), str):
                observed.append(Path(payload["path"]))
            else:
                malformed += 1
        integrity_failures = malformed + int(not installed)
        assert integrity_failures == 0, _audit_failure_message(
            {"home": 0, "eval-data": 0, "other-outside-tmp": integrity_failures}
        )
        assert_public_path_audit(
            observed,
            tmp_path=self.tmp_path,
            home_root=self.home_root,
            allowed_roots=self.allowed_roots,
        )


def audited_python_process(
    tmp_path: Path,
    label: str,
    source: str,
    *,
    allowed_roots: Iterable[Path] = (),
    extra_env: Mapping[str, str] | None = None,
) -> AuditedPythonProcess:
    resolved_allowed_roots = _resolved_audit_roots(allowed_roots)
    process_root = tmp_path / f"child-{label}"
    process_root.mkdir()
    audit_module_root = process_root / "audit-module"
    audit_module_root.mkdir()
    child_home = process_root / "home"
    child_home.mkdir()
    audit_log = process_root / "opened-paths.jsonl"
    audit_log.write_text("", encoding="utf-8")
    sitecustomize = audit_module_root / "sitecustomize.py"
    sitecustomize.write_text(
        """from __future__ import annotations

import builtins
import json
import os
import sys
import sysconfig
from pathlib import Path

_AUDIT_LOG = Path(os.environ["FOLIO_RESOLVE_UAT_AUDIT_LOG"]).resolve()
_AUDIT_ALLOWED_ROOTS = tuple(
    Path(path).resolve()
    for path in (
        *json.loads(os.environ["FOLIO_RESOLVE_UAT_AUDIT_ALLOWED_ROOTS"]),
        sys.prefix,
        sys.base_prefix,
        sysconfig.get_paths()["purelib"],
        sysconfig.get_paths()["platlib"],
        sysconfig.get_paths()["stdlib"],
    )
)
_AUDIT_PROTECTED_ROOTS = tuple(
    Path(path).resolve()
    for path in json.loads(os.environ["FOLIO_RESOLVE_UAT_AUDIT_PROTECTED_ROOTS"])
)
_ORIGINAL_OPEN = builtins.open
_ORIGINAL_PATH_OPEN = Path.open


def _write(payload: dict[str, object]) -> None:
    with _ORIGINAL_OPEN(_AUDIT_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\\n")


def _record(file: object) -> None:
    if isinstance(file, (str, os.PathLike)):
        path = Path(file).resolve()
        protected = any(path.is_relative_to(root) for root in _AUDIT_PROTECTED_ROOTS)
        allowed = any(path.is_relative_to(root) for root in _AUDIT_ALLOWED_ROOTS)
        if path != _AUDIT_LOG and (protected or not allowed):
            _write({"path": str(path)})


def _audited_open(file: object, *args: object, **kwargs: object) -> object:
    _record(file)
    return _ORIGINAL_OPEN(file, *args, **kwargs)


def _audited_path_open(path: Path, *args: object, **kwargs: object) -> object:
    _record(path)
    return _ORIGINAL_PATH_OPEN(path, *args, **kwargs)


builtins.open = _audited_open
Path.open = _audited_path_open
_write({"installed": True})
""",
        encoding="utf-8",
    )
    runner = process_root / "runner.py"
    runner.write_text(
        f"""from __future__ import annotations

import importlib.util

spec = importlib.util.spec_from_file_location("sitecustomize", {str(sitecustomize)!r})
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
exec(compile({source!r}, "<uat-child>", "exec"))
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(child_home),
            "PYTHONPATH": str(audit_module_root),
            "FOLIO_RESOLVE_UAT_AUDIT_LOG": str(audit_log),
            _AUDIT_ALLOWED_ROOTS_ENV: json.dumps([str(path) for path in resolved_allowed_roots]),
            _AUDIT_PROTECTED_ROOTS_ENV: json.dumps(
                [str((_REPO_ROOT / path.rstrip("/")).resolve()) for path in PROTECTED_ROOTS]
            ),
        }
    )
    if extra_env is not None:
        reserved = {
            "HOME",
            "PYTHONPATH",
            "FOLIO_RESOLVE_UAT_AUDIT_LOG",
            _AUDIT_ALLOWED_ROOTS_ENV,
            _AUDIT_PROTECTED_ROOTS_ENV,
        } & extra_env.keys()
        if reserved:
            raise ValueError(f"reserved audit environment overrides={len(reserved)}")
        env.update(extra_env)
    return AuditedPythonProcess(
        _command=(sys.executable, "-I", str(runner)),
        _env=env,
        audit_log=audit_log,
        tmp_path=tmp_path,
        home_root=Path.home().resolve(),
        allowed_roots=resolved_allowed_roots,
    )


def blocked_optional_imports(snippet: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    blocked_modules = sorted(module for modules in _EXTRA_MODULES.values() for module in modules)
    source = f"""
import importlib.abc
import sys

BLOCKED = frozenset({blocked_modules!r})

class BlockedOptionalFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in BLOCKED:
            raise ImportError(f"optional dependency blocked: {{fullname}}")
        return None

sys.meta_path.insert(0, BlockedOptionalFinder())
import folio_resolve
exec(compile({snippet!r}, "<uat-snippet>", "exec"))
"""
    child = audited_python_process(
        tmp_path,
        "blocked-optional",
        source,
        extra_env={"PYTHONHASHSEED": "0"},
    )
    return child.run()
