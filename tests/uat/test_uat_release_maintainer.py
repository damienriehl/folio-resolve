from __future__ import annotations

import importlib.metadata
from pathlib import Path

from folio_resolve import __version__

from .conftest import audited_python_process, blocked_optional_imports


def test_us_rm_01_core_import_and_quick_start_without_extras(tmp_path: Path) -> None:
    """US-RM-01 imports the public core and runs the README quick start without extras."""
    completed = blocked_optional_imports(
        """
import json
from folio_resolve import InMemoryOntology, Concept, MatchPipeline, DomainPrior

ontology = InMemoryOntology([
    Concept(iri="R-defenses", label="Litigation Defenses", branch="Objectives"),
    Concept(iri="R-arb", label="Arbitration Rules", branch="Service"),
])
pipe = MatchPipeline(ontology=ontology)
matches = pipe.match("rules of arbitration")
prior = DomainPrior.from_manifest_subjects("treatise", [("R-lit", "Litigation")])
pipe.match("Defenses", domain_prior=prior)
print(json.dumps({"label": matches[0].label, "version": folio_resolve.__version__}, sort_keys=True))
""",
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"label": "Arbitration Rules"' in completed.stdout
    assert f'"version": "{__version__}"' in completed.stdout


def test_us_rm_02_version_and_extras_are_independent(
    extras_present: dict[str, bool],
) -> None:
    """US-RM-02 reports the installed version and each declared optional extra independently."""
    distribution = importlib.metadata.metadata("folio-resolve")
    declared_extras = set(distribution.get_all("Provides-Extra") or ())

    assert importlib.metadata.version("folio-resolve") == __version__ == "0.4.0"
    assert {"folio", "spacy", "embedding"} <= declared_extras
    assert set(extras_present) == {"folio", "spacy", "embedding"}
    assert all(isinstance(available, bool) for available in extras_present.values())


def _run_determinism_probe(seed: str, tmp_path: Path) -> str:
    source = """
import json
from folio_resolve import Concept, InMemoryOntology, MatchPipeline, generate_search_terms

ontology = InMemoryOntology([
    Concept(iri="R-003", label="Arbitration Rules C", branch="Service"),
    Concept(iri="R-001", label="Arbitration Rules A", branch="Service"),
    Concept(iri="R-002", label="Arbitration Rules B", branch="Service"),
])
pipeline = MatchPipeline(ontology=ontology)
payload = {
    "matches": [
        (candidate.iri, candidate.label, candidate.score)
        for candidate in pipeline.match("Arbitration Rules")
    ],
    "terms": generate_search_terms("litigation"),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
"""
    child = audited_python_process(
        tmp_path,
        f"release-determinism-{seed}",
        source,
        extra_env={"PYTHONHASHSEED": seed},
    )
    completed = child.run()
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_us_rm_03_public_output_is_hash_seed_deterministic(tmp_path: Path) -> None:
    """US-RM-03 returns byte-identical, stably ordered public output across hash seeds."""
    seed_zero = _run_determinism_probe("0", tmp_path)
    seed_one = _run_determinism_probe("1", tmp_path)

    assert seed_zero == seed_one
    assert seed_zero.index("R-001") < seed_zero.index("R-002") < seed_zero.index("R-003")
