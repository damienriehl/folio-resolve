from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from folio_eval.answer_rule import AnswerRuleConfig, commit_answers
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.selftest import synthetic_scoring_payload
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.synthetic_score import (
    DocumentAdapter,
    SyntheticScoringError,
    build_synthetic_report,
    depth_probe,
    score_corpus,
    write_report,
)

from folio_resolve import AliasBlocklist, BlockedAlias, Concept, InMemoryOntology


def _ontology() -> InMemoryOntology:
    return InMemoryOntology(
        [
            Concept(iri="R-arb", label="Arbitration Rules"),
            Concept(iri="R-proof", label="Burden of Proof", definition="evidentiary burden"),
            Concept(iri="R-place", label="Spain", branch="Location"),
        ]
    )


def _corpus(config: AnswerRuleConfig) -> LoadedCorpus:
    manifest = CorpusManifest(
        version=1,
        content_sha256="a" * 64,
        nomatch_content_sha256="b" * 64,
        ontology_cache_sha256="c" * 64,
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={"needs_review": 1, "nomatch": 2, "scoreable": 2},
        non_lexical_fraction=0.5,
        non_lexical_floor=0.3,
        scoreable=True,
        seed=7,
        created="2026-08-16T00:00:00Z",
        manifest_path=Path("corpus_v1.manifest.json"),
    )
    return LoadedCorpus(
        manifest=manifest,
        corpus_items=(
            SyntheticItem(
                item_id="score-1",
                doc_type="motion",
                jurisdiction="US",
                text="The motion invokes Arbitration Rules.",
                gold_iris=frozenset({"R-arb"}),
                verification="deterministic",
            ),
            SyntheticItem(
                item_id="score-2",
                doc_type="brief",
                jurisdiction="US",
                text="The evidentiary burden concerns the burden of proof.",
                gold_iris=frozenset({"R-proof"}),
                verification="human",
            ),
            SyntheticItem(
                item_id="review",
                doc_type="brief",
                jurisdiction="US",
                text="Arbitration Rules",
                gold_iris=frozenset(),
                verification="needs_review",
            ),
        ),
        nomatch_items=(
            SyntheticItem(
                item_id="no-1",
                doc_type="contract",
                jurisdiction="US",
                text="The parties exchanged ordinary notices.",
                provenance={"no_match": True},
            ),
            SyntheticItem(
                item_id="no-2",
                doc_type="contract",
                jurisdiction="US",
                text="A routine signature page follows.",
                provenance={"no_match": True},
            ),
        ),
    )


def test_adapter_is_deterministic_and_accounts_for_every_raw_candidate() -> None:
    ontology = _ontology()
    blocklist = AliasBlocklist([BlockedAlias("Arbitration Rules", "R-arb")])
    adapter = DocumentAdapter(ontology, blocklist=blocklist)

    first = adapter.adapt("Arbitration Rules were discussed near Spain.")
    second = adapter.adapt("Arbitration Rules were discussed near Spain.")

    assert first == second
    assert [candidate.iri for candidate in first.candidates] == sorted(
        (candidate.iri for candidate in first.candidates),
        key=lambda iri: next(-c.score for c in first.candidates if c.iri == iri),
    )
    assert first.raw_candidate_count == len(first.candidates) + sum(first.suppression_counters.values())
    assert first.suppression_counters["blocklist"] >= 1


def test_adapter_committed_sets_repeat_identically() -> None:
    config = AnswerRuleConfig(threshold=0.5, top_k=5)
    adapter = DocumentAdapter(_ontology())
    passage = "Arbitration Rules govern the burden of proof."
    assert commit_answers(adapter(passage), config) == commit_answers(adapter(passage), config)


def test_nomatch_fp_rate_and_needs_review_exclusion() -> None:
    config = AnswerRuleConfig(threshold=0.5, top_k=5)
    corpus = _corpus(config)
    real = score_corpus(corpus, _ontology(), config)
    always = DocumentAdapter(_ontology(), phrase_extractor=lambda _text: ("Arbitration Rules",))
    planted = score_corpus(corpus, _ontology(), config, adapter=always)

    assert real.nomatch_fp_rate < 1.0
    assert planted.nomatch_fp_rate == 1.0
    assert real.run.overall.items == 2
    assert {item.item_id for item in real.run.item_scores} == {"score-1", "score-2"}


def test_config_hash_mismatch_raises() -> None:
    config = AnswerRuleConfig(threshold=0.5)
    corpus = _corpus(config)
    with pytest.raises(SyntheticScoringError, match="answer_rule_config_sha256"):
        score_corpus(corpus, _ontology(), replace(config, threshold=0.9))


def test_depth_probe_has_monotone_counts_and_metrics() -> None:
    config = AnswerRuleConfig(threshold=0.0, top_k=5)
    probe = depth_probe(_corpus(config), _ontology(), config, depths=(1, 2, 5))
    counts = [probe[str(depth)]["mean_candidate_count"] for depth in (1, 2, 5)]
    assert counts == sorted(counts)
    for row in probe.values():
        assert 0.0 <= row["micro_f1"] <= 1.0
        assert 0.0 <= row["mean_raw_candidate_recall_at_k"] <= 1.0


def test_report_shape_and_leak_checked_atomic_write(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    result = score_corpus(corpus, _ontology(), config)
    report = build_synthetic_report(
        result,
        corpus=corpus,
        config=config,
        label="planted collision",
        ontology_pin=corpus.manifest.ontology_cache_sha256,
        depth_probe_result=depth_probe(corpus, _ontology(), config, depths=(1,)),
        determinism_selftest={"matched": True},
    )
    salt = b"tiny-test-salt"
    manifest = build_manifest(
        ["planted collision"],
        salt=salt,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    out = tmp_path / "report.json"
    with pytest.raises(SyntheticScoringError, match="leak check"):
        write_report(out, report, manifest, salt)
    assert not out.exists()


def test_existing_synthetic_scoring_payload_is_unchanged_and_deterministic() -> None:
    assert synthetic_scoring_payload() == synthetic_scoring_payload()
