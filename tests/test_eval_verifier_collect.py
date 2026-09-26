"""Collector tests deliberately use fake processes only."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import folio_eval.verifier_collect as collector
import pytest
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.verifier import load_collection
from folio_eval.verifier_collect import (
    CodexRunner,
    RenderStats,
    RunnerReply,
    collect,
    parse_events,
    render_prompt,
    write_collection,
)
from test_eval_verifier import collection, corpus

TEMPLATE = 'Judge tags.\n{passage}\n{candidates}'
CONCEPTS = {'wrong': ('Other', 'Another concept'), 'gold': ('Correct', 'A concept')}


@pytest.fixture(autouse=True)
def fake_cli_version(monkeypatch):
    monkeypatch.setattr(collector, '_cli_version', lambda: 'codex-cli test-version', raising=False)


def checkpoints(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / 'checkpoint').rglob('*.json')
            if p.name != 'manifest.json']


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
    assert all(p['failure_reasons'] == ['tool_event'] * 3 for p in checkpoints(tmp_path))


@pytest.mark.parametrize('surface', [
    'https://folio.openlegalstandard.org/R123',
    'folio.openlegalstandard.org/R123',
    'http://lmss.sali.org/R123',
    'lmss.sali.org/R123',
    'HTTPS://FOLIO.OPENLEGALSTANDARD.ORG/R123',
])
def test_prompt_refuses_iri(surface):
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', surface, ('wrong',), CONCEPTS)
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', 'text', ('wrong',), {'wrong': ('Label', surface)})


@pytest.mark.parametrize('field', ['label', 'definition'])
def test_prompt_neutralizes_external_links(field):
    text = 'Before https://www.ncsc.org/reference after\nhttp://example.org/page end'
    label, definition = (text, 'Definition') if field == 'label' else ('Label', text)
    prompt, _ = render_prompt(
        TEMPLATE, 'id', 'text', ('wrong',), {'wrong': (label, definition)},
    )
    assert 'Before [link] after\\n[link] end' in prompt
    assert 'https://' not in prompt and 'http://' not in prompt


@pytest.mark.parametrize('surface', [
    'https://folio.openlegalstandard.org/R123',
    'folio.openlegalstandard.org/R123',
    'http://lmss.sali.org/R123',
    'lmss.sali.org/R123',
    'HTTPS://FOLIO.OPENLEGALSTANDARD.ORG/R123',
])
def test_prompt_refuses_folio_iri_in_label(surface):
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', 'text', ('wrong',), {'wrong': (surface, 'Definition')})


def test_prompt_refuses_candidate_url_in_label():
    iri = 'https://example.org/candidate'
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', 'text', (iri,), {iri: (iri, 'Definition')})


def test_replacement_count_includes_prompt_rejected_by_backstop():
    stats = RenderStats()
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(TEMPLATE, 'id', 'text', ('wrong',),
                      {'wrong': ('Label', 'See https://example.org/page and lmss.sali.org/R123')},
                      stats=stats)
    assert stats.urls_replaced == 1


@pytest.mark.parametrize('field,surface', [
    ('definition', 'oasis:names:tc:legalxml:example'),
    ('passage', 'Note:see the accompanying text'),
])
def test_prompt_preserves_non_folio_colon_text(field, surface):
    concepts = {'wrong': ('Label', surface if field == 'definition' else 'Definition')}
    prompt, _ = render_prompt(
        TEMPLATE, 'id', surface if field == 'passage' else 'text', ('wrong',), concepts,
    )
    assert surface in prompt


@pytest.mark.parametrize('field', ['template', 'passage', 'label', 'definition'])
@pytest.mark.parametrize('iri', ['https://example.org/loaded-concept', 'urn:custom:loaded'])
def test_prompt_refuses_exact_loaded_iri_anywhere(field, iri):
    # The loaded concept need not be in this item's shortlist.
    concepts = {
        'wrong': (iri if field == 'label' else 'Label',
                  iri if field == 'definition' else 'Definition'),
        iri: ('Unused', 'Not shortlisted'),
    }
    with pytest.raises(ValueError, match='IRI'):
        render_prompt(
            TEMPLATE + (iri if field == 'template' else ''), 'id',
            iri if field == 'passage' else 'text', ('wrong',), concepts,
        )


def test_collector_reports_replaced_urls(tmp_path, capsys):
    c = corpus()
    baseline = collection(c)
    concepts = {**CONCEPTS, 'gold': ('Correct https://example.org/label',
                                   'See https://www.ncsc.org/reference for details')}
    collect(baseline, c, concepts, TEMPLATE, Fake(),
            checkpoint=tmp_path / 'cp', runner_identity='fake', limit=1)
    expected = 2 * sum('gold' in d.shortlist for d in baseline.decisions)
    assert f'urls_replaced={expected}' in capsys.readouterr().out


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


def test_missing_final_message_refused():
    with pytest.raises(ValueError, match='no_final_message'):
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
    path = next(p for p in (tmp_path / 'checkpoint').rglob('*.json') if p.name != 'manifest.json')
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
    serial_bytes = output.read_bytes()
    parallel_args = [*args, '--checkpoint', str(tmp_path / 'parallel-cp'), '--jobs', '3']
    assert cli.main(parallel_args, runner=Fake()) == 0
    assert output.read_bytes() == serial_bytes


def test_owl_display_labels_and_optional_definitions(tmp_path: Path) -> None:
    import hashlib

    from run_verifier_collect import load_concepts

    owl = tmp_path / 'labels.owl'
    owl.write_text('''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
        xmlns:skos="http://www.w3.org/2004/02/skos/core#">
        <rdf:Description rdf:about="https://folio.org/rdfs-only">
          <rdfs:label>Negative Pledge</rdfs:label>
          <skos:definition>A restriction</skos:definition></rdf:Description>
        <rdf:Description rdf:about="https://folio.org/no-definition">
          <skos:prefLabel>Preferred Only</skos:prefLabel></rdf:Description>
        <rdf:Description rdf:about="https://folio.org/both">
          <rdfs:label>Display Label</rdfs:label><skos:prefLabel>Preferred Label</skos:prefLabel>
          <skos:definition>Both labels</skos:definition></rdf:Description></rdf:RDF>''')
    concepts = load_concepts(owl, hashlib.sha256(owl.read_bytes()).hexdigest())
    prompt, _ = render_prompt(TEMPLATE, 'id', 'public text', (
        'https://folio.org/rdfs-only', 'https://folio.org/no-definition',
        'https://folio.org/both',
    ), concepts)
    assert 'Negative Pledge' in prompt
    assert 'Preferred Only' in prompt
    assert 'Display Label' in prompt and 'Preferred Label' not in prompt
    assert '(no definition available)' in prompt


@pytest.mark.parametrize('blank_label', [None, '', '   '])
def test_unresolved_shortlist_preflight_before_runner(
    tmp_path: Path, blank_label: str | None,
) -> None:
    c = corpus()
    baseline = collection(c)
    # Only the final item has the unresolved IRI: even --limit must check it.
    missing = 'https://folio.org/unresolvable'
    last = replace(baseline.decisions[-1], shortlist=(missing,), candidates=(), state='failed',
                   no_match_p=None)
    baseline = replace(baseline, decisions=(*baseline.decisions[:-1], last))
    concepts = dict(CONCEPTS)
    if blank_label is not None:
        concepts[missing] = (blank_label, 'Definition')
    fake = Fake()
    with pytest.raises(ValueError, match=f'1 unresolved shortlisted concept.*{missing}'):
        collect(baseline, c, concepts, TEMPLATE, fake, checkpoint=tmp_path / 'cp',
                runner_identity='fake', limit=1)
    assert fake.calls == 0
    assert not (tmp_path / 'cp').exists()


def test_preflight_reports_unique_missing_definitions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    concepts = {**CONCEPTS, 'gold': ('Correct', '')}
    fake = Fake()
    c = corpus()
    result = collect(collection(c), c, concepts, TEMPLATE, fake,
                     checkpoint=tmp_path / 'cp', runner_identity='fake', limit=1)
    assert result is None and fake.calls == 1
    assert 'resolved=2 unresolved=0 missing_definition=1' in capsys.readouterr().out


class Observed(Fake):
    def __call__(self, prompt, cwd):
        reply = super().__call__(prompt, cwd)
        rows = [json.loads(line) for line in reply.events.splitlines()]
        rows[0] = {'type': 'thread.started', 'thread_id': 'test-thread'}
        rows.insert(1, {'type': 'turn.started'})
        rows[2]['item']['id'] = 'item_0'
        return RunnerReply('\n'.join(json.dumps(row) for row in rows))


def test_observed_stream_requested_provenance(tmp_path):
    fake = Observed()
    result = run(tmp_path, fake)
    assert fake.calls == 15
    assert all(d.state == 'decided' for d in result.decisions)
    assert result.model_id == (
        'fake-v1 (requested; codex-cli test-version; served model not reported by CLI)')
    for payload in checkpoints(tmp_path):
        assert payload['failure_reasons'] == []
        assert payload['model_mismatch'] is False
        assert payload['requested_model'] == 'fake-v1'
        assert payload['cli_version'] == 'codex-cli test-version'


def test_reported_model_mismatch_flag(tmp_path, capsys):
    result = run(tmp_path, Fake())
    assert result.model_id == 'reported-model'
    assert all(p['model_mismatch'] is True for p in checkpoints(tmp_path))
    assert 'Model mismatch:' in capsys.readouterr().out


@pytest.mark.parametrize('bad,reason', [
    ('not json', 'malformed_json'),
    ('{"no_match_p":0,"no_match_p":1,"candidates":{}}', 'malformed_json'),
    ('[]', 'invalid_response'),
    ('{"no_match_p":0,"candidates":{"c01":0}}', 'missing_handles'),
    ('{"no_match_p":0,"candidates":{"c01":2,"c02":0}}', 'probability_out_of_range'),
    ('{"no_match_p":true,"candidates":{"c01":0,"c02":0}}', 'probability_out_of_range'),
    ('{"no_match_p":NaN,"candidates":{"c01":0,"c02":0}}', 'probability_out_of_range'),
    (json.dumps({'no_match_p': 10 ** 400, 'candidates': {'c01': 0, 'c02': 0}}),
     'probability_out_of_range'),
])
def test_rejection_reasons(tmp_path, capsys, bad, reason):
    run(tmp_path, Fake(bad), limit=1)
    payload, = checkpoints(tmp_path)
    assert payload['failure_reasons'] == [reason] * 3
    assert payload['decision']['state'] == 'failed'
    assert 'failure_reasons' not in payload['decision']
    assert f'"{reason}": 3' in capsys.readouterr().out


@pytest.mark.parametrize('reply,reason', [
    ('not json', 'malformed_json'),
    ('[]', 'invalid_event_stream'),
    (events('{}', [{'type': 'error'}]), 'rejected_event_type'),
    (events('{}', [{'type': 'item.started', 'item': {'type': 'web_search'}}]), 'tool_event'),
    ('{"type":"turn.completed"}', 'no_final_message'),
    (events('{}').split('\n{"type": "turn.completed"')[0], 'no_turn_completion'),
    (events('{}', [{'type': 'turn.started', 'model': 'different'}]), 'inconsistent_model'),
    (events('{}', [{'type': 'turn.started', 'model_id': []}]), 'invalid_model'),
])
def test_event_rejection_reasons(tmp_path, reply, reason):
    run(tmp_path, lambda *_: RunnerReply(reply), limit=1)
    assert checkpoints(tmp_path)[0]['failure_reasons'] == [reason] * 3


@pytest.mark.parametrize('error,reason', [
    (subprocess.TimeoutExpired('fake', 1), 'timeout'),
    (subprocess.CalledProcessError(1, 'fake'), 'nonzero_exit'),
    (OSError('fake'), 'os_error'),
    (subprocess.SubprocessError('fake'), 'subprocess_error'),
    (TypeError('fake'), 'invalid_response'),
    (ValueError('fake'), 'invalid_response'),
])
def test_runner_rejection_reasons(tmp_path, capsys, error, reason):
    def reject(*_):
        raise error
    result = run(tmp_path, reject)
    assert all(d.state == 'failed' for d in result.decisions)
    assert all(p['failure_reasons'] == [reason] * 3 for p in checkpoints(tmp_path))
    assert f'"{reason}": 45' in capsys.readouterr().out


def test_recovered_attempt_retains_reason_on_resume(tmp_path, capsys):
    class Recover(Observed):
        def __call__(self, prompt, cwd):
            self.bad = 'bad' if self.calls == 0 else None
            return super().__call__(prompt, cwd)
    result = run(tmp_path, Recover())
    assert sum(len(p['failure_reasons']) for p in checkpoints(tmp_path)) == 1
    capsys.readouterr()
    assert run(tmp_path, Observed()) == result
    assert '"malformed_json": 1' in capsys.readouterr().out


def test_old_fingerprint_recomputed(tmp_path):
    cp = tmp_path / 'checkpoint'
    cp.mkdir()
    old_manifest = '{"fingerprint":"old-collector-fingerprint"}'
    (cp / 'manifest.json').write_text(old_manifest)
    for source in collection(corpus()).decisions[:2]:
        payload = {'fingerprint': 'old-collector-fingerprint', 'models': [],
                   'decision': replace(source, state='failed', no_match_p=None,
                                       candidates=()).to_json()}
        old_item = cp / f'{collector._digest(source.item_id)}.json'
        old_item.write_text(json.dumps({**payload, 'sha256': collector._digest(payload)}))
    fake = Observed()
    result = run(tmp_path, fake)
    assert fake.calls == 15 and all(d.state == 'decided' for d in result.decisions)
    assert (cp / 'manifest.json').read_text() == old_manifest
    resumed = Observed()
    assert run(tmp_path, resumed) == result and resumed.calls == 0


def test_jobs_bounded_and_output_identical(tmp_path):
    class Concurrent(Observed):
        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()
            self.active = self.peak = 0
            self.barrier = threading.Barrier(3)

        def __call__(self, prompt, cwd):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                reply = super().__call__(prompt, cwd)
                index = self.calls
            if index <= 3:
                self.barrier.wait(timeout=5)
            time.sleep(.002 * (4 - index % 3))
            with self.lock:
                self.active -= 1
            return reply
    serial = run(tmp_path / 'serial', Observed(), jobs=1)
    fake = Concurrent()
    parallel = run(tmp_path / 'parallel', fake, jobs=3)
    assert fake.peak == 3 and fake.calls == 15
    salt = b'test salt'
    manifest = build_manifest(['private company'], salt, gold_version='gold_v1', gold_content_sha256='a' * 64,
                              scrypt_params=ScryptParams(n=16, r=1, p=1, dklen=16, test_params=True))
    paths = [tmp_path / 'serial.json', tmp_path / 'parallel.json']
    for path, result in zip(paths, [serial, parallel], strict=True):
        write_collection(path, result, corpus(), manifest, salt)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    assert all(p['decision']['state'] == 'decided' for p in checkpoints(tmp_path / 'parallel'))


def test_parallel_limit_resume(tmp_path):
    fake = Observed()
    assert run(tmp_path, fake, jobs=3, limit=2) is None
    assert fake.calls == 2
    resumed = Observed()
    assert run(tmp_path, resumed, jobs=3) is not None
    assert resumed.calls == 13


@pytest.mark.parametrize('jobs', [0, -1, 9])
def test_jobs_bounds(tmp_path, jobs):
    with pytest.raises(ValueError, match='jobs'):
        run(tmp_path, Fake(), jobs=jobs)


def test_cli_jobs_bounds():
    import run_verifier_collect as cli
    for jobs in ['0', '-1', '9']:
        with pytest.raises(SystemExit) as error:
            cli.main(['--shortlists', 'unused', '--model', 'fake', '--checkpoint', 'unused',
                      '--salt-file', 'unused', '--jobs', jobs])
        assert error.value.code == 2


def test_cli_version_captured_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.undo()
    def fake_process(argv, **kwargs):
        calls.append(argv)
        assert argv == ['codex', '--version']
        assert kwargs['check'] is True
        return subprocess.CompletedProcess(argv, 0, 'codex-cli test-version\n', '')
    monkeypatch.setattr(subprocess, 'run', fake_process)
    result = run(tmp_path, Observed(), jobs=3)
    assert calls == [['codex', '--version']]
    assert 'codex-cli test-version' in result.model_id


@pytest.mark.parametrize('key', ['model', 'model_id'])
def test_matching_reported_model(tmp_path, key):
    def matching(prompt, cwd):
        reply = Fake()(prompt, cwd)
        rows = [json.loads(line) for line in reply.events.splitlines()]
        rows[0] = {'type': 'thread.started', key: 'fake-v1'}
        return RunnerReply('\n'.join(json.dumps(row) for row in rows))
    result = run(tmp_path, matching)
    assert result.model_id == 'fake-v1'
    assert all(p['model_mismatch'] is False for p in checkpoints(tmp_path))


def test_parallel_checkpoint_checksum(tmp_path):
    run(tmp_path, Observed(), jobs=3)
    for payload in checkpoints(tmp_path):
        digest = payload.pop('sha256')
        assert collector._digest(payload) == digest
