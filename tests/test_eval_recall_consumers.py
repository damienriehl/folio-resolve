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
 stages=({'stage0_prescan':['text'],'stage1_filter':1,'stage1b_expand':1,'embedding_rerank':1,'stage3_judge':{'judged':1},'committed':[iri]} if a.llm_on else {'stage1_filter':[iri],'embedding_rerank':[iri],'committed':[iri]}) if stack=='folio-mapper' else {k:[iri] for k in (['entity_ruler','llm_concept'] if a.llm_on else ['EntityRuler','Reconciliation','Resolution','StringMatch'])}
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
    arm = arms_for([spec])[1]
    control = spec.repo_root / "control.json"
    control.write_text(json.dumps({"fail_item": "7"}))
    kwargs = dict(
        arms=[arm],
        items_path=items_file(tmp_path),
        scoreable_ids=[str(i) for i in range(12)],
        local_dir=tmp_path / "state",
        batch_size=2,
        prices=fake_prices(),
        bounds={arm.key: consumers.TokenBound(100, 100)},
        prepare=False,
    )
    with pytest.raises(ConsumerRunError) as error:
        consumers.run_consumer_campaign(**kwargs)
    assert "useful tail" in str(error.value)
    assert all(k not in str(error.value) for k in runner[1].values())
    assert len(list((tmp_path / "state").glob("**/batch-*.json"))) == 2
    control.unlink()
    result = consumers.run_consumer_campaign(**kwargs)
    calls = [json.loads(x) for x in (spec.repo_root / "calls.jsonl").read_text().splitlines()]
    assert calls[:4] == [[str(i) for i in range(5)], ["5", "6"], ["7", "8"], ["7", "8"]]
    assert result["projection_usd"] == "0.0024"
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            assert all(k not in path.read_text() for k in runner[1].values())


@pytest.mark.parametrize("projection,blocked", [("31", True), ("12", False)])
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
        bounds={a.key: consumers.TokenBound(token_count, 0) for a in arms},
        prepare=False,
    )
    if blocked:
        with pytest.raises(consumers.SpendLimitError, match="31"):
            consumers.run_consumer_campaign(**kwargs)
        for spec in runner[0]:
            assert len((spec.repo_root / "calls.jsonl").read_text().splitlines()) == 2
    else:
        assert Decimal(consumers.run_consumer_campaign(**kwargs)["projection_usd"]) == 12


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
    arm = arms_for([spec])[1]
    (spec.repo_root / "control.json").write_text(json.dumps({"fail_item": "25"}))
    kwargs = dict(
        arms=[arm],
        items_path=items_file(tmp_path, 100),
        scoreable_ids=[str(i) for i in range(100)],
        local_dir=tmp_path / "state",
        batch_size=20,
        prices=fake_prices(),
        bounds={arm.key: consumers.TokenBound(120000, 0)},
        prepare=False,
    )
    for _ in range(9):
        with pytest.raises(ConsumerRunError):
            consumers.run_consumer_campaign(**kwargs)
    calls = (spec.repo_root / "calls.jsonl").read_text()
    with pytest.raises(consumers.SpendLimitError, match="next batch"):
        consumers.run_consumer_campaign(**kwargs)
    assert (spec.repo_root / "calls.jsonl").read_text() == calls
    assert (
        Decimal(json.loads((tmp_path / "state/projection.json").read_text())["projection_usd"])
        == 12
    )
    assert consumers.SpendGuard(tmp_path / "state/spend.json").spent == Decimal("24.6")


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
        bounds={arm.key: consumers.TokenBound(100, 100)},
        prices=fake_prices(),
        prepare=False,
    )
    captured = capsys.readouterr()
    outputs = repr(result) + captured.out + captured.err
    outputs += "".join(p.read_text() for p in (tmp_path / "state").rglob("*") if p.is_file())
    assert "[REDACTED]" in outputs
    assert all(key not in outputs for key in runner[1].values())
