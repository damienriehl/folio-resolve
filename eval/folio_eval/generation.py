"""Label-blind generation quotas, prompt guard, and audit evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .synthesize import SyntheticItem

# Public top-level vocabulary used by the resolver. Keeping this host-side means callers cannot
# weaken the guard by omitting branch targets from an assignment.
PUBLIC_BRANCH_NAMES = frozenset(
    {
        "Actor / Player",
        "Area of Law",
        "Asset Type",
        "Document Artifacts",
        "Events",
        "Industry and Market",
        "Location",
        "Objectives",
        "Service",
    }
)
LEAKAGE_MARKERS = frozenset({"folio", "iri", "http://", "https://"})


@dataclass(frozen=True, slots=True)
class DocTypeQuota:
    """Targets for one generator-visible document assignment family."""

    doc_type: str
    jurisdictions: tuple[str, ...]
    scoreable: int
    no_match: int

    def to_json(self) -> dict[str, object]:
        return {
            "doc_type": self.doc_type,
            "jurisdictions": list(self.jurisdictions),
            "scoreable": self.scoreable,
            "no_match": self.no_match,
        }


@dataclass(frozen=True, slots=True)
class QuotaTable:
    """Host-side corpus targets; the complete table is never a worker input."""

    version: int
    doc_types: tuple[DocTypeQuota, ...]
    branch_targets: Mapping[str, int]

    @property
    def scoreable_target(self) -> int:
        return sum(quota.scoreable for quota in self.doc_types)

    @property
    def no_match_target(self) -> int:
        return sum(quota.no_match for quota in self.doc_types)

    def to_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "doc_types": [quota.to_json() for quota in self.doc_types],
            "branch_targets": dict(sorted(self.branch_targets.items())),
        }


@dataclass(frozen=True, slots=True)
class DocTypeFill:
    scoreable_count: int
    scoreable_target: int
    scoreable_fraction: float
    no_match_count: int
    no_match_target: int
    no_match_fraction: float


@dataclass(frozen=True, slots=True)
class FillReport:
    """Document and post-grading branch quota fill fractions.

    Before grading, only document-type fill is knowable. Branch counts require gold IRIs plus the
    grader-produced ``provenance.branches_by_iri`` mapping; absent mappings contribute no branch
    attribution rather than guessing from text or labels.
    """

    doc_types: Mapping[str, DocTypeFill]
    branches: Mapping[str, float]


def _positive_or_zero(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def load_quotas(path: Path) -> QuotaTable:
    """Load and validate a versioned quota table."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("quota table must be a version-1 object")
    raw_doc_types = payload.get("doc_types")
    raw_branches = payload.get("branch_targets")
    if not isinstance(raw_doc_types, list) or not isinstance(raw_branches, dict):
        raise ValueError("quota table requires doc_types and branch_targets")
    doc_types: list[DocTypeQuota] = []
    seen: set[str] = set()
    for raw in raw_doc_types:
        if not isinstance(raw, dict) or not isinstance(raw.get("jurisdictions"), list):
            raise ValueError("malformed document quota")
        name = str(raw.get("doc_type", "")).strip()
        jurisdictions = tuple(str(value).strip() for value in raw["jurisdictions"])
        if not name or name in seen or not jurisdictions or any(not value for value in jurisdictions):
            raise ValueError(f"invalid or duplicate document quota: {name!r}")
        seen.add(name)
        doc_types.append(
            DocTypeQuota(
                doc_type=name,
                jurisdictions=jurisdictions,
                scoreable=_positive_or_zero(raw.get("scoreable"), "scoreable"),
                no_match=_positive_or_zero(raw.get("no_match"), "no_match"),
            )
        )
    branches = {
        str(name): _positive_or_zero(target, f"branch target {name!r}")
        for name, target in raw_branches.items()
    }
    if not branches or any(name not in PUBLIC_BRANCH_NAMES for name in branches):
        raise ValueError("branch_targets contains an unknown public branch name")
    return QuotaTable(version=1, doc_types=tuple(doc_types), branch_targets=branches)


def _fraction(count: int, target: int) -> float:
    return count / target if target else (1.0 if count == 0 else float("inf"))


def fill_report(quotas: QuotaTable, items: Iterable[SyntheticItem]) -> FillReport:
    """Measure quota fill; branch attribution is valid only after independent grading."""
    rows = tuple(items)
    doc_fills: dict[str, DocTypeFill] = {}
    for quota in quotas.doc_types:
        matching = tuple(item for item in rows if item.doc_type == quota.doc_type)
        scoreable = sum(item.is_scoreable for item in matching)
        no_match = sum(item.is_nomatch for item in matching)
        doc_fills[quota.doc_type] = DocTypeFill(
            scoreable,
            quota.scoreable,
            _fraction(scoreable, quota.scoreable),
            no_match,
            quota.no_match,
            _fraction(no_match, quota.no_match),
        )
    branch_counts = dict.fromkeys(quotas.branch_targets, 0)
    for item in rows:
        mapping = item.provenance.get("branches_by_iri")
        if not isinstance(mapping, dict):
            continue
        for iri in item.gold_iris:
            branch = mapping.get(iri)
            if isinstance(branch, str) and branch in branch_counts:
                branch_counts[branch] += 1
    return FillReport(
        doc_types=doc_fills,
        branches={
            branch: _fraction(branch_counts[branch], target)
            for branch, target in quotas.branch_targets.items()
        },
    )


def input_manifest(paths: Iterable[Path]) -> dict[str, object]:
    """Return deterministic SHA-256 evidence for every file made visible to a worker."""
    resolved = sorted({path.resolve() for path in paths}, key=str)
    files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in resolved}
    return {"algorithm": "sha256", "files": files}


def render_prompt(template: str, assignment: Mapping[str, object]) -> str:
    """Render an assignment and refuse any host vocabulary or link-like leakage marker."""
    required = ("doc_type", "jurisdiction", "scenario", "length", "register")
    try:
        rendered = template.format(**{name: assignment[name] for name in required})
    except KeyError as exc:
        raise ValueError(f"missing prompt assignment field: {exc.args[0]}") from exc
    folded = rendered.casefold()
    markers = LEAKAGE_MARKERS | PUBLIC_BRANCH_NAMES
    found = sorted(marker for marker in markers if marker.casefold() in folded)
    if found:
        raise ValueError(f"rendered prompt contains leakage marker: {found[0]!r}")
    return rendered
