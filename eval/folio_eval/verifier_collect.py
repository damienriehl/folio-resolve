"""Isolated, resumable verifier collection; no model calls at import time."""
from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
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

COLLECTOR_LOGIC_VERSION = 2
SHUFFLE_SEED = 20260727
IRI_RE = re.compile(
    r'(?<![a-z0-9.-])(?:folio\.openlegalstandard\.org|lmss\.sali\.org)(?![a-z0-9.-])',
    re.IGNORECASE,
)
URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)


@dataclass
class RenderStats:
    """URL replacements across render attempts, including repeated concepts."""

    urls_replaced: int = 0


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


class AttemptRejected(ValueError):
    """A stable reason safe to store without model prose or subprocess output."""


def _cli_version() -> str:
    result = subprocess.run(
        ['codex', '--version'], capture_output=True, text=True, timeout=30, check=True,
    )
    version = result.stdout.strip()
    if not version or '\n' in version:
        raise ValueError('invalid codex --version output')
    return version


def _json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AttemptRejected('malformed_json')
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise AttemptRejected('malformed_json') from exc


def _events(reply: RunnerReply) -> list[dict[str, Any]]:
    rows = [_json(line) for line in reply.events.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise AttemptRejected('invalid_event_stream')
    return rows


def _model(rows: list[dict[str, Any]]) -> str | None:
    # Only lifecycle metadata is evidence; agent-written prose is never identity.
    models: set[str] = set()
    for row in rows:
        if row.get('type') not in {'thread.started', 'turn.started', 'turn.completed'}:
            continue
        for key in ('model_id', 'model'):
            model = row.get(key)
            if model is None:
                continue
            if not isinstance(model, str) or not model.strip():
                raise AttemptRejected('invalid_model')
            models.add(model)
    if len(models) > 1:
        raise AttemptRejected('inconsistent_model')
    return next(iter(models), None)


def parse_events(reply: RunnerReply) -> tuple[str | None, str]:
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
            raise AttemptRejected('rejected_event_type')
        item = row.get('item')
        if not isinstance(item, dict) or item.get('type') not in {'reasoning', 'agent_message'}:
            raise AttemptRejected('tool_event')
        if kind == 'item.completed' and item['type'] == 'agent_message':
            final = item.get('text')
    if not isinstance(final, str) or not final.strip():
        raise AttemptRejected('no_final_message')
    if not completed:
        raise AttemptRejected('no_turn_completion')
    return model, final


def preflight_concepts(
    shortlists: Iterable[tuple[str, ...]], concepts: Mapping[str, tuple[str, str]],
) -> dict[str, int]:
    """Validate all shortlisted labels and count unique IRIs, without running a model."""
    iris = {iri for shortlist in shortlists for iri in shortlist}
    unresolved = sorted(iri for iri in iris if iri not in concepts or not concepts[iri][0].strip())
    if unresolved:
        raise ValueError(
            f'{len(unresolved)} unresolved shortlisted concepts (missing label); '
            f'first {min(5, len(unresolved))}: {", ".join(unresolved[:5])}'
        )
    return {'resolved': len(iris), 'unresolved': 0,
            'missing_definition': sum(not concepts[iri][1].strip() for iri in iris)}


def render_prompt(
    template: str, item_id: str, passage: str, shortlist: tuple[str, ...],
    concepts: Mapping[str, tuple[str, str]],
    *, stats: RenderStats | None = None,
) -> tuple[str, dict[str, str]]:
    if len(set(shortlist)) != len(shortlist):
        raise ValueError('duplicate shortlist concept')
    preflight_concepts((shortlist,), concepts)
    ordered = list(shortlist)
    seed = hashlib.sha256(f'{SHUFFLE_SEED}:{item_id}'.encode()).digest()
    random.Random(int.from_bytes(seed, 'big')).shuffle(ordered)
    # Avoid the identity permutation, including small N where it is common.
    if len(ordered) > 1 and ordered == list(shortlist):
        ordered = ordered[1:] + ordered[:1]
    handles = {f'c{i:02d}': iri for i, iri in enumerate(ordered, 1)}
    def neutralize_links(text: str) -> str:
        # Sanitization must not hide FOLIO identifiers or known candidate IRIs.
        for match in URL_RE.finditer(text):
            url = match.group()
            if IRI_RE.search(url) or any(iri in url for iri in concepts):
                raise ValueError('rendered prompt contains an IRI')
        sanitized, count = URL_RE.subn('[link]', text)
        if stats is not None:
            stats.urls_replaced += count
        return sanitized

    rows = []
    for handle, iri in handles.items():
        label, definition = concepts[iri]
        label, definition = neutralize_links(label), neutralize_links(definition)
        if not definition.strip():
            definition = '(no definition available)'
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
        raise AttemptRejected('invalid_response')
    candidates = row['candidates']
    if not isinstance(candidates, dict) or set(candidates) != set(handles):
        raise AttemptRejected('missing_handles')
    try:
        probabilities = {
            iri: _probability(candidates[handle], handle) for handle, iri in handles.items()
        }
        no_match_p = _probability(row['no_match_p'], 'no_match_p')
    except (ValueError, OverflowError) as exc:
        raise AttemptRejected('probability_out_of_range') from exc
    return replace(
        source, state='decided', no_match_p=no_match_p,
        candidates=tuple(CandidateProbability(iri, probabilities[iri]) for iri in source.shortlist),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def collect(
    baseline: DecisionCollection, corpus: LoadedCorpus,
    concepts: Mapping[str, tuple[str, str]], template: str, runner: Runner,
    *, checkpoint: Path, runner_identity: str, limit: int | None = None, jobs: int = 1,
) -> DecisionCollection | None:
    """A limit collects a resumable prefix; partial runs never publish a collection."""
    baseline.validate(corpus)
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 8:
        raise ValueError('jobs must be between 1 and 8')
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    counts = preflight_concepts((d.shortlist for d in baseline.decisions), concepts)
    # U2 has no metadata field for this count; include it even for limited/resumed runs.
    print('Shortlist concept preflight (unique IRIs): ' +
          ' '.join(f'{key}={value}' for key, value in counts.items()))
    texts = {item.item_id: item.text for item in (*corpus.scoreable_items, *corpus.nomatch_items)}
    stats = RenderStats()
    prompts = {
        d.item_id: render_prompt(template, d.item_id, texts[d.item_id], d.shortlist, concepts,
                                 stats=stats)
        for d in baseline.decisions
    }
    print(f'Prompt rendering (all items): urls_replaced={stats.urls_replaced}')
    cli_version = _cli_version()
    requested_provenance = (
        f'{runner_identity} (requested; {cli_version}; served model not reported by CLI)'
    )
    # Leave pre-fix checkpoints intact, but never reuse their failed decisions.
    checkpoint = checkpoint / f'collector-v{COLLECTOR_LOGIC_VERSION}'
    fingerprint = _digest({
        'baseline': baseline.to_json(), 'prompts': prompts, 'runner': runner_identity,
        'collector_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'template': template, 'seed': SHUFFLE_SEED,
        'collector_logic_version': COLLECTOR_LOGIC_VERSION, 'cli_version': cli_version,
    })
    manifest = {'fingerprint': fingerprint}
    _atomic_create(checkpoint / 'manifest.json', manifest)
    if _json((checkpoint / 'manifest.json').read_text()) != manifest:
        raise ValueError('checkpoint fingerprint mismatch')
    results: dict[str, PassageDecision] = {}
    models: set[str] = set()
    reasons: Counter[str] = Counter()
    mismatches: set[str] = set()
    pending: list[PassageDecision] = []

    def record(payload: dict[str, Any]) -> None:
        decision = PassageDecision.from_json(payload['decision'])
        results[decision.item_id] = decision
        models.update(payload['models'])
        reasons.update(payload['failure_reasons'])
        if payload['model_mismatch']:
            mismatches.update(payload['models'])

    for source in baseline.decisions:
        path = checkpoint / f'{_digest(source.item_id)}.json'
        if not path.exists():
            pending.append(source)
            continue
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
        for key in ('models', 'failure_reasons'):
            values = payload[key]
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f'invalid checkpoint {key}')
        if (not isinstance(payload['model_mismatch'], bool)
                or payload['requested_model'] != runner_identity
                or payload['cli_version'] != cli_version):
            raise ValueError('invalid checkpoint provenance')
        record(payload)

    def collect_item(source: PassageDecision) -> dict[str, Any]:
        prompt, handles = prompts[source.item_id]
        decision = replace(source, state='failed', no_match_p=None, candidates=())
        item_models: set[str] = set()
        failure_reasons: list[str] = []
        mismatch = False
        for _attempt in range(3):
            try:
                # Absolute /tmp avoids TMPDIR accidentally placing prompts in the repo.
                with tempfile.TemporaryDirectory(prefix='folio-verifier-', dir='/tmp') as temp:
                    cwd = Path(temp)
                    if cwd.is_relative_to(Path(__file__).resolve().parents[2]):
                        raise AttemptRejected('unsafe_cwd')
                    reply = runner(prompt, cwd)
                reported = _model(_events(reply))
                item_models.add(reported if reported is not None else requested_provenance)
                mismatch |= reported is not None and reported != runner_identity
                _, text = parse_events(reply)
                decision = _decision(text, source, handles)
                break
            except AttemptRejected as exc:
                failure_reasons.append(str(exc))
            except subprocess.TimeoutExpired:
                failure_reasons.append('timeout')
            except subprocess.CalledProcessError:
                failure_reasons.append('nonzero_exit')
            except subprocess.SubprocessError:
                failure_reasons.append('subprocess_error')
            except OSError:
                failure_reasons.append('os_error')
            except (ValueError, TypeError):
                failure_reasons.append('invalid_response')
        payload = {'fingerprint': fingerprint, 'decision': decision.to_json(),
                   'models': sorted(item_models), 'failure_reasons': failure_reasons,
                   'requested_model': runner_identity, 'cli_version': cli_version,
                   'model_mismatch': mismatch}
        path = checkpoint / f'{_digest(source.item_id)}.json'
        _atomic_write(path, {**payload, 'sha256': _digest(payload)})
        return payload

    # Each worker saves its own checkpoint before returning; map yields in input
    # order even when completion order differs. Only this thread aggregates state.
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for payload in pool.map(collect_item, pending[:limit]):
            record(payload)
    print('Rejected attempt reasons: ' + json.dumps(dict(sorted(reasons.items()))))
    if mismatches:
        print(f'Model mismatch: requested={runner_identity}; reported={sorted(mismatches)}')
    if len(results) != len(baseline.decisions):
        return None
    if not models:
        models.add(requested_provenance)
    if len(models) != 1:
        raise ValueError('collection requires one model provenance; mixed identities recorded')
    result = replace(
        baseline, arm_name='codex-verifier-v1', model_id=next(iter(models)),
        prompt_template_sha256=hashlib.sha256(template.encode()).hexdigest(),
        decisions=tuple(results[d.item_id] for d in baseline.decisions),
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
