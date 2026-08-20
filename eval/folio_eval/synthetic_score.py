"""Document-level adapter and scoring runner for the frozen synthetic lane (U8)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from .score import EMPTY_HIERARCHY, ScoreRun, score_items
from .selftest import assert_ontology_pin, ensure_hash_seed, run_determinism_selftest
from .splits import GoldItemRecord
from .synthesize import LoadedCorpus, load_corpus

PhraseExtractor = Callable[[str], Sequence[str]]
SUPPRESSION_CATEGORIES = ("blocklist", "place_gate", "short_label_gate", "score_floor")


class SyntheticScoringError(RuntimeError):
    """Raised when a synthetic scoring contract or publication gate fails."""


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
    recall_top_n: int = 200
    _matcher: AhoCorasickMatcher = field(init=False, repr=False)
    _recall: MultiStrategyRecall = field(init=False, repr=False)
    _cache: dict[tuple[str, tuple[str, ...] | None, int | None], AdapterResult] = field(
        init=False, repr=False, default_factory=dict
    )

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

    @staticmethod
    def _copy_result(result: AdapterResult) -> AdapterResult:
        return AdapterResult(
            tuple(replace(candidate) for candidate in result.candidates),
            result.raw_candidate_count,
            MappingProxyType(dict(result.suppression_counters)),
        )

    def adapt(
        self, passage: str, *, segments: Sequence[str] | None = None
    ) -> AdapterResult:
        segment_key = tuple(segments) if segments is not None else None
        extractor_key = None if segments is not None else id(self.phrase_extractor)
        cache_key = (passage, segment_key, extractor_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._copy_result(cached)
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
        result = AdapterResult(
            tuple(survivors), len(best), MappingProxyType(dict(counters))
        )
        self._cache[cache_key] = self._copy_result(result)
        return self._copy_result(result)

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
        keep_ranked=max(200, config.top_k),
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
    corpus: LoadedCorpus,
    ontology: RecallOntology,
    config: AnswerRuleConfig,
    depths: Sequence[int] = (10, 50, 200),
    *,
    adapter: DocumentAdapter | None = None,
) -> dict[str, dict[str, float | int]]:
    """Compare committed F1 and pre-answer-rule gold recall under candidate depth caps."""
    _assert_config(corpus, config)
    if any(depth < 1 for depth in depths):
        raise ValueError("depths must be positive")
    document_adapter = adapter or DocumentAdapter(ontology, recall_top_n=max(depths, default=1))
    if document_adapter.recall_top_n < max(depths, default=1):
        raise ValueError("adapter recall_top_n must cover every requested depth")
    records = corpus.gold_item_records()
    cached = {
        item.item_id: document_adapter.adapt(item.input_text).candidates for item in records
    }
    output: dict[str, dict[str, float | int]] = {}
    for depth in depths:

        def _predict_at_depth(item: GoldItemRecord, cap: int = depth) -> tuple[CandidateLike, ...]:
            return tuple(cached[item.item_id][:cap])

        run = score_items(
            records,
            _predict_at_depth,
            config=config,
            slice_name=f"synthetic-depth-{depth}",
            keep_ranked=depth,
        )
        recalls = [
            len(set(candidate.iri for candidate in cached[item.item_id][:depth]) & item.gold_iris)
            / len(item.gold_iris)
            for item in records
        ]
        candidate_counts = [min(depth, len(cached[item.item_id])) for item in records]
        output[str(depth)] = {
            "depth": depth,
            "micro_f1": round(run.overall.f1, 6),
            "mean_raw_candidate_recall_at_k": round(sum(recalls) / len(recalls), 6)
            if recalls
            else 0.0,
            "mean_candidate_count": round(sum(candidate_counts) / len(candidate_counts), 6)
            if candidate_counts
            else 0.0,
        }
    return output


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
        "kind": "synthetic_baseline",
        "label": label,
        "corpus_version": corpus.manifest.version,
        "content_sha256": corpus.manifest.content_sha256,
        "answer_rule_config_sha256": config.content_sha256(),
        "answer_rule_config": config.to_json(),
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
        "determinism_selftest": dict(determinism_selftest),
    }


def write_report(path: Path, report: Mapping[str, object], leak_manifest: Manifest, salt: bytes) -> Path:
    """Leak-check the complete serialization, then atomically publish it."""
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    def strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            return [
                text
                for key, nested in value.items()
                for text in (*strings(key), *strings(nested))
            ]
        if isinstance(value, (list, tuple, set, frozenset)):
            return [text for nested in value for text in strings(nested)]
        return []

    collisions = sum(scan_text(text, leak_manifest, salt) for text in strings(report))
    if collisions:
        raise SyntheticScoringError(f"leak check failed: collisions={collisions}")
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
    parser.add_argument("--label", default="synthetic-baseline")
    args = parser.parse_args(argv)
    ensure_hash_seed()
    corpus = load_corpus(args.corpus_manifest)
    pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    selftest = run_determinism_selftest()
    config = load_config(args.config)
    _assert_config(corpus, config)
    from folio import FOLIO

    ontology = FolioPythonProvider(_folio=FOLIO())
    adapter = DocumentAdapter(ontology)
    result = score_corpus(corpus, ontology, config, adapter=adapter)
    probe = depth_probe(corpus, ontology, config, adapter=adapter)
    report = build_synthetic_report(
        result,
        corpus=corpus,
        config=config,
        label=args.label,
        ontology_pin=pin.sha256,
        depth_probe_result=probe,
        determinism_selftest=selftest.to_json(),
    )
    write_report(args.out, report, load_manifest(args.leak_manifest), args.salt_file.read_bytes())
    print(json.dumps({"out": str(args.out), "overall": report["overall"]}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
