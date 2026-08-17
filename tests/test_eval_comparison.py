"""Synthetic cross-stack comparison orchestration (U10c)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from folio_eval.answer_rule import AnswerRuleConfig
from folio_eval.comparison import (
    ComparisonError,
    IncumbentInstallMismatch,
    StackContractError,
    StackRun,
    VersionSkewError,
    assert_incumbent_probe,
    build_comparison,
    classify_verdict,
    emit_items_file,
    parse_stack_output,
    score_stack,
    write_comparison,
    write_stage_snapshots,
)
from folio_eval.leakcheck import build_manifest
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem


def _corpus(tmp_path: Path) -> LoadedCorpus:
    config = AnswerRuleConfig()
    scoreable = (
        SyntheticItem("one", "brief", "US", "Alpha beta", ("A",), frozenset({"iri:a"}), "human"),
        SyntheticItem("two", "brief", "US", "Gamma", ("B",), frozenset({"iri:b"}), "human"),
    )
    nomatch = (SyntheticItem("none", "brief", "US", "Nothing here", provenance={"no_match": True}),)
    manifest = CorpusManifest(
        version=7,
        content_sha256="corpus-hash",
        nomatch_content_sha256="nomatch-hash",
        ontology_cache_sha256="ontology-hash",
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={},
        non_lexical_fraction=0.0,
        non_lexical_floor=0.0,
        scoreable=True,
        seed=1,
        created="2026-08-16",
        manifest_path=tmp_path / "manifest.json",
    )
    return LoadedCorpus(manifest, scoreable, nomatch)


def _run(stack: str, lane: str, rows: dict[str, set[str]], *, py: str = "1.2.3") -> StackRun:
    return StackRun(
        stack=stack,
        lane=lane,
        folio_resolve_version="0.4.0" if lane == "incumbent" else "0.5.0",
        folio_python_version=py,
        config={"top_k": 3},
        rows={key: frozenset(value) for key, value in rows.items()},
        stages={key: {} for key in rows},
    )


def test_parse_join_and_metrics_math(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "synthetic-stack-run",
                        "stack": "folio-mapper",
                        "lane": "incumbent",
                        "folio_resolve_version": "0.4.0",
                        "folio_python_version": "1.2.3",
                        "config": {},
                    }
                ),
                json.dumps({"item_id": "one", "iris": ["iri:a", "iri:x"], "stages": {"ranked": 2}}),
                json.dumps({"item_id": "two", "iris": [], "stages": {}}),
                json.dumps({"item_id": "none", "iris": ["iri:x"], "stages": {}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run = parse_stack_output(path)
    metrics, item_f1 = score_stack(_corpus(tmp_path), run)
    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["f1"] == 0.5
    assert metrics["nomatch_fp_rate"] == 1.0
    assert item_f1 == {"one": pytest.approx(2 / 3), "two": 0.0}


@pytest.mark.parametrize(
    ("candidate", "incumbent", "expected"),
    [
        ([1.0] * 20, [0.0] * 20, "win"),
        ([0.0] * 20, [1.0] * 20, "loss"),
        ([1.0, 0.0], [0.0, 1.0], "hold"),
    ],
)
def test_verdict_ci_classification(
    candidate: list[float], incumbent: list[float], expected: str
) -> None:
    verdict = classify_verdict(candidate, incumbent, n_resamples=500, seed=11)
    assert verdict["verdict"] == expected


def test_version_skew_aborts(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    runs = [
        _run(
            "folio-resolve",
            "candidate",
            {"one": {"iri:a"}, "two": {"iri:b"}, "none": set()},
            py="1",
        ),
        _run("folio-mapper", "incumbent", {"one": set(), "two": set(), "none": set()}, py="2"),
    ]
    with pytest.raises(VersionSkewError, match="folio-python version skew"):
        build_comparison(corpus, runs, AnswerRuleConfig())


@pytest.mark.parametrize("field", ["folio_resolve_version", "folio_python_version"])
def test_parse_rejects_empty_version_strings(tmp_path: Path, field: str) -> None:
    header = {
        "kind": "synthetic-stack-run", "stack": "s", "lane": "candidate",
        "folio_resolve_version": "1", "folio_python_version": "1", "config": {},
    }
    header[field] = "  "
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    with pytest.raises(StackContractError, match="empty"):
        parse_stack_output(path)


def test_incumbent_assertion_logic() -> None:
    probe = {
        "folio_resolve_version": "0.4.0",
        "folio_resolve_file": "/venv/lib/python3.12/site-packages/folio_resolve/__init__.py",
        "folio_python_version": "1.2.3",
    }
    assert assert_incumbent_probe(probe, "0.4.0")["folio_python_version"] == "1.2.3"
    with pytest.raises(IncumbentInstallMismatch):
        assert_incumbent_probe({**probe, "folio_resolve_version": "0.3.0"}, "0.4.0")
    with pytest.raises(IncumbentInstallMismatch):
        assert_incumbent_probe(
            {**probe, "folio_resolve_file": "/checkout/src/folio_resolve/__init__.py"}, "0.4.0"
        )


def test_items_file_emits_shared_segments_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def extractor(text: str) -> tuple[str, ...]:
        calls.append(text)
        return (text.upper(),)

    out = emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl", extractor=extractor)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["segments"] for row in rows] == [["ALPHA BETA"], ["GAMMA"], ["NOTHING HERE"]]
    assert calls == ["Alpha beta", "Gamma", "Nothing here"]


def test_items_and_stage_snapshots_are_leak_gated(tmp_path: Path) -> None:
    salt = b"0123456789abcdef"
    manifest = build_manifest(["Alpha beta"], salt=salt, gold_version="g", gold_content_sha256="h")
    with pytest.raises(ComparisonError, match="items leak"):
        emit_items_file(_corpus(tmp_path), tmp_path / "items.jsonl", leak_manifest=manifest, salt=salt)
    run = _run("folio-resolve", "candidate", {"one": set()})
    run = replace(run, stages={"one": {"note": "Alpha beta"}})
    with pytest.raises(ComparisonError, match="stage snapshot leak"):
        write_stage_snapshots([run], tmp_path / "stages", leak_manifest=manifest, salt=salt)


def test_duplicate_snapshots_rejected_before_write(tmp_path: Path) -> None:
    run = _run("folio-resolve", "candidate", {"one": set()})
    with pytest.raises(StackContractError, match="duplicate"):
        write_stage_snapshots([run, run], tmp_path / "stages")
    assert not (tmp_path / "stages").exists()


def test_write_comparison_leakchecks_every_string(tmp_path: Path) -> None:
    salt = b"0123456789abcdef"
    manifest = build_manifest(
        ["Secret Surface"], salt=salt, gold_version="g", gold_content_sha256="h"
    )
    clean = {"kind": "synthetic_comparison", "note": "safe"}
    assert write_comparison(
        tmp_path / "clean.json", clean, leak_manifest=manifest, salt=salt
    ).exists()
    with pytest.raises(Exception, match="leak"):
        write_comparison(
            tmp_path / "bad.json",
            {**clean, "note": "Secret Surface"},
            leak_manifest=manifest,
            salt=salt,
        )


def test_build_comparison_records_pilot_and_iri_sets(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    runs = [
        _run("folio-resolve", "candidate", {"one": {"iri:a"}, "none": set()}),
        _run("folio-mapper", "incumbent", {"one": set(), "none": set()}),
    ]
    result = build_comparison(corpus, runs, AnswerRuleConfig(), limit=1, n_resamples=100)
    assert result["pilot"] is True
    assert result["scoreable_items"] == 1
    assert result["stacks"]["folio-resolve:candidate"]["items"] == {"one": ["iri:a"]}
    assert result["verdicts"]["folio-mapper"]["verdict"] == "win"


def test_build_comparison_config_hash_mismatch_raises(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    runs = [_run("folio-resolve", "candidate", {"one": {"iri:a"}, "two": {"iri:b"}, "none": set()})]
    with pytest.raises(Exception, match="answer_rule_config_sha256"):
        build_comparison(corpus, runs, AnswerRuleConfig(threshold=0.9))


def test_missing_item_is_stack_contract_error(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    run = _run("folio-resolve", "candidate", {"one": {"iri:a"}, "none": set()})
    with pytest.raises(StackContractError, match="omitted scoreable item"):
        build_comparison(corpus, [run], AnswerRuleConfig())
