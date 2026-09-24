"""Isolated, resumable verifier collection; no model calls at import time."""
from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .leakcheck import Manifest, scan_json_value
from .synthesize import LoadedCorpus
from .synthetic_checkpoint import _atomic_create, _atomic_write
from .verifier import (
    CandidateProbability,
    DecisionCollection,
    PassageDecision,
    _probability,
)

SHUFFLE_SEED = 20260727
IRI_RE = re.compile(r'\b[a-z][a-z0-9+.-]*:(?://|[^\s])', re.IGNORECASE)


@dataclass(frozen=True)
class RunnerReply:
    events: str


class Runner(Protocol):
    def __call__(self, prompt: str, cwd: Path) -> RunnerReply: ...


@dataclass(frozen=True)
class CodexRunner:
    model: str
    timeout: float = 300

    def __call__(self, prompt: str, cwd: Path) -> RunnerReply:
        if list(cwd.iterdir()):
            raise ValueError('runner cwd must be empty')
        argv = [
            'codex', 'exec', '-C', str(cwd), '--skip-git-repo-check',
            '--ephemeral', '--sandbox', 'read-only', '--json',
            '--ignore-user-config', '--model', self.model, '-',
        ]
        result = subprocess.run(
            argv, cwd=cwd, input=prompt, capture_output=True, text=True,
            timeout=self.timeout, check=True,
        )
        return RunnerReply(result.stdout)


def _json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def _events(reply: RunnerReply) -> list[dict[str, Any]]:
    rows = [_json(line) for line in reply.events.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError('invalid event stream')
    return rows


def _model(rows: list[dict[str, Any]]) -> str:
    # CLI variants may report model or model_id on lifecycle events. Never use
    # requested --model or model-written agent prose as evidence of actual identity.
    models = {
        row.get('model_id', row.get('model'))
        for row in rows
        if row.get('type') in {'thread.started', 'turn.started', 'turn.completed'}
        and row.get('model_id', row.get('model')) is not None
    }
    if len(models) != 1:
        raise ValueError('missing or inconsistent reported model id')
    model = next(iter(models))
    if not isinstance(model, str) or not model.strip():
        raise ValueError('invalid reported model id')
    return model


def parse_events(reply: RunnerReply) -> tuple[str, str]:
    """Fail closed on unknown event/item types, including every execution tool."""
    rows = _events(reply)
    model = _model(rows)
    final: str | None = None
    completed = False
    for row in rows:
        kind = row.get('type')
        if kind in {'thread.started', 'turn.started'}:
            continue
        if kind == 'turn.completed':
            completed = True
            continue
        if kind not in {'item.started', 'item.updated', 'item.completed'}:
            raise ValueError('rejected event type')
        item = row.get('item')
        if not isinstance(item, dict) or item.get('type') not in {'reasoning', 'agent_message'}:
            raise ValueError('command/tool execution or unknown item rejected')
        if kind == 'item.completed' and item['type'] == 'agent_message':
            final = item.get('text')
    if not completed or not isinstance(final, str) or not final.strip():
        raise ValueError('missing final agent message or turn completion')
    return model, final


def render_prompt(
    template: str, item_id: str, passage: str, shortlist: tuple[str, ...],
    concepts: Mapping[str, tuple[str, str]],
) -> tuple[str, dict[str, str]]:
    if len(set(shortlist)) != len(shortlist):
        raise ValueError('duplicate shortlist concept')
    ordered = list(shortlist)
    seed = hashlib.sha256(f'{SHUFFLE_SEED}:{item_id}'.encode()).digest()
    random.Random(int.from_bytes(seed, 'big')).shuffle(ordered)
    # Avoid the identity permutation, including small N where it is common.
    if len(ordered) > 1 and ordered == list(shortlist):
        ordered = ordered[1:] + ordered[:1]
    handles = {f'c{i:02d}': iri for i, iri in enumerate(ordered, 1)}
    rows = []
    for handle, iri in handles.items():
        label, definition = concepts[iri]
        if not label.strip() or not definition.strip():
            raise ValueError('concept requires a label and FOLIO definition')
        rows.append({'handle': handle, 'label': label, 'definition': definition})
    fields = {'passage': json.dumps(passage, ensure_ascii=False),
              'candidates': json.dumps(rows, ensure_ascii=False)}
    rendered = re.sub(r'\{(passage|candidates)\}', lambda match: fields[match[1]], template)
    if IRI_RE.search(rendered) or any(iri in rendered for iri in concepts):
        raise ValueError('rendered prompt contains an IRI')
    return rendered, handles


def _decision(text: str, source: PassageDecision, handles: dict[str, str]) -> PassageDecision:
    row = _json(text)
    if not isinstance(row, dict) or set(row) != {'no_match_p', 'candidates'}:
        raise ValueError('expected strict response JSON')
    candidates = row['candidates']
    if not isinstance(candidates, dict) or set(candidates) != set(handles):
        raise ValueError('response must cover every handle exactly')
    probabilities = {
        iri: _probability(candidates[handle], handle) for handle, iri in handles.items()
    }
    return replace(
        source, state='decided', no_match_p=_probability(row['no_match_p'], 'no_match_p'),
        candidates=tuple(CandidateProbability(iri, probabilities[iri]) for iri in source.shortlist),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def collect(
    baseline: DecisionCollection, corpus: LoadedCorpus,
    concepts: Mapping[str, tuple[str, str]], template: str, runner: Runner,
    *, checkpoint: Path, runner_identity: str, limit: int | None = None,
) -> DecisionCollection | None:
    """A limit collects a resumable prefix; partial runs never publish a collection."""
    baseline.validate(corpus)
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    texts = {item.item_id: item.text for item in (*corpus.scoreable_items, *corpus.nomatch_items)}
    prompts = {
        d.item_id: render_prompt(template, d.item_id, texts[d.item_id], d.shortlist, concepts)
        for d in baseline.decisions
    }
    fingerprint = _digest({
        'baseline': baseline.to_json(), 'prompts': prompts, 'runner': runner_identity,
        'collector_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'template': template, 'seed': SHUFFLE_SEED,
    })
    manifest = {'fingerprint': fingerprint}
    _atomic_create(checkpoint / 'manifest.json', manifest)
    if _json((checkpoint / 'manifest.json').read_text()) != manifest:
        raise ValueError('checkpoint fingerprint mismatch')
    results = []
    models: set[str] = set()
    called = 0
    for source in baseline.decisions:
        path = checkpoint / f'{_digest(source.item_id)}.json'
        if path.exists():
            payload = _json(path.read_text())
            digest = payload.pop('sha256')
            if _digest(payload) != digest or payload['fingerprint'] != fingerprint:
                raise ValueError('checkpoint checksum or fingerprint mismatch')
            decision = PassageDecision.from_json(payload['decision'])
            if (decision.item_id, decision.kind, decision.shortlist) != (
                source.item_id, source.kind, source.shortlist,
            ):
                raise ValueError('checkpoint item mismatch')
            # Reuse U2's complete validation even on a partial checkpoint.
            replace(baseline, decisions=tuple(
                decision if d.item_id == source.item_id else d for d in baseline.decisions
            )).validate(corpus)
            item_models = payload['models']
            if not isinstance(item_models, list) or any(
                not isinstance(m, str) or not m.strip() for m in item_models
            ):
                raise ValueError('invalid checkpoint models')
        else:
            if limit is not None and called >= limit:
                return None
            called += 1
            prompt, handles = prompts[source.item_id]
            decision = replace(source, state='failed', no_match_p=None, candidates=())
            item_models = []
            for _attempt in range(3):
                try:
                    # Absolute /tmp avoids TMPDIR accidentally placing prompts in the repo.
                    with tempfile.TemporaryDirectory(prefix='folio-verifier-', dir='/tmp') as temp:
                        cwd = Path(temp)
                        if cwd.is_relative_to(Path(__file__).resolve().parents[2]):
                            raise ValueError('runner cwd is inside repository')
                        reply = runner(prompt, cwd)
                    item_models.append(_model(_events(reply)))
                    _, text = parse_events(reply)
                    decision = _decision(text, source, handles)
                    break
                except (ValueError, TypeError, OSError, subprocess.SubprocessError):
                    continue
            payload = {'fingerprint': fingerprint, 'decision': decision.to_json(),
                       'models': sorted(set(item_models))}
            _atomic_write(path, {**payload, 'sha256': _digest(payload)})
        models.update(item_models)
        results.append(decision)
    if len(models) != 1:
        raise ValueError('collection requires one reported model id; no guessed provenance')
    result = replace(
        baseline, arm_name='codex-verifier-v1', model_id=next(iter(models)),
        prompt_template_sha256=hashlib.sha256(template.encode()).hexdigest(),
        decisions=tuple(results),
    )
    result.validate(corpus)
    return result


def write_collection(
    path: Path, collection: DecisionCollection, corpus: LoadedCorpus,
    manifest: Manifest, salt: bytes,
) -> None:
    collection.validate(corpus)
    payload = collection.to_json()
    if scan_json_value(payload, manifest, salt):
        raise ValueError('firm surface collision; collection not written')
    _atomic_write(path, payload)
