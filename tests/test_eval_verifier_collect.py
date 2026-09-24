"""Collector tests deliberately use fake processes only."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.verifier import load_collection
from folio_eval.verifier_collect import (
    CodexRunner,
    RunnerReply,
    collect,
    parse_events,
    render_prompt,
    write_collection,
)
from test_eval_verifier import collection, corpus

TEMPLATE = 'Judge tags.\n{passage}\n{candidates}'
CONCEPTS = {'wrong': ('Other', 'Another concept'), 'gold': ('Correct', 'A concept')}


def events(text: str, extra: list[dict] | None = None) -> str:
    rows = [{'type': 'thread.started', 'model': 'reported-model'}]
    rows += extra or []
    rows += [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
             {'type': 'turn.completed', 'usage': {}}]
    return '\n'.join(json.dumps(row) for row in rows)


class Fake:
    def __init__(self, bad: str | None = None, extra: list[dict] | None = None):
        self.calls = 0
        self.bad = bad
        self.extra = extra

    def __call__(self, prompt: str, cwd: Path) -> RunnerReply:
        assert cwd.is_dir() and not list(cwd.iterdir())
        assert not cwd.is_relative_to(Path.cwd())
        assert 'http' not in prompt
        self.calls += 1
        text = self.bad or json.dumps({'no_match_p': 0.2, 'candidates': {'c01': .1, 'c02': .9}})
        return RunnerReply(events(text, self.extra))


def run(tmp_path: Path, runner: Fake, **kwargs):
    c = corpus()
    return collect(collection(c), c, CONCEPTS, TEMPLATE, runner,
                   checkpoint=tmp_path / 'checkpoint', runner_identity='fake-v1', **kwargs)


def test_mapping_shuffle_resume(tmp_path):
    prompt, handles = render_prompt(TEMPLATE, 's0', 'public text', ('wrong', 'gold'), CONCEPTS)
    assert list(handles.values()) != ['wrong', 'gold']
    assert render_prompt(TEMPLATE, 's0', 'public text', ('wrong', 'gold'), CONCEPTS) == (prompt, handles)
    fake = Fake()
    result = run(tmp_path, fake)
    assert result.model_id == 'reported-model'
    assert {p.iri: p.p for p in result.decisions[0].candidates} == {
        handles['c01']: .1, handles['c02']: .9}
    assert fake.calls == 15
    assert run(tmp_path, Fake()).to_json() == result.to_json()
    resumed = Fake()
    run(tmp_path, resumed)
    assert resumed.calls == 0
    result.validate(corpus())


@pytest.mark.parametrize('bad', [
    '{"no_match_p":0,"candidates":{"c01":0.1}}',
    '{"no_match_p":0,"candidates":{"c01":2,"c02":0}}',
    '{"no_match_p":true,"candidates":{"c01":0,"c02":0}}',
    'not json',
])
def test_three_bad_replies_fail_and_continue(tmp_path, bad):
    fake = Fake(bad)
    result = run(tmp_path, fake)
    assert fake.calls == 45
    assert all(d.state == 'failed' and d.no_match_p is None and not d.candidates
               for d in result.decisions)


@pytest.mark.parametrize('kind', ['command_execution', 'mcp_tool_call', 'web_search', 'file_change'])
def test_tool_events_rejected(tmp_path, kind):
    fake = Fake(extra=[{'type': 'item.started', 'item': {'type': kind, 'command': 'cat gold'}}])
    result = run(tmp_path, fake)
    assert fake.calls == 45
    assert all(d.state == 'failed' for d in result.decisions)


@pytest.mark.parametrize('surface', ['https://folio.org/id', 'urn:folio:concept'])
def test_prompt_refuses_iri(surface):
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', surface, ('wrong',), CONCEPTS)
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', 'text', ('wrong',), {'wrong': ('Label', surface)})


def test_real_runner_argv_fake_subprocess(tmp_path, monkeypatch):
    def fake_process(argv, **kwargs):
        cwd = Path(kwargs['cwd'])
        assert cwd == Path(argv[argv.index('-C') + 1])
        assert not list(cwd.iterdir()) and not cwd.is_relative_to(Path.cwd())
        assert argv == ['codex', 'exec', '-C', str(cwd), '--skip-git-repo-check',
                        '--ephemeral', '--sandbox', 'read-only', '--json',
                        '--ignore-user-config', '--model', 'requested-model', '-']
        assert kwargs['input'] == 'prompt'
        return subprocess.CompletedProcess(argv, 0, events('{}'), '')
    monkeypatch.setattr(subprocess, 'run', fake_process)
    reply = CodexRunner('requested-model')('prompt', tmp_path)
    assert parse_events(reply)[0] == 'reported-model'


def test_missing_reported_model_refused():
    with pytest.raises(ValueError, match='model'):
        parse_events(RunnerReply('{"type":"turn.completed"}'))


def test_checkpoint_input_change_rejected(tmp_path):
    run(tmp_path, Fake())
    with pytest.raises(ValueError, match='fingerprint'):
        collect(collection(corpus()), corpus(), CONCEPTS, TEMPLATE + 'changed', Fake(),
                checkpoint=tmp_path / 'checkpoint', runner_identity='fake-v1')


def test_leak_gate_before_write(tmp_path):
    c = corpus()
    result = run(tmp_path, Fake())
    salt = b'test salt'
    manifest = build_manifest(['private company'], salt, gold_version='gold_v1',
                              gold_content_sha256='a' * 64,
                              scrypt_params=ScryptParams(n=16, r=1, p=1, dklen=16, test_params=True))
    path = tmp_path / 'result.json'
    with pytest.raises(ValueError, match='collision'):
        write_collection(path, replace(result, arm_name='private company'), c, manifest, salt)
    assert not path.exists()
    write_collection(path, result, c, manifest, salt)
    assert load_collection(path, c) == result


def test_smoke_limit_then_resume(tmp_path):
    fake = Fake()
    assert run(tmp_path, fake, limit=2) is None
    assert fake.calls == 2
    resumed = Fake()
    result = run(tmp_path, resumed)
    assert resumed.calls == 13
    assert len(result.decisions) == 15


def test_retry_recovers(tmp_path):
    class Recover(Fake):
        def __call__(self, prompt, cwd):
            self.bad = 'bad' if self.calls == 0 else None
            return super().__call__(prompt, cwd)
    fake = Recover()
    result = run(tmp_path, fake)
    assert fake.calls == 16
    assert all(d.state == 'decided' for d in result.decisions)


def test_real_template_and_iri_mapping(tmp_path):
    template = Path('eval/synthetic/verifier/prompt_template_v1.md').read_text()
    concepts = {'https://folio.org/one': ('One', 'First concept'),
                'https://folio.org/two': ('Two', 'Second concept')}
    prompt, handles = render_prompt(template, 'item', 'public passage', tuple(concepts), concepts)
    assert all(iri not in prompt for iri in concepts)
    assert set(handles.values()) == set(concepts)
    assert '"no_match_p"' in prompt


def test_corrupt_checkpoint_refused(tmp_path):
    run(tmp_path, Fake())
    path = next(p for p in (tmp_path / 'checkpoint').glob('*.json') if p.name != 'manifest.json')
    payload = json.loads(path.read_text())
    payload['decision']['no_match_p'] = .77
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='checksum'):
        run(tmp_path, Fake())


def test_ontology_loader_and_cli_fake(tmp_path, monkeypatch):
    import hashlib

    import run_verifier_collect as cli

    owl = tmp_path / 'ontology.owl'
    owl.write_text('''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:skos="http://www.w3.org/2004/02/skos/core#">
        <rdf:Description rdf:about="wrong"><skos:prefLabel>Other</skos:prefLabel>
        <skos:definition>Another concept</skos:definition></rdf:Description>
        <rdf:Description rdf:about="gold"><skos:prefLabel>Correct</skos:prefLabel>
        <skos:definition>A concept</skos:definition></rdf:Description></rdf:RDF>''')
    digest = hashlib.sha256(owl.read_bytes()).hexdigest()
    assert cli.load_concepts(owl, digest) == CONCEPTS
    with pytest.raises(ValueError, match='hash'):
        cli.load_concepts(owl, 'x' * 64)
    c = corpus()
    c = replace(c, manifest=replace(c.manifest, ontology_cache_sha256=digest))
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps(collection(c).to_json()))
    monkeypatch.setattr(cli, 'load_corpus', lambda _: c)
    salt = tmp_path / 'salt'
    salt.write_bytes(b'test salt')
    manifest = build_manifest(['private company'], salt.read_bytes(), gold_version='gold_v1',
                              gold_content_sha256='a' * 64,
                              scrypt_params=ScryptParams(n=16, r=1, p=1, dklen=16, test_params=True))
    monkeypatch.setattr(cli, 'load_manifest', lambda _: manifest)
    output = tmp_path / 'out.json'
    args = ['--shortlists', str(baseline), '--model', 'fake', '--checkpoint', str(tmp_path / 'cp'),
            '--salt-file', str(salt), '--ontology-cache', str(owl), '--out', str(output)]
    fake = Fake()
    assert cli.main([*args, '--limit', '1'], runner=fake) == 0
    assert fake.calls == 1 and not output.exists()
    assert cli.main(args, runner=fake) == 0
    assert fake.calls == 15
    assert load_collection(output, c).model_id == 'reported-model'
