from __future__ import annotations

import importlib
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from folio_resolve import Concept, FolioPythonProvider, InMemoryOntology, OntologyProvider

UAT_ROOT = Path(__file__).resolve().parent
PROTECTED_ROOTS = ("eval/data/", "eval/reports/")
_REAL_ONTOLOGY_SKIP_REASON = "requires the folio extra and FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1"

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


@pytest.fixture
def real_ontology() -> Iterator[OntologyProvider]:
    if os.environ.get("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY") != "1":
        pytest.skip(_REAL_ONTOLOGY_SKIP_REASON)
    try:
        importlib.import_module("folio")
    except (ImportError, OSError):
        pytest.skip(_REAL_ONTOLOGY_SKIP_REASON)
    yield FolioPythonProvider()


def blocked_optional_imports(snippet: str) -> subprocess.CompletedProcess[str]:
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
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
