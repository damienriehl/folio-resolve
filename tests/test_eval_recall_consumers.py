"""Offline boundary tests; fake runners receive only runtime sentinel credentials."""

from __future__ import annotations

import json
import os
import sys
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import pytest
from folio_eval import comparison as comparison
from folio_eval import recall_consumers as consumers
from folio_eval.downstream import ConsumerRunError, ConsumerSpec


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "clean_tree_guard", lambda _: nullcontext())
    monkeypatch.setattr(comparison, "_git_repository_state", lambda _: {"git_sha": "a" * 40})
    monkeypatch.setattr(consumers, "assert_arm_checkout", lambda _: None)
    monkeypatch.setattr(
        comparison,
        "_probe_environment",
        lambda _: {
            "folio_resolve_version": "0.4.0",
            "folio_python_version": "1",
            "folio_resolve_file": "/fake/site-packages/folio_resolve/__init__.py",
        },
    )
    for name in tuple(os.environ):
        if name.endswith(("_API_KEY", "_TOKEN", "_SECRET")):
            monkeypatch.delenv(name)
    keys = {name: "sentinel-" + name.lower() for name in ("GOOGLE_API_KEY", "OPENAI_API_KEY")}
    for name, key in keys.items():
        monkeypatch.setenv(name, key)
    specs = []
    for name, relative in (("folio-enrich", "backend/eval"), ("folio-mapper", "backend/scripts")):
        root = tmp_path / name
        script = root / relative / "synthetic_runner.py"
        script.parent.mkdir(parents=True)
        script.write_text("""import argparse,json,os,pathlib,sys
p=argparse.ArgumentParser()
p.add_argument('--items');p.add_argument('--out');p.add_argument('--lane');p.add_argument('--llm-on',action='store_true')
a=p.parse_args();root=pathlib.Path(__file__).parents[2]
stack=root.name
control=json.loads((root/'control.json').read_text()) if (root/'control.json').exists() else {}
items=[json.loads(x) for x in pathlib.Path(a.items).read_text().splitlines()]
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps([x['item_id'] for x in items])+'\\n')
provider=('google' if any('GOOGLE_API_KEY' in k for k in os.environ) else 'openai') if a.llm_on else None
prefix='FOLIO_ENRICH_' if stack=='folio-enrich' else ''
expected=[prefix+provider.upper()+'_API_KEY'] if provider else []
assert sorted(k for k in os.environ if k.endswith('_API_KEY'))==expected
assert 'PYTHONPATH' not in os.environ
model=os.environ.get('FOLIO_ENRICH_LLM_MODEL' if prefix else 'FOLIO_MAPPER_LLM_MODEL')
assert not a.llm_on or model
if control.get('fail_item') in [x['item_id'] for x in items]:
 print('useful tail '+str([os.environ[k] for k in expected]));sys.exit(7)
config=json.loads((root/'deterministic.json').read_text()) if not a.llm_on else {'llm_provider':provider,'llm_model':model,'llm_on':True}
config.update(control.get('config',{}))
if control.get('echo_key'):
 config['diagnostic']=os.environ[expected[0]]
 print('diagnostic '+config['diagnostic'])
header={'kind':'synthetic-stack-run','stack':stack,'lane':control.get('lane','llm-on' if a.llm_on else 'deterministic'),'folio_resolve_version':'0.4.0','folio_python_version':'1','config':config}
iri='https://folio.openlegalstandard.org/Ra'
records=[header]
for item in items:
 stages=({'stage0_prescan':['text'],'stage1_filter':1,'stage1b_expand':1,'embedding_rerank':1,'stage3_judge':{'judged':1},'committed':[iri]} if a.llm_on else {'stage1_filter':[iri],'embedding_rerank':[iri],'committed':[iri]}) if stack=='folio-mapper' else {k:[iri] for k in (['entity_ruler','llm_concept_identification'] if a.llm_on else ['EntityRuler','Reconciliation','Resolution','StringMatch'])}
 records.append({'item_id':item['item_id'],'iris':[iri],'stages':stages})
pathlib.Path(a.out).write_text(''.join(json.dumps(x)+'\\n' for x in records))
""")
        (root / "deterministic.json").write_text(
            json.dumps(comparison.CONSUMER_DETERMINISTIC_CONFIGS[name])
        )
        specs.append(ConsumerSpec(name, root, Path(sys.executable)))
    return specs, keys


def items_file(tmp_path, count=12):
    path = tmp_path / "items.jsonl"
    path.write_text(
        "".join(
            json.dumps({"item_id": str(i), "text": "Synthetic passage", "segments": ["Synthetic"]})
            + "\n"
            for i in range(count)
        )
    )
    return path


def arms_for(specs):
    return consumers.consumer_arms(specs, mapper_commit="a" * 40)


def fake_prices():
    return {
        model: consumers.ModelPrice(
            Decimal("1"), Decimal("1"), "2026-09-27", "synthetic test price"
        )
        for model in ("gemini-3-flash-preview", "gpt-6-luna")
    }


def test_all_six_arms_isolate_environment_and_preserve_parent(runner, tmp_path):
    specs, keys = runner
    before = dict(os.environ)
    for arm in arms_for(specs):
        run = comparison.run_consumer_stack(
            arm.spec,
            items_file(tmp_path, 1),
            prepare=False,
            llm_provider=arm.provider,
            llm_model=arm.model,
        )
        assert run.config.get("llm_provider") == arm.provider
        output = json.dumps(comparison.write_stage_snapshots([run], tmp_path / arm.key)) + repr(run)
        assert all(key not in output for key in keys.values())
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "provider,model,override",
    [
        ("google", "gemini-3-flash-preview", {"lane": "deterministic"}),
        (None, None, {"lane": "llm-on"}),
        ("google", "gemini-3-flash-preview", {"config": {"llm_provider": "openai"}}),
        ("google", "gemini-3-flash-preview", {"config": {"llm_model": "wrong"}}),
    ],
)
def test_identity_rejected(runner, tmp_path, provider, model, override):
    spec = runner[0][1]
    (spec.repo_root / "control.json").write_text(json.dumps(override))
    with pytest.raises(comparison.StackContractError):
        comparison.run_consumer_stack(
            spec, items_file(tmp_path, 1), prepare=False, llm_provider=provider, llm_model=model
        )


def test_mapper_counts_only_pass_llm_validator(runner, tmp_path):
    spec = runner[0][1]
    run = comparison.run_consumer_stack(
        spec,
        items_file(tmp_path, 1),
        prepare=False,
        llm_provider="google",
        llm_model="gemini-3-flash-preview",
    )
    with pytest.raises(comparison.StackContractError):
        comparison._assert_consumer_rows(run, ["0"])


def test_failed_batch_resumes_and_scrubs_tail(runner, tmp_path):
    spec = runner[0][0]
    control = spec.repo_root / "control.json"
    control.write_text(json.dumps({"fail_item": "7"}))
    kwargs = dict(
        arms=[a for a in arms_for(runner[0]) if a.provider],
        items_path=items_file(tmp_path),
        scoreable_ids=[str(i) for i in range(12)],
        local_dir=tmp_path / "state",
        batch_size=2,
        prices=fake_prices(),
        bounds={
            a.key: consumers.TokenBound(25000, 15000) for a in arms_for(runner[0]) if a.provider
        },
        prepare=False,
    )
    with pytest.raises(ConsumerRunError) as error:
        consumers.run_consumer_campaign(**kwargs)
    assert "useful tail" in str(error.value)
    assert all(k not in str(error.value) for k in runner[1].values())
    assert len(list((tmp_path / "state").glob("**/batch-*.json"))) == 5
    control.unlink()
    result = consumers.run_consumer_campaign(**kwargs)
    calls = [json.loads(x) for x in (spec.repo_root / "calls.jsonl").read_text().splitlines()]
    assert calls[:5] == [[str(i) for i in range(5)]] * 2 + [["5", "6"], ["7", "8"], ["7", "8"]]
    assert result["projection_usd"] == "1.92"
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            assert all(k not in path.read_text() for k in runner[1].values())


@pytest.mark.parametrize("projection,blocked", [("31", True), ("16", False)])
def test_canary_gate(runner, tmp_path, projection, blocked):
    arms = [a for a in arms_for(runner[0]) if a.provider]
    # 100 passages, four paid arms; conservative per-item token bound.
    token_count = int(Decimal(projection) * 1000000 / 400)
    kwargs = dict(
        arms=arms,
        items_path=items_file(tmp_path, 100),
        scoreable_ids=[str(i) for i in range(100)],
        local_dir=tmp_path / "state",
        batch_size=20,
        prices=fake_prices(),
        bounds={a.key: consumers.TokenBound(token_count - 15000, 15000) for a in arms},
        prepare=False,
    )
    if blocked:
        with pytest.raises(consumers.SpendLimitError, match="31"):
            consumers.run_consumer_campaign(**kwargs)
        for spec in runner[0]:
            assert len((spec.repo_root / "calls.jsonl").read_text().splitlines()) == 2
    else:
        assert Decimal(consumers.run_consumer_campaign(**kwargs)["projection_usd"]) == 16


def test_guard_reserves_failed_attempts_and_rejects_before_launch(tmp_path):
    guard = consumers.SpendGuard(tmp_path / "ledger.json")
    guard.reserve(Decimal("12"))
    guard = consumers.SpendGuard(tmp_path / "ledger.json")
    with pytest.raises(consumers.SpendLimitError):
        guard.reserve(Decimal("14"))
    assert guard.spent == 12
    guard.reserve(Decimal("13"))
    with pytest.raises(consumers.SpendLimitError):
        guard.reserve(Decimal("0.01"))


def test_missing_price_fails_before_calls(runner, tmp_path):
    arms = arms_for(runner[0])
    with pytest.raises(consumers.PriceUnavailable):
        consumers.run_consumer_campaign(
            arms=arms,
            items_path=items_file(tmp_path),
            scoreable_ids=[str(i) for i in range(12)],
            local_dir=tmp_path / "state",
            prices={},
            bounds={},
            prepare=False,
        )
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])


def test_failed_batches_exhaust_cap_without_launching_next(runner, tmp_path):
    spec = runner[0][0]
    (spec.repo_root / "control.json").write_text(json.dumps({"fail_item": "25"}))
    arms = [a for a in arms_for(runner[0]) if a.provider]
    kwargs = dict(
        arms=arms,
        items_path=items_file(tmp_path, 100),
        scoreable_ids=[str(i) for i in range(100)],
        local_dir=tmp_path / "state",
        batch_size=20,
        prices=fake_prices(),
        bounds={a.key: consumers.TokenBound(25000, 15000) for a in arms},
        prepare=False,
    )
    for _ in range(12):
        with pytest.raises(ConsumerRunError):
            consumers.run_consumer_campaign(**kwargs)
    calls = (spec.repo_root / "calls.jsonl").read_text()
    with pytest.raises(consumers.SpendLimitError, match="ledger projects"):
        consumers.run_consumer_campaign(**kwargs)
    assert (spec.repo_root / "calls.jsonl").read_text() == calls
    assert consumers.SpendGuard(tmp_path / "state/spend.json").spent == Decimal("11.2")


def test_resume_rejects_changed_input(runner, tmp_path):
    arm = arms_for([runner[0][0]])[0]
    path = items_file(tmp_path)
    kwargs = dict(
        arms=[arm],
        items_path=path,
        scoreable_ids=[],
        local_dir=tmp_path / "state",
        bounds={},
        prepare=False,
    )
    consumers.run_consumer_campaign(**kwargs)
    path.write_text(path.read_text().replace("Synthetic passage", "Changed passage"))
    with pytest.raises(comparison.ComparisonError, match="checkpoint identity"):
        consumers.run_consumer_campaign(**kwargs)


def test_arm_rejects_unapproved_model_and_unsafe_pin(runner):
    with pytest.raises(comparison.ComparisonError):
        consumers.ConsumerArm(runner[0][0], "", "google", "../../escape")


def test_successful_runner_diagnostics_never_escape(runner, tmp_path, capsys):
    spec = runner[0][1]
    (spec.repo_root / "control.json").write_text(json.dumps({"echo_key": True}))
    arm = arms_for([spec])[1]
    result = consumers.run_consumer_campaign(
        arms=[arm],
        items_path=items_file(tmp_path),
        scoreable_ids=[str(i) for i in range(12)],
        local_dir=tmp_path / "state",
        bounds={arm.key: consumers.TokenBound(4000, 4500)},
        canary_only=True,
        prices=fake_prices(),
        prepare=False,
    )
    captured = capsys.readouterr()
    outputs = repr(result) + captured.out + captured.err
    outputs += "".join(p.read_text() for p in (tmp_path / "state").rglob("*") if p.is_file())
    assert "[REDACTED]" in outputs
    assert all(key not in outputs for key in runner[1].values())


def attribution_run(stack, stages, rows, lane="deterministic"):
    return comparison.StackRun(stack, lane, "test", "test", {}, rows, stages)


def attribute_fixture(run, gold, *, nomatch_ids=(), resolve_stages=None, votes=None):
    return consumers.attribute_consumer(
        run,
        gold,
        resolved_votes=votes or {k: [dict.fromkeys(v, 1.0)] * 2 for k, v in gold.items()},
        resolve_attribution={
            "relations": [
                {
                    "item_id": k,
                    "iri": iri,
                    "stage": (resolve_stages or {}).get(iri, "never_produced"),
                }
                for k, values in gold.items()
                for iri in values
            ]
        },
        nomatch_ids=nomatch_ids,
    )


def test_mapper_first_loss_and_resolve_cross_tab():
    run = attribution_run(
        "folio-mapper",
        {
            "a": {
                "committed": ["kept"],
                "embedding_rerank": ["kept"],
                "stage1_filter": ["lost", "kept"],
            }
        },
        {"a": frozenset({"kept"})},
    )
    result = attribute_fixture(run, {"a": {"lost", "kept", "absent"}})
    assert {r["iri"]: r["stage"] for r in result["relations"]} == {
        "lost": "embedding_rerank",
        "kept": "committed",
        "absent": "never_produced",
    }
    assert result["resolve_misses"]["by_stage"]["never_produced"] == {
        "count": 3,
        "produced": 2,
        "committed": 1,
        "produced_unknown": 0,
    }
    assert result["overall"]["embedding_rerank"]["by_agreement"] == {"2": 1, "3": 0}


def test_mapper_llm_counts_do_not_invent_produced_sets():
    run = attribution_run(
        "folio-mapper",
        {
            "a": {
                "stage1_filter": 15,
                "embedding_rerank": 8,
                "stage3_judge": {"judged": 8},
                "committed": ["kept"],
            }
        },
        {"a": frozenset({"kept"})},
        "llm-on",
    )
    result = attribute_fixture(run, {"a": {"kept", "lost"}})
    assert {r["stage"] for r in result["relations"]} == {"committed", "not_committed"}
    assert result["resolve_misses"]["by_stage"]["never_produced"] == {
        "count": 2,
        "produced": 1,
        "committed": 1,
        "produced_unknown": 1,
    }


def test_enrich_order_never_produced_late_appearance_and_final_loss():
    run = attribution_run(
        "folio-enrich",
        {
            "a": {
                "EntityRuler": ["lost"],
                "Reconciliation": ["late"],
                "Resolution": ["late"],
                "StringMatch": ["lost", "late"],
            }
        },
        {"a": frozenset()},
    )
    result = attribute_fixture(run, {"a": {"lost", "late", "absent"}})
    assert {r["iri"]: r["stage"] for r in result["relations"]} == {
        "lost": "Reconciliation",
        "late": "final_output",
        "absent": "never_produced",
    }


def test_enrich_llm_reads_runner_stage_order_and_keeps_first_loss():
    run = attribution_run(
        "folio-enrich",
        {"a": {"resolution": ["x"], "contextual_rerank": [], "string_matching": ["x"]}},
        {"a": frozenset({"x"})},
        "llm-on",
    )
    result = attribute_fixture(run, {"a": {"x"}}, votes={"a": [{"x": 1.0}] * 3})
    assert result["relations"][0]["stage"] == "contextual_rerank"
    assert result["overall"]["contextual_rerank"]["by_agreement"] == {"2": 0, "3": 1}
    assert result["metrics"]["recall"] == 1.0


def test_arm_micro_hand_count_and_nomatch_rate():
    rows = {
        "a": frozenset({"x", "wrong"}),
        "b": frozenset({"y"}),
        "c": frozenset(),
        "n1": frozenset({"wrong", "another"}),
        "n2": frozenset(),
    }
    run = attribution_run(
        "folio-mapper",
        {
            k: {"stage1_filter": list(v), "embedding_rerank": list(v), "committed": list(v)}
            for k, v in rows.items()
        },
        rows,
    )
    result = attribute_fixture(
        run,
        {"a": {"x", "missing"}, "b": {"y"}, "c": {"z"}},
        nomatch_ids=("n1", "n2"),
        resolve_stages={"x": "top_100", "y": "blocklist"},
    )
    m = result["metrics"]
    assert (m["tp"], m["fp"], m["fn"]) == (2, 1, 2)
    assert (m["precision"], m["recall"], m["f1"]) == (0.666667, 0.5, 0.571429)
    assert (m["nomatch_false_positives"], m["nomatch_fp_rate"]) == (1, 0.5)
    assert len(result["resolve_misses"]["relations"]) == 3
    assert result["resolve_misses"]["by_stage"]["blocklist"]["committed"] == 1


@pytest.mark.parametrize("votes", [[{"x": 1.0}], [{"x": 1.0}] * 4])
def test_arm_agreement_fails_closed(votes):
    run = attribution_run(
        "folio-enrich",
        {"a": {k: [] for k in ("EntityRuler", "StringMatch", "Reconciliation", "Resolution")}},
        {"a": frozenset()},
    )
    with pytest.raises(ValueError, match="votes"):
        attribute_fixture(run, {"a": {"x"}}, votes={"a": votes})


def test_pinned_model_prices_and_unknown_fail_closed():
    for model, inp, out, date, source in [
        (
            "gemini-3-flash-preview",
            "0.50",
            "3.00",
            "2026-09-24",
            "https://ai.google.dev/gemini-api/docs/pricing",
        ),
        (
            "gpt-6-luna",
            "0.10",
            "0.50",
            "2026-09-27",
            "https://developers.openai.com/api/docs/models/gpt-6-luna",
        ),
    ]:
        price = consumers.pinned_price(model)
        assert (price.input_per_million, price.output_per_million) == (Decimal(inp), Decimal(out))
        assert (price.date, price.source) == (date, source)
        assert consumers.TokenBound(1000000, 1000000).cost(price) == Decimal(inp) + Decimal(out)
    with pytest.raises(consumers.PriceUnavailable):
        consumers.pinned_price("unknown-model")


def test_arm_scores_reproduce_committed_pilot():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "eval/reports/synthetic-comparison-v1.json").read_text())
    corpus = [
        json.loads(line)
        for line in (root / "eval/synthetic/corpus_v1.jsonl").read_text().splitlines()
    ]
    nomatch = [
        json.loads(line)["item_id"]
        for line in (root / "eval/synthetic/nomatch_v1.jsonl").read_text().splitlines()
    ]
    for stack in ("folio-enrich", "folio-mapper"):
        pilot = report["stacks"][stack + ":incumbent"]
        run = attribution_run(
            stack,
            pilot["stage_snapshot"]["by_item"],
            {k: frozenset(v) for k, v in pilot["items"].items()},
        )
        gold = {r["item_id"]: set(r["gold_iris"]) for r in corpus if r["item_id"] in run.rows}
        result = attribute_fixture(run, gold, nomatch_ids=[k for k in nomatch if k in run.rows])
        assert result["metrics"] == pilot["metrics"]


def test_mapper_final_loss_is_not_labeled_committed():
    run = attribution_run(
        "folio-mapper",
        {"a": {"stage1_filter": ["x"], "embedding_rerank": ["x"], "committed": []}},
        {"a": frozenset()},
    )
    result = attribute_fixture(run, {"a": {"x"}})
    assert result["relations"][0]["stage"] == "final_output"


def test_persisted_enrich_llm_keeps_runner_stage_order():
    run = attribution_run(
        "folio-enrich",
        {"a": {"resolution": ["x"], "contextual_rerank": []}},
        {"a": frozenset()},
        "llm-on",
    )
    payload = json.loads(json.dumps(consumers._serialize_run(run), sort_keys=True))
    restored = consumers._load_run(payload)
    assert tuple(restored.stages["a"]) == ("resolution", "contextual_rerank")
    assert attribute_fixture(restored, {"a": {"x"}})["relations"][0]["stage"] == "contextual_rerank"


def test_consumer_uses_u2_resolved_votes():
    from folio_eval.grade import GraderVote
    from folio_eval.recall_attribution import resolve_grader_votes
    from folio_eval.resolve_labels import IndexedConcept, LabelIndex

    dictionary = LabelIndex.from_concepts([IndexedConcept("x", ("Alpha",), ("Alternate",))])
    votes = resolve_grader_votes(
        [
            GraderVote("a", str(i), str(i), labels, "gen")
            for i, labels in enumerate(
                [{"Alpha": 0.95, "Alternate": 0.2}, {"Alternate": 0.95}, {"Alpha": 0.1}]
            )
        ],
        dictionary,
    )
    run = attribution_run(
        "folio-mapper",
        {"a": {"stage1_filter": ["x"], "embedding_rerank": [], "committed": []}},
        {"a": frozenset()},
    )
    result = attribute_fixture(run, {"a": {"x"}}, votes=votes)
    assert result["overall"]["embedding_rerank"]["by_agreement"] == {"2": 1, "3": 0}


@pytest.mark.parametrize(
    "fault", ["missing_item", "missing_stage", "wrong_committed", "bad_baseline"]
)
def test_consumer_incomplete_evidence_fails_closed(fault):
    run = attribution_run(
        "folio-mapper",
        {"a": {"stage1_filter": ["x"], "embedding_rerank": ["x"], "committed": ["x"]}},
        {"a": frozenset({"x"})},
    )
    if fault == "missing_item":
        run.rows.clear()
    elif fault == "missing_stage":
        del run.stages["a"]["stage1_filter"]
    elif fault == "wrong_committed":
        run.stages["a"]["committed"] = []
    with pytest.raises(ValueError):
        attribute_fixture(
            run, {"a": {"x"}}, resolve_stages={"x": "invalid"} if fault == "bad_baseline" else None
        )


@pytest.fixture
def cli_fixture(runner, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from folio_eval.resolve_labels import IndexedConcept, LabelIndex
    from folio_eval.synthesize import LoadedCorpus, SyntheticItem

    iri = "https://folio.openlegalstandard.org/Ra"
    items = tuple(
        SyntheticItem(
            str(i),
            "contract",
            "US",
            "Private passage sentinel",
            ("Private label sentinel",),
            frozenset({iri}),
            "human",
            {
                "grader_votes": [
                    dict(
                        item_id=str(i),
                        grader_id=str(j),
                        model_family=str(j),
                        concepts={"Private label sentinel": 0.95},
                        generator_id_claimed="gen",
                    )
                    for j in range(3)
                ]
            },
        )
        for i in range(7)
    )
    corpus = LoadedCorpus(
        SimpleNamespace(
            scoreable=True,
            content_sha256="c" * 64,
            nomatch_content_sha256="n" * 64,
            ontology_cache_sha256="o" * 64,
        ),
        items,
        (
            SyntheticItem(
                "negative",
                "contract",
                "US",
                "Private passage sentinel",
                provenance={"no_match": True},
            ),
        ),
    )
    monkeypatch.setattr(consumers, "load_corpus", lambda _: corpus)
    dictionary = LabelIndex.from_concepts([IndexedConcept(iri, ("Private label sentinel",), ())])
    monkeypatch.setattr(consumers, "assert_ontology_pin", lambda _: None)
    monkeypatch.setattr(consumers, "load_folio_index", lambda: (dictionary, "o" * 64, None))
    monkeypatch.setattr(consumers, "enrich_spec", lambda _: runner[0][0])
    monkeypatch.setattr(consumers, "mapper_spec", lambda _: runner[0][1])
    from folio_eval.leakcheck import build_manifest, scan_json_value

    manifest = build_manifest(
        ["Private passage sentinel", "Private label sentinel"],
        b"test salt",
        gold_version="test",
        gold_content_sha256="a" * 64,
    )
    monkeypatch.setattr(consumers, "load_manifest", lambda _: manifest)
    scans = []

    def scan(value, manifest, salt):
        scans.append(value)
        return scan_json_value(value, manifest, salt)

    monkeypatch.setattr(consumers, "scan_json_value", scan)
    baseline = tmp_path / "attribution.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fingerprint": {
                    "corpus_content_sha256": "c" * 64,
                    "nomatch_content_sha256": "n" * 64,
                    "ontology_cache_sha256": "o" * 64,
                },
                "relations": [
                    dict(item_id=str(i), iri=iri, stage="never_produced") for i in range(7)
                ],
            }
        )
    )
    salt = tmp_path / "salt"
    salt.write_bytes(b"test salt")
    output = tmp_path / "output.json"
    args = [
        "--corpus-manifest",
        str(tmp_path / "manifest.json"),
        "--enrich-checkout",
        str(runner[0][0].repo_root),
        "--mapper-checkout",
        str(runner[0][1].repo_root),
        "--mapper-commit",
        "a" * 40,
        "--campaign-dir",
        str(tmp_path / "campaign"),
        "--attribution",
        str(baseline),
        "--attribution-sha256",
        consumers.sha256_text(baseline.read_text()),
        "--leak-manifest",
        str(tmp_path / "leak.json"),
        "--salt-file",
        str(salt),
        "--output",
        str(output),
    ]
    return args, output, scans


def test_cli_deterministic_only_needs_no_bounds(cli_fixture, runner, capsys):
    args, output, scans = cli_fixture
    assert consumers.main([*args, "--arms", "enrich:deterministic,mapper:deterministic"]) == 0
    report = json.loads(output.read_text())
    assert set(report["arms"]) == {"folio-enrich-deterministic", "folio-mapper-deterministic"}
    assert all(value["metrics"]["recall"] == 1 for value in report["arms"].values())
    assert all("relations" not in value for value in report["arms"].values())
    assert "Private passage sentinel" not in output.read_text()
    assert "Private label sentinel" not in output.read_text()
    assert scans[-1] == report
    assert output.with_suffix(".json.sha256").read_text().strip() == consumers.sha256_text(
        output.read_text()
    )
    assert "Reserved spend total: $0" in capsys.readouterr().out
    for spec in runner[0]:
        assert len(spec.repo_root.joinpath("calls.jsonl").read_text().splitlines()) == 2


def test_cli_paid_without_bounds_rejected_before_launch(cli_fixture, runner):
    args, _, _ = cli_fixture
    with pytest.raises(SystemExit):
        consumers.main([*args, "--arms", "enrich:gemini-3-flash-preview"])
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])


def test_cli_canary_only_and_resume(cli_fixture, runner, capsys):
    args, output, _ = cli_fixture
    paid = [
        "--arms",
        ALL_PAID_SELECTORS,
        *paid_bounds_cli(),
    ]
    assert consumers.main([*args, *paid, "--canary-only"]) == 0
    spec = runner[0][1]

    def calls():
        return [
            json.loads(x) for x in spec.repo_root.joinpath("calls.jsonl").read_text().splitlines()
        ]

    assert calls() == [["0"], ["0"]] + [["0", "1", "2", "3", "4"]] * 2
    assert not output.exists()
    assert "projection_usd" in capsys.readouterr().out
    assert consumers.main([*args, "--arms", "mapper:gpt-6-luna", *paid_bounds_cli()]) == 0
    assert calls() == [["0"], ["0"]] + [["0", "1", "2", "3", "4"]] * 2 + [["5", "6", "negative"]]
    assert (
        json.loads(output.read_text())["arms"]["folio-mapper-gpt-6-luna"]["metrics"]["items"] == 7
    )


@pytest.mark.parametrize(
    "surface",
    ["committed", "contextual_rerank", "folio-enrich-gpt-6-luna", "aggregate bound is an estimate"],
)
def test_cli_leak_scan_prevents_output_write(cli_fixture, monkeypatch, runner, surface):
    args, output, _ = cli_fixture
    output.write_text("existing artifact")
    from folio_eval.leakcheck import build_manifest

    manifest = build_manifest(
        [surface], b"test salt", gold_version="test", gold_content_sha256="a" * 64
    )
    monkeypatch.setattr(consumers, "load_manifest", lambda _: manifest)
    with pytest.raises(ValueError, match="leak check"):
        consumers.main([*args, "--arms", "mapper:deterministic"])
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])
    assert output.read_text() == "existing artifact"
    assert not output.with_suffix(".json.sha256").exists()


def test_cli_shared_ledger_blocks_full_run(cli_fixture, runner, capsys, tmp_path):
    args, output, _ = cli_fixture
    consumers.SpendGuard(tmp_path / "campaign/spend.json").reserve(Decimal("24.3"))
    with pytest.raises(consumers.SpendLimitError, match="ledger projects"):
        consumers.main(
            [
                *args,
                "--arms",
                ALL_PAID_SELECTORS,
                *paid_bounds_cli(),
            ]
        )
    calls = runner[0][1].repo_root.joinpath("calls.jsonl").read_text().splitlines()
    assert [len(json.loads(x)) for x in calls] == [1, 1, 5, 5]
    assert "projected_ledger_usd" in capsys.readouterr().out
    assert not output.exists()


def test_cli_attribution_digest_rejected_before_launch(cli_fixture, runner):
    args, _, _ = cli_fixture
    args[args.index("--attribution-sha256") + 1] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        consumers.main([*args, "--arms", "mapper:deterministic"])
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])


def test_cli_all_paid_arms_and_deterministic_share_ledger(cli_fixture, runner, tmp_path):
    args, output, _ = cli_fixture
    consumers.main([*args, "--arms", "enrich:deterministic,mapper:deterministic"])
    output.unlink()  # Aggregation must use campaign state, not the previous output.
    consumers.main(
        [
            *args,
            "--arms",
            "enrich:gpt-6-luna,mapper:gpt-6-luna,enrich:gemini-3-flash-preview,mapper:gemini-3-flash-preview",
            *paid_bounds_cli(),
        ]
    )
    report = json.loads(output.read_text())
    assert len(report["arms"]) == 6
    assert all(report["complete_arms"].values())
    assert report["token_bound_floors"]["folio-enrich"]["code_enforced"] is False
    assert "estimate" in report["token_bound_floors"]["folio-enrich"]["rationale"]
    assert all(value["metrics"]["recall"] == 1 for value in report["arms"].values())
    assert all(value["metrics"]["nomatch_fp_rate"] == 1 for value in report["arms"].values())
    assert consumers.SpendGuard(tmp_path / "campaign/spend.json").spent == Decimal("0.77085")
    for spec in runner[0]:
        assert len(spec.repo_root.joinpath("calls.jsonl").read_text().splitlines()) == 8


@pytest.mark.parametrize("consumer", ["folio-enrich", "folio-mapper"])
def test_review_bounds_below_floor_rejected(runner, tmp_path, consumer):
    arm = next(a for a in arms_for(runner[0]) if a.spec.name == consumer and a.provider)
    with pytest.raises(comparison.ComparisonError, match="floor"):
        consumers.run_consumer_campaign(
            arms=[arm],
            items_path=items_file(tmp_path),
            scoreable_ids=list(map(str, range(12))),
            local_dir=tmp_path / "state",
            bounds={arm.key: consumers.TokenBound(1, 0)},
        )
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])


def test_review_subset_requires_all_arm_projection(runner, tmp_path):
    arm = arms_for(runner[0])[1]
    with pytest.raises(comparison.ComparisonError, match=r"all.*arm projection"):
        consumers.run_consumer_campaign(
            arms=[arm],
            items_path=items_file(tmp_path),
            scoreable_ids=list(map(str, range(12))),
            local_dir=tmp_path / "state",
            bounds={arm.key: consumers.TokenBound(25000, 15000)},
        )
    assert not any(s.repo_root.joinpath("calls.jsonl").exists() for s in runner[0])


def test_review_four_eight_dollar_arms_refused(runner, tmp_path):
    arms = [a for a in arms_for(runner[0]) if a.provider]
    with pytest.raises(consumers.SpendLimitError, match="32"):
        consumers.run_consumer_campaign(
            arms=arms,
            items_path=items_file(tmp_path, 100),
            scoreable_ids=list(map(str, range(100))),
            local_dir=tmp_path / "state",
            prices=fake_prices(),
            bounds={a.key: consumers.TokenBound(65000, 15000) for a in arms},
        )
    for spec in runner[0]:
        assert [
            len(json.loads(row))
            for row in (spec.repo_root / "calls.jsonl").read_text().splitlines()
        ] == [5, 5]


def test_review_enrich_sorted_serialized_shape():
    names = (
        "ingestion",
        "normalization",
        "entity_ruler",
        "llm_concept_identification",
        "early_individual_extraction",
        "early_property_extraction",
        "early_proposition",
        "early_triple",
        "document_type_classification",
        "reconciliation",
        "resolution",
        "contextual_rerank",
        "branch_judge",
        "string_matching",
        "llm_individual_linking",
        "llm_property_linking",
        "triple_enrichment",
        "metadata",
    )
    stages = {name: [] for name in names}
    stages["resolution"] = ["x"]
    raw = json.loads(json.dumps({"item_id": "a", "iris": [], "stages": stages}, sort_keys=True))
    run = attribution_run("folio-enrich", {"a": raw["stages"]}, {"a": frozenset()}, "llm-on")
    assert attribute_fixture(run, {"a": {"x"}})["relations"][0]["stage"] == "contextual_rerank"
    raw["stages"]["unknown_stage"] = []
    with pytest.raises(ValueError, match="unexpected"):
        attribute_fixture(run, {"a": {"x"}})


ALL_PAID_SELECTORS = "enrich:gpt-6-luna,mapper:gpt-6-luna,enrich:gemini-3-flash-preview,mapper:gemini-3-flash-preview"


def paid_bounds_cli():
    return [
        "--enrich-input-token-bound",
        "25000",
        "--enrich-output-token-bound",
        "15000",
        "--mapper-input-token-bound",
        "4000",
        "--mapper-output-token-bound",
        "4500",
    ]


@pytest.mark.parametrize("change", ["bounds", "batch", "pin", "price", "canary"])
def test_projection_rejects_stale_identity(cli_fixture, runner, monkeypatch, tmp_path, change):
    args, _, _ = cli_fixture
    consumers.main([*args, "--arms", ALL_PAID_SELECTORS, *paid_bounds_cli(), "--canary-only"])
    calls = [s.repo_root.joinpath("calls.jsonl").read_text() for s in runner[0]]
    bounds = paid_bounds_cli()
    if change == "bounds":
        bounds[1] = "26000"
    elif change == "batch":
        args += ["--batch-size", "2"]
    elif change == "pin":
        args[args.index("--mapper-commit") + 1] = "b" * 40
    elif change == "price":
        monkeypatch.setitem(
            consumers.PINNED_PRICES,
            "gpt-6-luna",
            consumers.ModelPrice(Decimal("1"), Decimal("1"), "test", "test"),
        )
    else:
        next(
            (tmp_path / "campaign/batches/folio-enrich-gpt-6-luna").glob("batch-00000.json")
        ).unlink()
    with pytest.raises(comparison.ComparisonError):
        consumers.main([*args, "--arms", "mapper:gpt-6-luna", *bounds])
    assert calls == [s.repo_root.joinpath("calls.jsonl").read_text() for s in runner[0]]


def test_full_batches_have_persisted_all_arm_projection(runner, tmp_path, monkeypatch):
    original = consumers.run_consumer_stack
    state = tmp_path / "state"
    observed = []

    def checked(spec, items_path, **kwargs):
        ids = [json.loads(row)["item_id"] for row in items_path.read_text().splitlines()]
        if "5" in ids:
            proof = json.loads((state / "projection.json").read_text())
            assert set(proof["paid_arms"]) == consumers.PAID_ARM_KEYS
            assert Decimal(proof["projected_ledger_usd"]) < consumers.CAP_USD
            observed.append(spec.name)
        return original(spec, items_path, **kwargs)

    monkeypatch.setattr(consumers, "run_consumer_stack", checked)
    arms = [a for a in arms_for(runner[0]) if a.provider]
    consumers.run_consumer_campaign(
        arms=arms,
        items_path=items_file(tmp_path),
        scoreable_ids=list(map(str, range(12))),
        local_dir=state,
        bounds={a.key: consumers.TokenBound(25000, 15000) for a in arms},
    )
    assert len(observed) == 4


def test_completed_arm_survives_later_arm_failure(cli_fixture, runner):
    args, output, _ = cli_fixture
    control = runner[0][1].repo_root / "control.json"
    control.write_text(json.dumps({"fail_item": "0"}))
    with pytest.raises(ConsumerRunError):
        consumers.main([*args, "--arms", "enrich:deterministic,mapper:deterministic"])
    assert not output.exists()
    control.unlink()
    consumers.main([*args, "--arms", "mapper:deterministic"])
    report = json.loads(output.read_text())
    assert set(report["arms"]) == {"folio-enrich-deterministic", "folio-mapper-deterministic"}
    assert sum(report["complete_arms"].values()) == 2
