"""Offline U4 verdict, publication guard, and real ledger integration tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from folio_eval import experiment
from folio_eval import verifier_report as report
from folio_eval.leakcheck import Manifest, build_manifest, scan_json_value, scan_text
from folio_eval.synthesize import LoadedCorpus
from folio_eval.verifier import PairedVerifierResult, compare_collections
from test_eval_verifier import collection, corpus


def paired() -> PairedVerifierResult:
    c = corpus()
    return compare_collections(collection(c), collection(c), c)


@pytest.mark.parametrize(
    ("low", "recall_drops", "fp", "baseline_fp", "failures"),
    [
        (-0.01, False, 0.0, 1.0, ["significant_f1_lift"]),
        (0.0, False, 0.0, 1.0, ["significant_f1_lift"]),
        (0.01, True, 0.0, 1.0, ["recall_holds"]),
        (0.01, False, 0.5, 1.0, []),
        (0.01, False, 0.500001, 1.0, ["abstention_works"]),
        (0.01, False, 0.5, 0.5, ["abstention_works"]),
    ],
)
def test_verdict(
    low: float,
    recall_drops: bool,
    fp: float,
    baseline_fp: float,
    failures: list[str],
) -> None:
    result = paired()
    if recall_drops:
        result.candidate.run.overall.fn += 1
    result = replace(
        result,
        candidate=replace(result.candidate, nomatch_fp_rate=fp),
        baseline=replace(result.baseline, nomatch_fp_rate=baseline_fp),
        delta=replace(result.delta, point=0.1, low=low, high=0.2),
    )
    verdict = report.evaluate_verdict(result)
    assert verdict["verdict"] == ("no-go" if failures else "go")
    assert verdict["failing_criteria"] == failures
    assert verdict["arm_nomatch_fp_rate"] == fp
    assert verdict["baseline_nomatch_fp_rate"] == baseline_fp
    assert verdict["paired_delta"]["low"] == low
    assert verdict["arm_strict_recall"] == result.candidate.run.overall.recall


def manifest(words: tuple[str, ...] = ("thresholds",)) -> Manifest:
    return build_manifest(words, b"test", gold_version="1", gold_content_sha256="a" * 64)


def test_preflight_collision_before_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    salt = tmp_path / "salt"
    salt.write_bytes(b"test")
    monkeypatch.setattr(report, "load_manifest", lambda path: manifest(("ceiling",)))
    monkeypatch.setattr(report, "ensure_hash_seed", lambda: None)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("preflight must precede loading or scoring")

    monkeypatch.setattr(report, "load_collection", forbidden)
    monkeypatch.setattr(report, "load_corpus", forbidden)
    monkeypatch.setattr(report, "compare_collections", forbidden)
    with pytest.raises(ValueError, match="leak check"):
        report.main(["--arm", "unused", "--salt-file", str(salt)])


def grader_corpus() -> LoadedCorpus:
    c = corpus()
    scored = replace(
        c.corpus_items[0],
        provenance={"grader_votes": [{"model_family": "codex"}, {"model_family": "claude"}]},
    )
    unscored = replace(c.corpus_items[1], verification="needs_review")
    unscored_with_votes = replace(
        c.corpus_items[2],
        verification="needs_review",
        provenance={"grader_votes": [{"model_family": "excluded"}]},
    )
    return replace(c, corpus_items=(scored, unscored, unscored_with_votes))


@pytest.mark.parametrize("provenance", [{}, {"grader_votes": []}])
def test_scored_items_without_votes_are_counted(provenance: dict[str, Any]) -> None:
    c = grader_corpus()
    missing = replace(corpus().corpus_items[3], provenance=provenance)
    mix = report.grader_mix(replace(c, corpus_items=(*c.corpus_items, missing)))
    assert mix == {
        "passage_count": 2,
        "votes_by_family": {"claude": 1, "codex": 1},
        "passages_by_mix": {"claude: 1, codex: 1": 1},
        "corpus_rows_without_votes": 2,
        "scored_items_without_votes": 1,
    }


def test_outputs_and_provenance(tmp_path: Path) -> None:
    mix = report.grader_mix(grader_corpus())
    assert mix == {
        "passage_count": 1,
        "votes_by_family": {"claude": 1, "codex": 1},
        "passages_by_mix": {"claude: 1, codex: 1": 1},
        "corpus_rows_without_votes": 1,
        "scored_items_without_votes": 0,
    }
    depth = {
        "chosen_n": 24,
        "unreachable_gold_count_at_200": 7,
        "curve": {"24": {"unreachable_gold_count": 9}},
    }
    payload = report.build_report(paired(), depth, mix)
    report.write_reports(payload, tmp_path, manifest(), b"test")
    saved = json.loads((tmp_path / "verifier-ceiling.json").read_text())
    markdown = (tmp_path / "verifier-ceiling.md").read_text()
    assert saved == payload
    assert saved["unreachable_gold_count_at_chosen_n"] == 9
    assert saved["unreachable_gold_count_at_200"] == 7
    assert "shared model tendencies" in markdown
    for text in (json.dumps(saved), markdown):
        assert "thresholds" not in text.lower()
        assert not scan_text(text, manifest(), b"test")
    assert not scan_json_value(saved, manifest(), b"test")
    assert len(saved["arm"]["cuts_by_fold"]) == 5
    assert saved["baseline"]["cuts_by_fold"][0] == {
        "fold": 0,
        "admission_cut": 0.5,
        "nomatch_cut": None,
    }


def test_record_real_guarded_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Selftest:
        def to_json(self) -> dict[str, bool]:
            return {"passed": True}

    monkeypatch.setattr(experiment, "run_determinism_selftest", lambda target: Selftest())
    result = paired()
    verdict = report.evaluate_verdict(result)
    log = tmp_path / "experiments.jsonl"
    record = report.record_experiment(
        result,
        verdict,
        corpus(),
        manifest(),
        b"test",
        experiments_log=log,
        pending_path=tmp_path / "pending.json",
    )
    saved = json.loads(log.read_text())
    assert saved == record.to_json()
    assert saved["lever_scope"] == "adapter_only"
    assert saved["decision"] == "park"
    assert json.loads(saved["reason"]) == verdict
    assert saved["scores_before"]["synthetic"]["aggregate"]["f1"] == result.baseline.run.overall.f1
    assert not experiment._manifest_record_collisions(saved, manifest(), b"test")
    assert saved["bootstrap_ci"]["n_units"] == 10
    assert not (tmp_path / "pending.json").exists()


def test_pristine_gate_includes_untracked(monkeypatch: pytest.MonkeyPatch) -> None:
    class Status:
        stdout = "?? ordinary-untracked.txt\n"

    monkeypatch.setattr(
        "folio_eval.verifier_report.subprocess.run", lambda *args, **kwargs: Status()
    )
    with pytest.raises(ValueError, match="pristine"):
        report.require_pristine(Path("."))


@pytest.mark.parametrize(
    "bad_field",
    [
        None,
        "corpus_content_sha256",
        "nomatch_content_sha256",
        "adapter_source",
        "adapter_sha256",
        "answer_rule_config_sha256",
        "ontology_cache_sha256",
    ],
)
@pytest.mark.parametrize("record", [False, True])
def test_runner_fixture_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: bool, bad_field: str | None
) -> None:
    """Run CLI orchestration on in-memory fixture collections; never a live report."""
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setattr(report, "ROOT", tmp_path)
    monkeypatch.setattr(report, "load_manifest", lambda path: manifest())
    monkeypatch.setattr(report, "load_corpus", lambda path: corpus())
    monkeypatch.setattr(report, "load_collection", lambda path, c: collection(c))
    events: list[str] = []
    monkeypatch.setattr(report, "require_pristine", lambda root: events.append("pristine"))
    monkeypatch.setattr(report, "record_experiment", lambda *args: events.append("record"))
    salt = tmp_path / "salt"
    salt.write_bytes(b"test")
    depth = tmp_path / "depth.json"
    c = corpus()
    baseline = collection(c)
    depth_payload = {
        "chosen_n": 24,
        "unreachable_gold_count_at_200": 0,
        "curve": {"24": {"unreachable_gold_count": 0}},
        "corpus_content_sha256": c.manifest.content_sha256,
        "nomatch_content_sha256": c.manifest.nomatch_content_sha256,
        "adapter_source": baseline.adapter_source,
        "adapter_sha256": baseline.adapter_sha256,
        "answer_rule_config_sha256": baseline.prompt_template_sha256,
        "ontology_cache_sha256": c.manifest.ontology_cache_sha256,
    }
    # The baseline prompt hash identifies the deterministic answer rule.
    c = replace(
        c, manifest=replace(c.manifest, answer_rule_config_sha256=baseline.prompt_template_sha256)
    )
    monkeypatch.setattr(report, "load_corpus", lambda path: c)
    if bad_field:
        depth_payload[bad_field] = "0" * 64
    depth.write_text(json.dumps(depth_payload))
    argv = ["--arm", "fixture", "--depth", str(depth), "--salt-file", str(salt)]
    if record:
        argv.append("--record-experiment")
    if bad_field:
        with pytest.raises(ValueError, match=bad_field):
            report.main(argv)
        assert events == (["pristine"] if record else [])
        assert not (tmp_path / "docs").exists()
        return
    assert report.main(argv) == 0
    assert events == (["pristine", "record"] if record else [])
    output = json.loads((tmp_path / "docs/benchmarks/verifier-ceiling.json").read_text())
    assert output["grader_mix"]["scored_items_without_votes"] == 10
    assert output["grader_mix"]["corpus_rows_without_votes"] == 10
    assert output["decision"]["paired_delta"]["n_resamples"] == 2000
    assert output["decision"]["paired_delta"]["seed"] == 20260727
    assert output["decision"]["paired_delta"]["alpha"] == 0.05


def test_dynamic_collision_writes_nothing(tmp_path: Path) -> None:
    depth = {
        "chosen_n": 24,
        "unreachable_gold_count_at_200": 0,
        "curve": {"24": {"unreachable_gold_count": 0}},
    }
    payload = report.build_report(paired(), depth, {"planted secret": 1})
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="leak check"):
        report.write_reports(payload, output, manifest(("planted secret",)), b"test")
    assert not output.exists()
