from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from folio_eval import synthetic_score as synthetic_score_module
from folio_eval.answer_rule import AnswerRuleConfig, commit_answers, load_config
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.score import score_items
from folio_eval.selftest import synthetic_scoring_payload
from folio_eval.splits import GoldItemRecord
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem
from folio_eval.synthetic_score import (
    DocumentAdapter,
    PublicReportMetadata,
    SyntheticScoringError,
    build_synthetic_report,
    depth_probe,
    load_public_report_metadata,
    score_corpus,
    write_report,
)

from folio_resolve import AliasBlocklist, BlockedAlias, Concept, InMemoryOntology
from folio_resolve.pipeline import MatchCandidate


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


def _public_metadata_payload(config: AnswerRuleConfig) -> dict[str, object]:
    return {
        "kind": "synthetic-report-public-metadata",
        "version": 1,
        "answer_rule_config_sha256": config.content_sha256(),
        "fields": [
            {"path": ["kind"], "value": "synthetic_baseline"},
            {"path": ["label"], "value": "synthetic-baseline-v1"},
            {
                "path": ["answer_rule_config", "rationale"],
                "value": config.rationale,
            },
            {
                "path": ["determinism_selftest", "target"],
                "value": "folio_eval.selftest:synthetic_scoring_payload",
            },
        ],
    }


def _public_metadata(tmp_path: Path, config: AnswerRuleConfig) -> PublicReportMetadata:
    path = tmp_path / "public-report-metadata.json"
    path.write_text(json.dumps(_public_metadata_payload(config)), encoding="utf-8")
    return load_public_report_metadata(path)


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
    assert len(first.traces) == first.raw_candidate_count
    assert [trace.iri for trace in first.traces] == sorted(trace.iri for trace in first.traces)
    dispositions = Counter(trace.gate_disposition for trace in first.traces)
    assert dispositions["survived"] == len(first.candidates)
    for category, count in first.suppression_counters.items():
        assert dispositions[category] == count


def test_adapter_committed_sets_repeat_identically() -> None:
    config = AnswerRuleConfig(threshold=0.5, top_k=5)
    adapter = DocumentAdapter(_ontology())
    passage = "Arbitration Rules govern the burden of proof."
    assert commit_answers(adapter(passage), config) == commit_answers(adapter(passage), config)


def test_adapter_does_not_retain_candidates_for_identical_passage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DocumentAdapter(_ontology())
    calls = 0
    original = adapter._raw_candidates

    def counted(
        passage: str, *, segments: tuple[str, ...] | None = None
    ) -> list[MatchCandidate]:
        nonlocal calls
        calls += 1
        return original(passage, segments=segments)

    monkeypatch.setattr(adapter, "_raw_candidates", counted)
    passage = "Arbitration Rules govern the burden of proof."

    assert adapter.adapt(passage) == adapter.adapt(passage)
    assert calls == 2


def test_adapter_results_are_isolated_from_returned_candidate_mutation() -> None:
    adapter = DocumentAdapter(_ontology())
    passage = "Arbitration Rules govern this motion."

    first = adapter.adapt(passage)
    original_score = first.candidates[0].score
    first.candidates[0].score = -1.0

    second = adapter.adapt(passage)
    assert second.candidates[0].score == original_score
    assert second.candidates[0] is not first.candidates[0]


def test_adapter_honors_explicit_segments_separately() -> None:
    adapter = DocumentAdapter(_ontology(), phrase_extractor=lambda _text: ())
    passage = "Unrelated prose without an exact label."

    assert adapter.adapt(passage).candidates == ()
    segmented = adapter.adapt(passage, segments=("Arbitration Rules",))

    assert [candidate.iri for candidate in segmented.candidates] == ["R-arb"]


def test_adapter_has_no_corpus_wide_result_cache() -> None:
    adapter = DocumentAdapter(_ontology())

    for index in range(10):
        adapter.adapt(f"Distinct passage {index} about Arbitration Rules.")

    assert "_cache" not in vars(adapter)


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


def test_unscoreable_corpus_requires_explicit_diagnostics_override() -> None:
    config = AnswerRuleConfig(threshold=0.5)
    corpus = _corpus(config)
    corpus = replace(corpus, manifest=replace(corpus.manifest, scoreable=False))
    with pytest.raises(SyntheticScoringError, match="not scoreable"):
        score_corpus(corpus, _ontology(), config)
    result = score_corpus(corpus, _ontology(), config, allow_unscoreable=True)
    assert result.unscoreable_override is True
    report = build_synthetic_report(
        result, corpus=corpus, config=config, label="diagnostic",
        ontology_pin=corpus.manifest.ontology_cache_sha256,
        depth_probe_result={}, determinism_selftest={},
    )
    assert report["unscoreable_override"] is True


def test_depth_probe_has_monotone_counts_and_metrics() -> None:
    config = AnswerRuleConfig(threshold=0.0, top_k=5)
    result = score_corpus(_corpus(config), _ontology(), config)
    probe = depth_probe(result.run, config, depths=(1, 2, 5))
    counts = [probe[str(depth)]["mean_candidate_count"] for depth in (1, 2, 5)]
    assert counts == sorted(counts)
    for row in probe.values():
        assert 0.0 <= row["micro_f1"] <= 1.0
        assert 0.0 <= row["mean_raw_candidate_recall_at_k"] <= 1.0


def test_depth_probe_rejects_config_mismatch_and_unretained_depth() -> None:
    config = AnswerRuleConfig(threshold=0.0, top_k=5)
    result = score_corpus(_corpus(config), _ontology(), config)

    with pytest.raises(ValueError, match="config does not match"):
        depth_probe(result.run, replace(config, threshold=0.1), depths=(1,))

    result.run.ranked_limit = 1
    with pytest.raises(ValueError, match="retained top 1"):
        depth_probe(result.run, config, depths=(2,))
    assert depth_probe(result.run, config, depths=(1,))["1"]["depth"] == 1


def test_depth_probe_matches_independent_candidate_depth_rescore() -> None:
    config = AnswerRuleConfig(threshold=0.55, top_k=2)
    corpus = _corpus(config)
    ontology = _ontology()
    adapter = DocumentAdapter(ontology)
    result = score_corpus(corpus, ontology, config, adapter=adapter)
    depths = (1, 2, 3)
    observed = depth_probe(result.run, config, depths=depths)
    records = corpus.gold_item_records()
    candidates = {
        item.item_id: adapter.adapt(item.input_text).candidates for item in records
    }

    for depth in depths:
        def predict_at_depth(
            item: GoldItemRecord, cap: int = depth
        ) -> tuple[MatchCandidate, ...]:
            return candidates[item.item_id][:cap]

        rescored = score_items(
            records,
            predict_at_depth,
            config=config,
            slice_name=f"independent-depth-{depth}",
            keep_ranked=depth,
        )
        recalls = [
            len(
                {candidate.iri for candidate in candidates[item.item_id][:depth]}
                & item.gold_iris
            )
            / len(item.gold_iris)
            for item in records
        ]
        counts = [min(depth, len(candidates[item.item_id])) for item in records]

        assert observed[str(depth)] == {
            "depth": depth,
            "micro_f1": round(rescored.overall.f1, 6),
            "mean_raw_candidate_recall_at_k": round(sum(recalls) / len(recalls), 6),
            "mean_candidate_count": round(sum(counts) / len(counts), 6),
        }


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
        depth_probe_result=depth_probe(result.run, config, depths=(1,)),
        determinism_selftest={"matched": True},
    )
    salt = b"tiny-test-salt"
    manifest = build_manifest(
        ["planted collision"],
        salt=salt,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    out = tmp_path / "report.json"
    with pytest.raises(SyntheticScoringError, match="leak check"):
        write_report(out, report, manifest, salt)
    assert not out.exists()


def test_versioned_public_metadata_allows_only_exact_approved_paths(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    metadata = _public_metadata(tmp_path, config)
    public_values = tuple(metadata.fields.values())
    salt = b"tiny-test-salt"
    manifest = build_manifest(
        public_values,
        salt=salt,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    report = {
        "kind": "synthetic_baseline",
        "label": "synthetic-baseline-v1",
        "answer_rule_config_sha256": config.content_sha256(),
        "answer_rule_config": config.to_json(),
        "determinism_selftest": {
            "target": "folio_eval.selftest:synthetic_scoring_payload"
        },
    }

    out = tmp_path / "report.json"
    write_report(out, report, manifest, salt, public_metadata=metadata)

    assert out.exists()


def test_public_metadata_contract_rejects_every_malformed_shape(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    base = _public_metadata_payload(config)
    base_fields = cast(list[object], base["fields"])
    invalid_contracts: list[tuple[object, str]] = [
        ([], "invalid public metadata contract"),
        ({**base, "kind": "wrong"}, "invalid public metadata contract"),
        ({**base, "version": 2}, "unsupported public metadata version"),
        ({**base, "answer_rule_config_sha256": "bad"}, "config hash"),
        ({**base, "fields": "bad"}, "fields must be a list"),
        ({**base, "fields": [None]}, "field must be an object"),
        (
            {**base, "fields": [{"path": [], "value": "bad"}]},
            "malformed public metadata field",
        ),
        ({**base, "fields": [*base_fields, base_fields[0]]}, "duplicate"),
        ({**base, "fields": base_fields[:-1]}, "paths do not match"),
    ]

    for index, (payload, message) in enumerate(invalid_contracts):
        path = tmp_path / f"invalid-public-metadata-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SyntheticScoringError, match=message):
            load_public_report_metadata(path)


def test_public_metadata_never_exempts_data_derived_collisions(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    metadata = _public_metadata(tmp_path, config)
    salt = b"tiny-test-salt"
    manifest = build_manifest(
        ["planted collision"],
        salt=salt,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    report = {
        "kind": "synthetic_baseline",
        "label": "synthetic-baseline-v1",
        "answer_rule_config_sha256": config.content_sha256(),
        "answer_rule_config": config.to_json(),
        "determinism_selftest": {
            "target": "folio_eval.selftest:synthetic_scoring_payload"
        },
        "slices": {"generated": "planted collision"},
    }

    with pytest.raises(SyntheticScoringError, match="leak check"):
        write_report(
            tmp_path / "report.json",
            report,
            manifest,
            salt,
            public_metadata=metadata,
        )


def test_approved_value_still_collides_at_an_unapproved_path(tmp_path: Path) -> None:
    config = AnswerRuleConfig()
    metadata = _public_metadata(tmp_path, config)
    approved_label = metadata.fields[("label",)]
    salt = b"tiny-test-salt"
    manifest = build_manifest(
        [approved_label],
        salt=salt,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    report = {
        "kind": "synthetic_baseline",
        "label": approved_label,
        "answer_rule_config_sha256": config.content_sha256(),
        "answer_rule_config": config.to_json(),
        "determinism_selftest": {
            "target": "folio_eval.selftest:synthetic_scoring_payload"
        },
        "slices": {"generated": approved_label},
    }

    with pytest.raises(SyntheticScoringError, match="leak check"):
        write_report(
            tmp_path / "report.json",
            report,
            manifest,
            salt,
            public_metadata=metadata,
        )


def test_checked_in_public_metadata_matches_the_checked_in_config() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "eval/synthetic/answer_rule_config_synthetic_v1.json")
    metadata = load_public_report_metadata(
        repo_root / "eval/synthetic/public_report_metadata_v1.json"
    )

    assert metadata.answer_rule_config_sha256 == config.content_sha256()
    assert metadata.fields[("answer_rule_config", "rationale")] == config.rationale


def test_main_runs_publication_preflight_before_adapter_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AnswerRuleConfig()
    corpus = _corpus(config)
    metadata_path = _public_metadata(tmp_path, config).source_path
    salt_path = tmp_path / "salt"
    salt_path.write_bytes(b"tiny-test-salt")
    leak_manifest = build_manifest(
        ["planted collision"],
        salt=b"tiny-test-salt",
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
        gold_version="test",
        gold_content_sha256="d" * 64,
    )
    adapter_constructed = False

    def fail_if_constructed(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_constructed
        adapter_constructed = True

    monkeypatch.setattr(synthetic_score_module, "ensure_hash_seed", lambda: None)
    monkeypatch.setattr(synthetic_score_module, "load_corpus", lambda _path: corpus)
    monkeypatch.setattr(
        synthetic_score_module,
        "assert_ontology_pin",
        lambda _sha: SimpleNamespace(sha256=corpus.manifest.ontology_cache_sha256),
    )
    monkeypatch.setattr(
        synthetic_score_module,
        "run_determinism_selftest",
        lambda: SimpleNamespace(
            to_json=lambda: {
                "target": "folio_eval.selftest:synthetic_scoring_payload",
                "note": "planted collision",
            }
        ),
    )
    monkeypatch.setattr(synthetic_score_module, "load_config", lambda _path: config)
    monkeypatch.setattr(synthetic_score_module, "load_manifest", lambda _path: leak_manifest)
    monkeypatch.setattr(synthetic_score_module, "DocumentAdapter", fail_if_constructed)

    with pytest.raises(SyntheticScoringError, match="leak check"):
        synthetic_score_module.main(
            [
                "--corpus-manifest", str(tmp_path / "corpus.json"),
                "--config", str(tmp_path / "config.json"),
                "--out", str(tmp_path / "report.json"),
                "--leak-manifest", str(tmp_path / "leak.json"),
                "--salt-file", str(salt_path),
                "--public-metadata", str(metadata_path),
                "--label", "synthetic-baseline-v1",
            ]
        )

    assert adapter_constructed is False


def test_existing_synthetic_scoring_payload_is_unchanged_and_deterministic() -> None:
    assert synthetic_scoring_payload() == synthetic_scoring_payload()
