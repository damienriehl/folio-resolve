"""Offline report rules, artifact binding, and publication gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from folio_eval import recall_embedding_ceiling as embedding
from folio_eval import recall_report as report
from folio_eval.leakcheck import build_manifest, scan_text


def source(stages: dict[str, int]) -> dict[str, Any]:
    rows = [
        dict(
            item_id=f"p{i}",
            iri=f"i{i}",
            stage=stage,
            agreement=3,
            stratum_id="s",
            rank=250 if stage == "rank_below_200" else None,
        )
        for i, stage in enumerate(s for s, n in stages.items() for _ in range(n))
    ]
    counts = {stage: {"count": n, "by_agreement": {"2": 0, "3": n}} for stage, n in stages.items()}
    return {
        "schema_version": 1,
        "relations": rows,
        "gold_relation_count": len(rows),
        "scoreable_item_count": len(rows),
        "overall": counts,
        "by_stratum": {"s": counts},
        "rank_distance_below_200": {"50": stages.get("rank_below_200", 0)},
    }


def ceiling(fraction: float = 0.3) -> dict[str, Any]:
    return {
        "rankings": {
            arm: {
                str(k): {
                    "recovered_count": int(100 * fraction),
                    "non_gold_count": 2,
                    "suggestion_count": 32,
                }
                for k in (10, 25, 50, 100)
            }
            for arm in embedding.ARMS
        },
        "never_produced_count": 100,
        "passages": {},
    }


def app(n: int, lane: str = "deterministic") -> dict[str, Any]:
    return {
        "stack": "folio-mapper",
        "lane": lane,
        "overall": {"committed": {"count": 100, "by_agreement": {"2": 0, "3": 100}}},
        "gold_relation_count": 100,
        "metrics": dict(
            precision=0.2,
            recall=0.3,
            f1=0.24,
            nomatch_fp_rate=0.1,
            gold=100,
            items=100,
            nomatch_items=30,
        ),
        "resolve_misses": {
            "relations": [
                dict(
                    item_id=f"p{i}",
                    iri=f"i{i}",
                    committed=True,
                    produced=True,
                    stage="committed",
                    resolve_stage="never_produced",
                )
                for i in range(n)
            ]
        },
    }


@pytest.mark.parametrize(
    "stages,fraction,lever",
    [
        ({"never_produced": 100}, 0.3, "local_source"),
        ({"never_produced": 100}, 0.2, None),
        ({"rank_below_200": 60, "never_produced": 40}, 0.3, "ranking"),
        ({"short_label_gate": 60, "never_produced": 40}, 0.3, "gate_tuning"),
    ],
)
def test_rule(stages: dict[str, int], fraction: float, lever: str | None) -> None:
    result = report.choose_lever(source(stages), [], ceiling(fraction), {"recovered": 70})
    assert result["lever"] == lever
    assert result["route"] == ("Damien" if lever is None else "brainstorm")


def test_app_bar_and_llm_exclusion() -> None:
    a = source({"never_produced": 100})
    assert report.choose_lever(a, [app(20)], ceiling(), {})["lever"] == "app_stage"
    assert report.choose_lever(a, [app(10)], ceiling(), {})["lever"] == "app_stage"
    assert (
        report.choose_lever(a, [app(9), app(80, "llm-on")], ceiling(), {})["lever"]
        == "local_source"
    )
    assert report.choose_lever(a, [app(80, "llm-on")], ceiling(0.2), {})["route"] == "Damien"


def test_concentration_uses_base_rate_and_item_bootstrap() -> None:
    a = source({"top_100": 100, "never_produced": 100})
    for row in a["relations"][100:]:
        row["agreement"] = 2
    result = report.choose_lever(a, [app(20)], ceiling(), {})
    assert result["route"] == "Damien"
    interval = report.agreement_concentration(a["relations"])
    assert interval["low"] > 0
    assert interval == report.agreement_concentration(a["relations"])
    for row in a["relations"]:
        row["agreement"] = 2
    assert report.agreement_concentration(a["relations"])["low"] == 0
    assert report.choose_lever(a, [], ceiling(), {})["lever"] == "local_source"


def manifest(words: list[str]) -> Any:
    return build_manifest(words, b"fake-salt", gold_version="fake", gold_content_sha256="a" * 64)


def full_apps():
    return [
        dict(app(20), stack=stack, model=model, lane="deterministic" if model is None else "llm-on")
        for stack in ("folio-enrich", "folio-mapper")
        for model in (None, "gemini-3-flash-preview", "gpt-6-luna")
    ]


def payload() -> dict[str, Any]:
    return report.build_report(
        source({"never_produced": 100}),
        full_apps(),
        ceiling(),
        dict(publishable=1, recovered=20, per_item={}, item_count=0, embedding_sha256="b" * 64),
        {"attribution": "a" * 64, "embedding": "b" * 64},
    )


def test_fixed_prose_and_roundtrip(tmp_path: Path) -> None:
    m = manifest(
        [
            "thresholds",
            "benchmarks",
            "boundaries",
            "consumer",
            "execution",
            "exits",
            "fixtures",
            "identity",
            "pipelines",
            "synthetic",
        ]
    )
    p = payload()
    assert scan_text(report.render_markdown(p), m, b"fake-salt") == 0
    report.write_reports(p, tmp_path, m, b"fake-salt")
    assert json.loads((tmp_path / "recall-loss-attribution.json").read_text()) == p
    assert (tmp_path / "recall-loss-attribution.md").read_text() == report.render_markdown(p)


def test_leak_preserves_outputs(tmp_path: Path) -> None:
    p = payload()
    p["decision"]["reason"] = "forbidden phrase"
    for suffix in ("json", "md"):
        (tmp_path / f"recall-loss-attribution.{suffix}").write_text("prior")
    with pytest.raises(ValueError, match=r"inputs are intact.*no recomputation"):
        report.write_reports(p, tmp_path, manifest(["forbidden phrase"]), b"fake-salt")
    assert all(p.read_text() == "prior" for p in tmp_path.iterdir())


def test_sha_binding(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"attribution_sha256": "a" * 64}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert report.load_bound(path, digest, "a" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        report.load_bound(path, "b" * 64, "a" * 64)
    with pytest.raises(ValueError, match="attribution"):
        report.load_bound(path, digest, "b" * 64)


def test_unpublishable_llm_rejected() -> None:
    with pytest.raises(ValueError, match="publishable"):
        report.build_report(source({}), [], ceiling(), {"publishable": 0}, {})


def test_residual_ids_use_global_better_ranking() -> None:
    c = ceiling()
    c["rankings"]["sentence_windows"]["50"]["recovered_count"] = 40
    c["passages"] = {
        item: {
            "never_produced_count": 2,
            "sentence_windows": {"100": {"recovered_count": recovered}},
            "whole_passage": {"100": {"recovered_count": 2}},
        }
        for item, recovered in [("z", 1), ("a", 2), ("b", 0)]
    }
    assert embedding.residual_item_ids(c) == ["b", "z"]


def test_dirty_record_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def dirty(_: Path) -> None:
        raise ValueError("pristine tree")

    monkeypatch.setattr(report, "require_pristine", dirty)
    with pytest.raises(ValueError, match="pristine"):
        report.record_experiment(
            payload(), {}, manifest(["forbidden phrase"]), b"fake-salt", root=tmp_path
        )


def test_record_one_park_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from folio_eval import experiment

    monkeypatch.setattr(report, "require_pristine", lambda _: None)
    monkeypatch.setattr(
        experiment,
        "run_determinism_selftest",
        lambda _: type("Result", (), {"to_json": lambda self: {}})(),
    )
    log, pending = tmp_path / "ledger.jsonl", tmp_path / "pending.json"
    report.record_experiment(
        payload(),
        {"answer_rule_config_sha256": "a" * 64, "ontology_cache_sha256": "b" * 64},
        manifest(["forbidden phrase"]),
        b"fake-salt",
        experiments_log=log,
        pending_path=pending,
    )
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["decision"] == "park"
    assert entries[0]["lever_scope"] == "adapter_only"
    assert not pending.exists()


def test_numeric_app_adapter() -> None:
    result = report.app_arms(
        {
            "arms": {
                "folio-mapper-deterministic": {
                    "resolve_misses": {"by_stage": {"never_produced": {"committed": 20}}}
                }
            }
        }
    )
    assert (
        report.choose_lever(source({"never_produced": 100}), result, ceiling(), {})["lever"]
        == "app_stage"
    )


def test_cli_bound_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    inputs = {
        "attribution": source({"never_produced": 100}),
        "apps": {"arms": full_apps()},
        "embedding": ceiling(),
        "llm": {"publishable": 1, "recovered": 0, "per_item": {}, "item_count": 0},
    }
    args = []
    digest = ""
    for name, value in inputs.items():
        if name == "llm":
            value["embedding_sha256"] = hashlib.sha256(
                (tmp_path / "embedding.json").read_bytes()
            ).hexdigest()
        if name != "attribution":
            value["attribution_sha256"] = digest
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value))
        current_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        args += [f"--{name}", str(path)]
        if name == "attribution":
            digest = current_digest
            Path(str(path) + ".sha256").write_text(digest + "\n")
        else:
            args += [f"--{name}-sha256", current_digest]
    salt = tmp_path / "salt"
    salt.write_bytes(b"fake-salt")
    monkeypatch.setattr(report, "load_manifest", lambda _: manifest(["forbidden phrase"]))
    out = tmp_path / "out"
    args += [
        "--salt-file",
        str(salt),
        "--leak-manifest",
        str(tmp_path / "manifest"),
        "--output-dir",
        str(out),
    ]
    assert report.main(args) == 0
    assert (
        json.loads((out / "recall-loss-attribution.json").read_text())["decision"]["lever"]
        == "app_stage"
    )
    before = (out / "recall-loss-attribution.json").read_bytes()
    (tmp_path / "embedding.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        report.main(args)
    assert (out / "recall-loss-attribution.json").read_bytes() == before


def test_preflight_collision() -> None:
    with pytest.raises(ValueError, match="inputs are intact"):
        report.preflight(manifest(["Recall loss attribution"]), b"fake-salt")


def test_residual_option_writes_exact_json_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from folio_resolve.embedding import HashingEmbeddingProvider

    corpus = SimpleNamespace(
        manifest=SimpleNamespace(
            content_sha256="a" * 64, ontology_cache_sha256="b" * 64, scoreable=True
        ),
        scoreable_items=[],
    )
    a = source({})
    a["fingerprint"] = {"corpus_content_sha256": "a" * 64, "ontology_cache_sha256": "b" * 64}
    path = tmp_path / "attribution.json"
    path.write_text(json.dumps(a))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    c = ceiling()
    c["passages"] = {
        item: {
            "never_produced_count": 2,
            "whole_passage": {"100": {"recovered_count": count}},
            "sentence_windows": {"100": {"recovered_count": 2}},
        }
        for item, count in [("remaining", 1), ("recovered", 2)]
    }
    monkeypatch.setattr(embedding, "load_corpus", lambda _: corpus)
    monkeypatch.setattr(
        embedding, "assert_ontology_pin", lambda _: SimpleNamespace(path=tmp_path, sha256="b" * 64)
    )
    monkeypatch.setattr(
        embedding, "load_folio_index", lambda: (SimpleNamespace(iris=set()), "b" * 64, "fake")
    )
    monkeypatch.setattr(embedding.baseline, "load_corpus", lambda *_: [])
    monkeypatch.setattr(embedding, "load_manifest", lambda _: manifest(["forbidden phrase"]))
    monkeypatch.setattr(
        embedding, "load_local_provider", lambda _: (HashingEmbeddingProvider(), len)
    )
    monkeypatch.setattr(embedding, "measure_ceiling", lambda *_: c)
    salt = tmp_path / "salt"
    salt.write_bytes(b"fake-salt")
    output, ids = tmp_path / "embedding.json", tmp_path / "ids.json"
    args = [
        "--attribution",
        str(path),
        "--attribution-sha256",
        digest,
        "--corpus-manifest",
        str(tmp_path / "corpus"),
        "--model-path",
        str(tmp_path / "model"),
        "--leak-manifest",
        str(tmp_path / "leaks"),
        "--salt-file",
        str(salt),
        "--output",
        str(output),
        "--residual-item-ids",
        str(ids),
    ]
    assert embedding.main(args) == 0
    assert json.loads(ids.read_text()) == ["remaining"]
    assert json.loads(output.read_text())["attribution_sha256"] == digest


@pytest.mark.parametrize("recovered,lever", [(24, None), (25, "local_source")])
def test_local_bar_exact(recovered: int, lever: str | None) -> None:
    assert (
        report.choose_lever(
            source({"never_produced": 100}), [], ceiling(recovered / 100), {"recovered": 80}
        )["lever"]
        == lever
    )


def test_interval_zero_is_not_positive_and_ontology_absent_not_recoverable() -> None:
    a = source({"top_100": 1, "never_produced": 1})
    a["relations"][1]["agreement"] = 2
    assert report.agreement_concentration(a["relations"])["low"] == 0
    assert report.choose_lever(a, [], ceiling(), {})["route"] == "brainstorm"
    assert report.choose_lever(source({"ontology_absent": 100}), [], ceiling(), {})["lever"] is None
    assert (
        report.choose_lever(source({"rank_101_200": 100}), [], ceiling(), {})["lever"] == "ranking"
    )


def test_largest_qualifying_app_wins() -> None:
    enrich = dict(app(30), stack="folio-enrich")
    decision = report.choose_lever(
        source({"never_produced": 100}), [app(20), enrich], ceiling(), {}
    )
    assert decision["app"] == "folio-enrich"


def test_stage_shares_and_distance_are_rendered() -> None:
    p = report.build_report(
        source({"rank_below_200": 60, "never_produced": 40}),
        full_apps(),
        ceiling(),
        {"publishable": 1, "per_item": {}, "item_count": 0, "embedding_sha256": "b" * 64},
        {"embedding": "b" * 64},
    )
    assert p["stages"]["rank_below_200"]["share"] == 0.6
    assert p["stages"]["rank_below_200"]["by_agreement"]["3"]["share"] == 1
    assert "Doc type s" in report.render_markdown(p)
    assert "| 50 | 60 |" in report.render_markdown(p)


def test_review_five_arms_refused():
    arms = [
        dict(app(20), stack=stack, model=model, lane="deterministic" if model is None else "llm-on")
        for stack in ("folio-enrich", "folio-mapper")
        for model in (None, "gemini-3-flash-preview", "gpt-6-luna")
    ]
    with pytest.raises(ValueError, match="six"):
        report.build_report(
            source({"never_produced": 100}),
            arms[:5],
            ceiling(),
            dict(publishable=1, per_item={}),
            {},
        )


def test_review_ae3_precedes_ranking():
    local = ceiling(0.16)
    local["never_produced_count"] = 80
    decision = report.choose_lever(
        source({"never_produced": 80, "rank_below_200": 20}), [], local, {"recovered": 60}
    )
    assert decision["route"] == "Damien"
    assert decision["lever"] is None


@pytest.mark.parametrize("damage", ["duplicate", "partial", "binding", "residual"])
def test_review_publication_gates(damage):
    arms = full_apps()
    llm = dict(publishable=1, per_item={}, item_count=0, embedding_sha256="b" * 64)
    if damage == "duplicate":
        arms[-1] = arms[0]
    elif damage == "partial":
        arms[0]["metrics"]["items"] = 99
    elif damage == "binding":
        llm["embedding_sha256"] = "c" * 64
    else:
        llm["per_item"] = {"stale": {}}
    with pytest.raises(ValueError):
        report.build_report(
            source({"never_produced": 100}), arms, ceiling(), llm, {"embedding": "b" * 64}
        )


@pytest.mark.parametrize(
    "heading",
    [
        "Distance past rank 200",
        "EntityRuler",
        "Paid-arm projection",
        "Non-gold proposals",
        "Local search ceilings",
    ],
)
def test_review_all_fixed_prose_preflight(heading):
    with pytest.raises(ValueError, match="leak check"):
        report.preflight(manifest([heading]), b"fake-salt")
