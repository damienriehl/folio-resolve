"""Versioned synthetic-corpus schema, builder, and verified loader (U5).

The scoreable synthetic lane deliberately reuses :class:`GoldItemRecord`.  No-match passages
remain a separate slice because an empty gold set is invalid input to ``score_items``.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast

from .answer_rule import AnswerRuleConfig
from .leakcheck import Manifest, scan_text
from .normalize import normalize_label
from .resolve_labels import LabelIndex, resolve_gold_value
from .splits import GoldItemRecord

Verification = Literal["deterministic", "human", "needs_review"]
SCOREABLE_VERIFICATIONS = frozenset({"deterministic", "human"})
CORPUS_KIND = "synthetic-corpus"


class SynthesisError(RuntimeError):
    """Raised when a corpus cannot be built or verified safely."""


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _jsonl(payloads: Iterable[Mapping[str, object]]) -> str:
    lines = [json.dumps(payload, ensure_ascii=False, sort_keys=True) for payload in payloads]
    return "\n".join(lines) + ("\n" if lines else "")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class SyntheticItem:
    """One generated passage and its audit-facing gold metadata."""

    item_id: str
    doc_type: str
    jurisdiction: str
    text: str
    gold_labels: tuple[str, ...] = ()
    gold_iris: frozenset[str] = frozenset()
    verification: Verification = "needs_review"
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verification not in {"deterministic", "human", "needs_review"}:
            raise ValueError(f"unsupported verification state: {self.verification!r}")

    @property
    def is_nomatch(self) -> bool:
        return self.provenance.get("no_match") is True

    @property
    def is_scoreable(self) -> bool:
        return self.verification in SCOREABLE_VERIFICATIONS and bool(self.gold_iris)

    def to_json(self) -> dict[str, object]:
        return {
            "doc_type": self.doc_type,
            "gold_iris": sorted(self.gold_iris),
            "gold_labels": list(self.gold_labels),
            "item_id": self.item_id,
            "jurisdiction": self.jurisdiction,
            "provenance": dict(self.provenance),
            "text": self.text,
            "verification": self.verification,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> SyntheticItem:
        labels = payload.get("gold_labels", [])
        iris = payload.get("gold_iris", [])
        provenance = payload.get("provenance", {})
        if (
            not isinstance(labels, list)
            or not isinstance(iris, list)
            or not isinstance(provenance, dict)
        ):
            raise SynthesisError(f"malformed synthetic row: {payload.get('item_id')!r}")
        return cls(
            item_id=str(payload["item_id"]),
            doc_type=str(payload["doc_type"]),
            jurisdiction=str(payload["jurisdiction"]),
            text=str(payload["text"]),
            gold_labels=tuple(str(label) for label in labels),
            gold_iris=frozenset(str(iri) for iri in iris),
            verification=cast(Verification, str(payload["verification"])),
            provenance=provenance,
        )


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    """The version pin and integrity metadata for one frozen corpus cohort."""

    version: int
    content_sha256: str
    nomatch_content_sha256: str
    ontology_cache_sha256: str
    answer_rule_config_sha256: str
    item_counts: Mapping[str, int]
    non_lexical_fraction: float
    non_lexical_floor: float
    scoreable: bool
    seed: int
    created: str
    manifest_path: Path = field(compare=False, repr=False)
    kind: str = CORPUS_KIND

    @property
    def corpus_path(self) -> Path:
        return self.manifest_path.with_name(f"corpus_v{self.version}.jsonl")

    @property
    def nomatch_path(self) -> Path:
        return self.manifest_path.with_name(f"nomatch_v{self.version}.jsonl")

    def to_json(self) -> dict[str, object]:
        return {
            "answer_rule_config_sha256": self.answer_rule_config_sha256,
            "content_sha256": self.content_sha256,
            "created": self.created,
            "item_counts": dict(sorted(self.item_counts.items())),
            "kind": self.kind,
            "nomatch_content_sha256": self.nomatch_content_sha256,
            "non_lexical_floor": self.non_lexical_floor,
            "non_lexical_fraction": self.non_lexical_fraction,
            "ontology_cache_sha256": self.ontology_cache_sha256,
            "scoreable": self.scoreable,
            "seed": self.seed,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class LoadedCorpus:
    """A hash-verified corpus with distinct scoring and no-match slice access."""

    manifest: CorpusManifest
    corpus_items: tuple[SyntheticItem, ...]
    nomatch_items: tuple[SyntheticItem, ...]

    @property
    def scoreable_items(self) -> tuple[SyntheticItem, ...]:
        return tuple(item for item in self.corpus_items if item.is_scoreable)

    @property
    def needs_review_items(self) -> tuple[SyntheticItem, ...]:
        return tuple(item for item in self.corpus_items if item.verification == "needs_review")

    def gold_item_records(self) -> tuple[GoldItemRecord, ...]:
        return tuple(to_gold_item_record(item) for item in self.scoreable_items)


def resolve_gold(items: Iterable[SyntheticItem], ontology: LabelIndex) -> list[SyntheticItem]:
    """Resolve labels independently, quarantining every ambiguous or unresolved item."""
    resolved_items: list[SyntheticItem] = []
    for item in items:
        iris: set[str] = set()
        labels_by_iri: dict[str, str] = {}
        issues: list[dict[str, object]] = []
        for label in item.gold_labels:
            resolution = resolve_gold_value(label, ontology)
            if resolution.ambiguous:
                issues.append(
                    {
                        "branch": resolution.branch,
                        "candidates": list(resolution.candidates),
                        "label": label,
                        "reason": "ambiguous",
                    }
                )
            elif not resolution.resolved:
                issues.append({"branch": resolution.branch, "label": label, "reason": "unresolved"})
            elif resolution.iri is not None:
                iris.add(resolution.iri)
                labels_by_iri.setdefault(
                    resolution.iri,
                    normalize_label(ontology.label_for(resolution.iri) or resolution.normalized),
                )
        provenance = dict(item.provenance)
        if issues:
            provenance["resolution_issues"] = issues
            resolved_items.append(
                replace(
                    item,
                    gold_iris=frozenset(),
                    verification="needs_review",
                    provenance=provenance,
                )
            )
        else:
            provenance["resolved_labels_by_iri"] = dict(sorted(labels_by_iri.items()))
            resolved_items.append(replace(item, gold_iris=frozenset(iris), provenance=provenance))
    return resolved_items


def to_gold_item_record(item: SyntheticItem) -> GoldItemRecord:
    """Map a scoreable synthetic row onto the scorer's complete record contract."""
    if not item.is_scoreable:
        raise SynthesisError(f"item is not scoreable: {item.item_id}")
    return GoldItemRecord(
        item_id=item.item_id,
        firm="synthetic",
        stratum=item.doc_type,
        stratum_id=item.doc_type,
        ancestor_path=(),
        leaf="",
        input_text=item.text,
        gold_iris=item.gold_iris,
        flags=frozenset(),
        blank=False,
        family_id=item.item_id,
    )


def _string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            yield from _string_values(nested)


def _check_leaks(items: Sequence[SyntheticItem], manifest: Manifest, salt: bytes) -> None:
    collisions = 0
    item_ids: list[str] = []
    for item in items:
        count = sum(scan_text(value, manifest, salt) for value in _string_values(item.to_json()))
        collisions += count
        if count:
            item_ids.append(item.item_id)
    if collisions:
        raise SynthesisError(
            f"leak check failed: collisions={collisions} item_ids={','.join(sorted(item_ids))}"
        )


def _non_lexical_fraction(items: Sequence[SyntheticItem]) -> float:
    total = sum(len(item.gold_iris) for item in items)
    if not total:
        return 0.0
    non_lexical = 0
    for item in items:
        passage = normalize_label(item.text)
        raw_mapping = item.provenance.get("resolved_labels_by_iri")
        labels_by_iri = (
            {str(iri): normalize_label(str(label)) for iri, label in raw_mapping.items()}
            if isinstance(raw_mapping, Mapping)
            else {}
        )
        if len(item.gold_iris) == 1 and len(item.gold_labels) == 1 and not labels_by_iri:
            labels_by_iri = {next(iter(item.gold_iris)): normalize_label(item.gold_labels[0])}
        missing = sorted(item.gold_iris - labels_by_iri.keys())
        if missing:
            raise SynthesisError(
                f"missing resolved label mapping: {item.item_id} iri_count={len(missing)}"
            )
        for iri in sorted(item.gold_iris):
            label = labels_by_iri[iri]
            if not label or label not in passage:
                non_lexical += 1
    return non_lexical / total


def build_corpus(
    items: Iterable[SyntheticItem],
    *,
    version: int,
    answer_rule_config: AnswerRuleConfig,
    leak_manifest: Manifest,
    salt: bytes,
    out_dir: Path,
    seed: int,
    ontology_cache_sha256: str,
    created: str,
    non_lexical_floor: float = 0.30,
) -> CorpusManifest:
    """Validate and atomically emit one immutable synthetic corpus version."""
    if version < 1:
        raise SynthesisError(f"version must be positive: {version}")
    if not 0.0 <= non_lexical_floor <= 1.0:
        raise SynthesisError(f"non_lexical_floor must be in [0, 1]: {non_lexical_floor}")

    all_items = list(items)
    seen: set[str] = set()
    for item in all_items:
        if item.item_id in seen:
            raise SynthesisError(f"duplicate item_id: {item.item_id}")
        seen.add(item.item_id)
        if item.is_nomatch:
            if item.gold_iris or item.gold_labels:
                raise SynthesisError(f"no-match row has gold values: {item.item_id}")
            continue
        if item.verification in SCOREABLE_VERIFICATIONS and not item.gold_iris:
            raise SynthesisError(f"scoreable row has empty gold_iris: {item.item_id}")
        if item.is_scoreable and len(item.gold_iris) > answer_rule_config.top_k:
            raise SynthesisError(
                "gold_iris exceeds top_k: "
                f"{item.item_id} count={len(item.gold_iris)} top_k={answer_rule_config.top_k}"
            )

    _check_leaks(all_items, leak_manifest, salt)
    ordered = sorted(all_items, key=lambda row: row.item_id)
    random.Random(seed).shuffle(ordered)
    corpus = [item for item in ordered if not item.is_nomatch]
    nomatch = [item for item in ordered if item.is_nomatch]
    scoreable_items = [item for item in corpus if item.is_scoreable]
    needs_review = [item for item in corpus if item.verification == "needs_review"]

    corpus_bytes = _jsonl(item.to_json() for item in corpus).encode("utf-8")
    nomatch_bytes = _jsonl(item.to_json() for item in nomatch).encode("utf-8")
    fraction = _non_lexical_fraction(scoreable_items)
    manifest_path = out_dir / f"corpus_v{version}.manifest.json"
    result = CorpusManifest(
        version=version,
        content_sha256=_sha256(corpus_bytes),
        nomatch_content_sha256=_sha256(nomatch_bytes),
        ontology_cache_sha256=ontology_cache_sha256,
        answer_rule_config_sha256=answer_rule_config.content_sha256(),
        item_counts={
            "needs_review": len(needs_review),
            "nomatch": len(nomatch),
            "scoreable": len(scoreable_items),
        },
        non_lexical_fraction=fraction,
        non_lexical_floor=non_lexical_floor,
        scoreable=fraction >= non_lexical_floor,
        seed=seed,
        created=created,
        manifest_path=manifest_path,
    )
    paths = (result.corpus_path, result.nomatch_path, result.manifest_path)
    existing = [path.name for path in paths if path.exists()]
    if existing:
        raise SynthesisError(f"corpus version already exists: {','.join(sorted(existing))}")
    _atomic_write(result.corpus_path, corpus_bytes)
    _atomic_write(result.nomatch_path, nomatch_bytes)
    _atomic_write(result.manifest_path, _canonical_json(result.to_json()).encode("utf-8"))
    return result


def _manifest_from_json(payload: object, path: Path) -> CorpusManifest:
    if not isinstance(payload, dict):
        raise SynthesisError(f"corpus manifest is not an object: {path}")
    counts = payload.get("item_counts")
    if not isinstance(counts, dict):
        raise SynthesisError(f"corpus manifest item_counts are malformed: {path}")
    try:
        manifest = CorpusManifest(
            kind=str(payload["kind"]),
            version=int(payload["version"]),
            content_sha256=str(payload["content_sha256"]),
            nomatch_content_sha256=str(payload["nomatch_content_sha256"]),
            ontology_cache_sha256=str(payload["ontology_cache_sha256"]),
            answer_rule_config_sha256=str(payload["answer_rule_config_sha256"]),
            item_counts={str(key): int(value) for key, value in counts.items()},
            non_lexical_fraction=float(payload["non_lexical_fraction"]),
            non_lexical_floor=float(payload["non_lexical_floor"]),
            scoreable=bool(payload["scoreable"]),
            seed=int(payload["seed"]),
            created=str(payload["created"]),
            manifest_path=path,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SynthesisError(f"corpus manifest fields are malformed: {path}") from exc
    if manifest.kind != CORPUS_KIND:
        raise SynthesisError(f"unsupported corpus manifest kind: {manifest.kind}")
    return manifest


def _load_rows(path: Path) -> tuple[SyntheticItem, ...]:
    rows: list[SyntheticItem] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SynthesisError(f"cannot read corpus slice: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SynthesisError(f"malformed JSONL: {path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise SynthesisError(f"JSONL line is not an object: {path}:{line_number}")
        try:
            rows.append(SyntheticItem.from_json(payload))
        except (KeyError, TypeError, ValueError) as exc:
            raise SynthesisError(f"malformed synthetic row: {path}:{line_number}") from exc
    return tuple(rows)


def load_corpus(manifest_path: Path) -> LoadedCorpus:
    """Load both slices only after verifying their manifest-pinned byte hashes."""
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SynthesisError(f"cannot decode corpus manifest: {manifest_path}") from exc
    manifest = _manifest_from_json(manifest_payload, manifest_path)
    for path, expected, name in (
        (manifest.corpus_path, manifest.content_sha256, "content_sha256"),
        (manifest.nomatch_path, manifest.nomatch_content_sha256, "nomatch_content_sha256"),
    ):
        actual = _sha256(path.read_bytes())
        if actual != expected:
            raise SynthesisError(
                f"corpus verification failed: check={name} expected={expected} actual={actual}"
            )
    corpus_items = _load_rows(manifest.corpus_path)
    nomatch_items = _load_rows(manifest.nomatch_path)
    all_items = (*corpus_items, *nomatch_items)
    ids = [item.item_id for item in all_items]
    if len(ids) != len(set(ids)):
        raise SynthesisError("corpus verification failed: duplicate item_id")
    if any(item.is_nomatch for item in corpus_items):
        raise SynthesisError("corpus verification failed: no-match row in corpus slice")
    if any(not item.is_nomatch or item.gold_iris or item.gold_labels for item in nomatch_items):
        raise SynthesisError("corpus verification failed: invalid no-match slice row")
    if any(
        item.verification in SCOREABLE_VERIFICATIONS and not item.gold_iris for item in corpus_items
    ):
        raise SynthesisError("corpus verification failed: scoreable row has empty gold_iris")
    actual_counts = {
        "needs_review": sum(item.verification == "needs_review" for item in corpus_items),
        "nomatch": len(nomatch_items),
        "scoreable": sum(item.is_scoreable for item in corpus_items),
    }
    if dict(manifest.item_counts) != actual_counts:
        raise SynthesisError(
            f"corpus verification failed: item_counts expected={dict(manifest.item_counts)} "
            f"actual={actual_counts}"
        )
    return LoadedCorpus(manifest=manifest, corpus_items=corpus_items, nomatch_items=nomatch_items)


def extend_corpus(
    prev_manifest: Path,
    ratified_items: Iterable[SyntheticItem],
    *,
    answer_rule_config: AnswerRuleConfig,
    leak_manifest: Manifest,
    salt: bytes,
    out_dir: Path,
    seed: int,
    ontology_cache_sha256: str,
    created: str,
    non_lexical_floor: float = 0.30,
) -> CorpusManifest:
    """Build N+1 from a verified N cohort without rewriting any N artifact."""
    previous = load_corpus(prev_manifest)
    if ontology_cache_sha256 != previous.manifest.ontology_cache_sha256:
        raise SynthesisError(
            "cannot extend corpus across ontology pins: "
            f"previous={previous.manifest.ontology_cache_sha256} "
            f"requested={ontology_cache_sha256}"
        )
    additions = list(ratified_items)
    if any(item.verification == "needs_review" for item in additions):
        raise SynthesisError("ratified additions must not be needs_review")
    return build_corpus(
        [*previous.corpus_items, *previous.nomatch_items, *additions],
        version=previous.manifest.version + 1,
        answer_rule_config=answer_rule_config,
        leak_manifest=leak_manifest,
        salt=salt,
        out_dir=out_dir,
        seed=seed,
        ontology_cache_sha256=ontology_cache_sha256,
        created=created,
        non_lexical_floor=non_lexical_floor,
    )
