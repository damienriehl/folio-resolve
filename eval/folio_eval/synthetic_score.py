"""Document-level adapter and scoring runner for the frozen synthetic lane (U8)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from folio_resolve.blocklist import AliasBlocklist, load_seed_blocklist
from folio_resolve.gates import PlaceNameGate, ShortLabelGate
from folio_resolve.matching.aho_corasick import AhoCorasickMatcher
from folio_resolve.ontology import FolioPythonProvider, RecallOntology
from folio_resolve.pipeline import MatchCandidate
from folio_resolve.recall import MultiStrategyRecall
from folio_resolve.scoring import content_words

from .answer_rule import (
    AnswerRuleConfig,
    CandidateLike,
    commit_from_ranked,
    load_config,
    rank_candidates,
)
from .leakcheck import Manifest, load_manifest, scan_text
from .score import EMPTY_HIERARCHY, MicroCounts, ScoreRun, score_items
from .selftest import assert_ontology_pin, ensure_hash_seed, run_determinism_selftest
from .splits import GoldItemRecord
from .synthesize import LoadedCorpus, load_corpus

PhraseExtractor = Callable[[str], Sequence[str]]
SUPPRESSION_CATEGORIES = ("blocklist", "place_gate", "short_label_gate", "score_floor")
REPORT_KIND = "synthetic_baseline"
PUBLIC_METADATA_KIND = "synthetic-report-public-metadata"
PUBLIC_METADATA_VERSION = 1
DEPTH_PROBE_MAX = 200
DEPTH_PROBE_DEPTHS = (10, 50, DEPTH_PROBE_MAX)
DEFAULT_PUBLIC_METADATA_PATH = (
    Path(__file__).resolve().parents[1] / "synthetic" / "public_report_metadata_v1.json"
)
PUBLIC_METADATA_PATHS = frozenset(
    {
        ("kind",),
        ("label",),
        ("answer_rule_config", "rationale"),
        ("determinism_selftest", "target"),
    }
)


class SyntheticScoringError(RuntimeError):
    """Raised when a synthetic scoring contract or publication gate fails."""


@dataclass(frozen=True, slots=True)
class PublicReportMetadata:
    """Versioned, path-bound public strings that the leak gate may exempt."""

    source_path: Path
    version: int
    answer_rule_config_sha256: str
    fields: Mapping[tuple[str, ...], str]


def load_public_report_metadata(path: Path) -> PublicReportMetadata:
    """Load the independently reviewed public-string publication contract."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("kind") != PUBLIC_METADATA_KIND:
        raise SyntheticScoringError(f"invalid public metadata contract: {path}")
    if payload.get("version") != PUBLIC_METADATA_VERSION:
        raise SyntheticScoringError(
            f"unsupported public metadata version: {payload.get('version')!r}"
        )
    config_sha = payload.get("answer_rule_config_sha256")
    if not isinstance(config_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", config_sha):
        raise SyntheticScoringError("public metadata config hash must be lowercase SHA-256")
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise SyntheticScoringError("public metadata fields must be a list")
    fields: dict[tuple[str, ...], str] = {}
    for entry in raw_fields:
        if not isinstance(entry, dict):
            raise SyntheticScoringError("public metadata field must be an object")
        raw_path = entry.get("path")
        value = entry.get("value")
        if (
            not isinstance(raw_path, list)
            or not raw_path
            or not all(isinstance(part, str) and part for part in raw_path)
            or not isinstance(value, str)
        ):
            raise SyntheticScoringError("malformed public metadata field")
        field_path = tuple(raw_path)
        if field_path in fields:
            raise SyntheticScoringError(f"duplicate public metadata path: {field_path!r}")
        fields[field_path] = value
    if frozenset(fields) != PUBLIC_METADATA_PATHS:
        raise SyntheticScoringError("public metadata paths do not match the v1 contract")
    return PublicReportMetadata(
        source_path=path,
        version=PUBLIC_METADATA_VERSION,
        answer_rule_config_sha256=config_sha,
        fields=MappingProxyType(fields),
    )


def nounish_ngrams(text: str, *, max_tokens: int = 5) -> tuple[str, ...]:
    """Return deterministic content-bearing token windows.

    This intentionally small heuristic is an injection seam, not a claim that noun phrase
    extraction is settled. Campaign measurements can replace it without changing scoring.
    """
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    phrases: set[str] = set()
    for width in range(1, min(max_tokens, len(tokens)) + 1):
        for start in range(len(tokens) - width + 1):
            phrase = " ".join(tokens[start : start + width])
            if content_words(phrase):
                phrases.add(phrase)
    return tuple(sorted(phrases, key=lambda value: (len(value.split()), value.casefold(), value)))


@dataclass(frozen=True, slots=True)
class AdapterResult:
    candidates: tuple[MatchCandidate, ...]
    raw_candidate_count: int
    suppression_counters: Mapping[str, int]


@dataclass
class DocumentAdapter:
    """Map a passage to ranked ontology candidates using exact sweep plus broad recall."""

    ontology: RecallOntology
    phrase_extractor: PhraseExtractor = nounish_ngrams
    blocklist: AliasBlocklist = field(default_factory=load_seed_blocklist)
    place_gate: PlaceNameGate = field(default_factory=PlaceNameGate)
    short_gate: ShortLabelGate = field(default_factory=ShortLabelGate)
    score_floor: float = 45.0
    recall_top_n: int = DEPTH_PROBE_MAX
    _matcher: AhoCorasickMatcher = field(init=False, repr=False)
    _recall: MultiStrategyRecall = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.ontology, RecallOntology):
            raise TypeError("DocumentAdapter requires a RecallOntology provider")
        self._matcher = AhoCorasickMatcher()
        for surface, info in sorted(self.ontology.all_labels().items()):
            self._matcher.add_pattern(
                surface,
                {"iri": info.concept.iri, "label": info.concept.label, "surface": surface},
            )
        self._matcher.build()
        self._recall = MultiStrategyRecall(self.ontology, top_n=self.recall_top_n)

    def _raw_candidates(
        self, passage: str, *, segments: Sequence[str] | None = None
    ) -> list[MatchCandidate]:
        raw: list[MatchCandidate] = []
        for match in self._matcher.search(passage):
            iri = str(match.value["iri"])
            concept = self.ontology.get_concept(iri)
            raw.append(
                MatchCandidate(
                    iri=iri,
                    label=str(match.value["label"]),
                    score=100.0,
                    branch=concept.branch if concept else "",
                    extraction_path="aho_corasick",
                    surface_term=str(match.value["surface"]),
                )
            )
        phrases = self.phrase_extractor(passage) if segments is None else segments
        for phrase in phrases:
            for recalled in self._recall.recall(phrase):
                raw.append(
                    MatchCandidate(
                        iri=recalled.concept.iri,
                        label=recalled.concept.label,
                        score=recalled.score,
                        branch=recalled.concept.branch,
                        extraction_path="multi_strategy_recall",
                        surface_term=phrase,
                    )
                )
        return raw

    def adapt(
        self, passage: str, *, segments: Sequence[str] | None = None
    ) -> AdapterResult:
        best: dict[str, MatchCandidate] = {}
        for candidate in self._raw_candidates(passage, segments=segments):
            current = best.get(candidate.iri)
            candidate_key = (-candidate.score, candidate.extraction_path, candidate.surface_term)
            current_key = (
                (-current.score, current.extraction_path, current.surface_term)
                if current is not None
                else None
            )
            if current_key is None or candidate_key < current_key:
                best[candidate.iri] = candidate

        counters = dict.fromkeys(SUPPRESSION_CATEGORIES, 0)
        survivors: list[MatchCandidate] = []
        for iri in sorted(best):
            candidate = best[iri]
            if self.blocklist.is_blocked(candidate.surface_term, candidate.iri):
                counters["blocklist"] += 1
                continue
            place = self.place_gate.evaluate(
                query=candidate.surface_term,
                label=candidate.label,
                branch=candidate.branch,
                score=candidate.score,
            )
            if place.demoted and place.score < self.score_floor:
                counters["place_gate"] += 1
                continue
            short = self.short_gate.evaluate(
                query=candidate.surface_term, label=candidate.label, score=place.score
            )
            if short.demoted and short.score < self.score_floor:
                counters["short_label_gate"] += 1
                continue
            if short.score < self.score_floor:
                counters["score_floor"] += 1
                continue
            candidate.score = short.score
            candidate.gated = place.demoted or short.demoted
            candidate.gate_reason = "; ".join((place.reason, short.reason))
            survivors.append(candidate)
        survivors.sort(key=lambda candidate: (-candidate.score, candidate.iri))
        return AdapterResult(
            tuple(survivors), len(best), MappingProxyType(dict(counters))
        )

    def __call__(self, passage: str | GoldItemRecord) -> Sequence[CandidateLike]:
        text = passage if isinstance(passage, str) else passage.input_text
        return self.adapt(text).candidates


@dataclass(frozen=True, slots=True)
class SyntheticScoreResult:
    run: ScoreRun
    nomatch_fp_rate: float
    suppression_counters: Mapping[str, int]
    raw_candidate_count: int
    survivor_count: int
    unscoreable_override: bool = False


def _assert_config(corpus: LoadedCorpus, config: AnswerRuleConfig) -> None:
    actual = config.content_sha256()
    expected = corpus.manifest.answer_rule_config_sha256
    if actual != expected:
        raise SyntheticScoringError(
            "answer_rule_config_sha256 mismatch: " f"expected={expected} actual={actual}"
        )


def score_corpus(
    corpus: LoadedCorpus,
    ontology: RecallOntology,
    config: AnswerRuleConfig,
    *,
    adapter: DocumentAdapter | None = None,
    allow_unscoreable: bool = False,
) -> SyntheticScoreResult:
    """Score verified rows and evaluate no-match false positives outside ``score_items``."""
    _assert_config(corpus, config)
    if not corpus.manifest.scoreable and not allow_unscoreable:
        raise SyntheticScoringError(
            "corpus manifest is not scoreable; pass allow_unscoreable=True for diagnostics"
        )
    document_adapter = adapter or DocumentAdapter(ontology)
    counters = dict.fromkeys(SUPPRESSION_CATEGORIES, 0)
    raw_count = 0
    survivor_count = 0

    def predict(item: GoldItemRecord) -> Sequence[CandidateLike]:
        nonlocal raw_count, survivor_count
        result = document_adapter.adapt(item.input_text)
        raw_count += result.raw_candidate_count
        survivor_count += len(result.candidates)
        for category, count in result.suppression_counters.items():
            counters[category] += count
        return result.candidates

    run = score_items(
        corpus.gold_item_records(),
        predict,
        config=config,
        hierarchy=EMPTY_HIERARCHY,
        slice_name="synthetic",
        keep_ranked=max(DEPTH_PROBE_MAX, config.top_k),
    )
    false_positives = 0
    for item in sorted(corpus.nomatch_items, key=lambda row: row.item_id):
        adapted = document_adapter.adapt(item.text)
        raw_count += adapted.raw_candidate_count
        survivor_count += len(adapted.candidates)
        for category, count in adapted.suppression_counters.items():
            counters[category] += count
        committed = commit_from_ranked(rank_candidates(adapted.candidates, config), config)
        false_positives += bool(committed)
    denominator = len(corpus.nomatch_items)
    return SyntheticScoreResult(
        run=run,
        nomatch_fp_rate=false_positives / denominator if denominator else 0.0,
        suppression_counters=dict(sorted(counters.items())),
        raw_candidate_count=raw_count,
        survivor_count=survivor_count,
        unscoreable_override=not corpus.manifest.scoreable and allow_unscoreable,
    )


def depth_probe(
    run: ScoreRun,
    config: AnswerRuleConfig,
    depths: Sequence[int] = DEPTH_PROBE_DEPTHS,
) -> dict[str, dict[str, float | int]]:
    """Probe candidate depths from rankings already retained by the scoring pass."""
    if any(depth < 1 for depth in depths):
        raise ValueError("depths must be positive")
    if run.config != config:
        raise ValueError("score run config does not match depth-probe config")
    if any(depth > run.ranked_limit for depth in depths):
        raise ValueError(
            f"depth probe is limited to the retained top {run.ranked_limit} candidates"
        )
    output: dict[str, dict[str, float | int]] = {}
    for depth in depths:
        counts = MicroCounts()
        recalls: list[float] = []
        candidate_counts: list[int] = []
        for score in run.item_scores:
            ranked = score.ranked[:depth]
            committed = commit_from_ranked(ranked, config)
            gold = set(score.gold_iris)
            predicted = {candidate.iri for candidate in committed}
            ranked_iris = {candidate.iri for candidate in ranked}
            counts.tp += len(predicted & gold)
            counts.fp += len(predicted - gold)
            counts.fn += len(gold - predicted)
            recalls.append(len(ranked_iris & gold) / len(gold))
            candidate_counts.append(len(ranked))
        output[str(depth)] = {
            "depth": depth,
            "micro_f1": round(counts.f1, 6),
            "mean_raw_candidate_recall_at_k": round(sum(recalls) / len(recalls), 6)
            if recalls
            else 0.0,
            "mean_candidate_count": round(sum(candidate_counts) / len(candidate_counts), 6)
            if candidate_counts
            else 0.0,
        }
    return output


def _publication_fields(
    config: AnswerRuleConfig,
    label: str,
    determinism_selftest: Mapping[str, object],
) -> dict[str, object]:
    """Return the report fields covered by the early publication preflight."""
    return {
        "kind": REPORT_KIND,
        "label": label,
        "answer_rule_config_sha256": config.content_sha256(),
        "answer_rule_config": config.to_json(),
        "determinism_selftest": dict(determinism_selftest),
    }


def build_synthetic_report(
    result: SyntheticScoreResult,
    *,
    corpus: LoadedCorpus,
    config: AnswerRuleConfig,
    label: str,
    ontology_pin: str,
    depth_probe_result: Mapping[str, object],
    determinism_selftest: Mapping[str, object],
) -> dict[str, object]:
    """Assemble the aggregate-only committed report shape for the synthetic lane."""
    return {
        **_publication_fields(config, label, determinism_selftest),
        "corpus_version": corpus.manifest.version,
        "content_sha256": corpus.manifest.content_sha256,
        "ontology_cache_sha256": ontology_pin,
        "overall": result.run.overall.to_json(),
        "slices": {
            key: counts.to_json() for key, counts in sorted(result.run.by_stratum.items())
        },
        "nomatch_fp_rate": round(result.nomatch_fp_rate, 6),
        "suppression_counters": dict(sorted(result.suppression_counters.items())),
        "raw_candidate_count": result.raw_candidate_count,
        "survivor_count": result.survivor_count,
        "unscoreable_override": result.unscoreable_override,
        "depth_probe": dict(sorted(depth_probe_result.items())),
    }


def _value_at_path(report: Mapping[str, object], path: tuple[str, ...]) -> object:
    value: object = report
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            raise SyntheticScoringError(f"public metadata path missing from report: {path!r}")
        value = value[part]
    return value


def preflight_report_publication(
    report: Mapping[str, object],
    leak_manifest: Manifest,
    salt: bytes,
    *,
    public_metadata: PublicReportMetadata | None = None,
) -> None:
    """Fail closed on report strings, except exact values at reviewed public paths."""
    public_fields: Mapping[tuple[str, ...], str] = {}
    if public_metadata is not None:
        report_config_sha = report.get("answer_rule_config_sha256")
        if report_config_sha != public_metadata.answer_rule_config_sha256:
            raise SyntheticScoringError("public metadata answer-rule config hash mismatch")
        for field_path, expected in public_metadata.fields.items():
            if _value_at_path(report, field_path) != expected:
                raise SyntheticScoringError(
                    f"public metadata value mismatch at path: {field_path!r}"
                )
        public_fields = public_metadata.fields

    def collisions(value: object, path: tuple[str, ...] = ()) -> int:
        if isinstance(value, str):
            if public_fields.get(path) == value:
                return 0
            return scan_text(value, leak_manifest, salt)
        if isinstance(value, Mapping):
            total = 0
            for key, nested in value.items():
                if isinstance(key, str):
                    total += scan_text(key, leak_manifest, salt)
                    nested_path = (*path, key)
                else:
                    nested_path = path
                total += collisions(nested, nested_path)
            return total
        if isinstance(value, (list, tuple, set, frozenset)):
            return sum(collisions(nested, path) for nested in value)
        return 0

    collision_count = collisions(report)
    if collision_count:
        raise SyntheticScoringError(f"leak check failed: collisions={collision_count}")


def write_report(
    path: Path,
    report: Mapping[str, object],
    leak_manifest: Manifest,
    salt: bytes,
    *,
    public_metadata: PublicReportMetadata | None = None,
) -> Path:
    """Leak-check the complete serialization, then atomically publish it."""
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    preflight_report_publication(
        report,
        leak_manifest,
        salt,
        public_metadata=public_metadata,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - I/O orchestration
    parser = argparse.ArgumentParser(prog="python -m folio_eval.synthetic_score")
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--public-metadata", type=Path, default=DEFAULT_PUBLIC_METADATA_PATH)
    parser.add_argument("--label", default="synthetic-baseline-v1")
    args = parser.parse_args(argv)
    ensure_hash_seed()
    corpus = load_corpus(args.corpus_manifest)
    pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    selftest = run_determinism_selftest()
    config = load_config(args.config)
    _assert_config(corpus, config)
    leak_manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    public_metadata = load_public_report_metadata(args.public_metadata)
    preflight_report_publication(
        _publication_fields(config, args.label, selftest.to_json()),
        leak_manifest,
        salt,
        public_metadata=public_metadata,
    )
    from folio import FOLIO

    ontology = FolioPythonProvider(_folio=FOLIO())
    adapter = DocumentAdapter(ontology)
    result = score_corpus(corpus, ontology, config, adapter=adapter)
    probe = depth_probe(result.run, config)
    report = build_synthetic_report(
        result,
        corpus=corpus,
        config=config,
        label=args.label,
        ontology_pin=pin.sha256,
        depth_probe_result=probe,
        determinism_selftest=selftest.to_json(),
    )
    write_report(
        args.out,
        report,
        leak_manifest,
        salt,
        public_metadata=public_metadata,
    )
    print(json.dumps({"out": str(args.out), "overall": report["overall"]}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
