"""Generated-only U10 finalization proof; never consumes preserved evaluation inputs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Install before eval imports: collection is part of the protected-input boundary.
_REPOSITORY = Path(__file__).resolve().parents[1]
_AUDIT_ACTIVE = True
_HOME = Path.home().resolve()
_ALLOWED = (
    _REPOSITORY,
    Path(sys.prefix).resolve(),
    Path(sys.base_prefix).resolve(),
    Path(tempfile.gettempdir()).resolve(),
)
_PROTECTED = tuple(_REPOSITORY / "eval" / name for name in ("synthetic", "data", "reports"))


def _offline_audit(event, args):
    if not _AUDIT_ACTIVE:
        return
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("generated proof forbids network access")
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0])).resolve()
    if _HOME in path.parents and not any(path == root or root in path.parents for root in _ALLOWED):
        raise AssertionError("generated proof forbids unrelated home access")
    if (
        any(
            root in path.parents
            for root in (_REPOSITORY / "docs" / "benchmarks", _REPOSITORY / "benchmarks")
        )
        and path.suffix == ".json"
    ):
        raise AssertionError("generated proof forbids benchmark evidence reads")
    if any(path == root or root in path.parents for root in _PROTECTED):
        raise AssertionError("generated proof forbids protected evaluation reads")
    if (
        path.name.startswith(".env")
        or path.suffix in {".pem", ".key"}
        or "twin-secrets" in path.parts
    ):
        raise AssertionError("generated proof forbids credential access")


sys.addaudithook(_offline_audit)

import build_campaign_report as campaign_cli  # noqa: E402
import folio_eval.campaign_report as campaign_report  # noqa: E402
import folio_eval.comparison as comparison  # noqa: E402
import folio_eval.comparison_pilot as pilot  # noqa: E402
import folio_eval.comparison_pilot_repair as repair  # noqa: E402
import folio_eval.leakcheck as leakcheck  # noqa: E402
import pytest  # noqa: E402
from folio_eval.answer_rule import AnswerRuleConfig  # noqa: E402
from folio_eval.synthesize import SyntheticItem, build_corpus, load_corpus  # noqa: E402

_AUDIT_ACTIVE = False


@pytest.fixture(autouse=True)
def scoped_generated_audit():
    global _AUDIT_ACTIVE
    _AUDIT_ACTIVE = True
    try:
        yield
    finally:
        _AUDIT_ACTIVE = False


@pytest.fixture
def generated_checkpoint(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    pilot.subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    monkeypatch.setattr(pilot, "FOLIO_RESOLVE_ROOT", candidate)
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    salt = b"generated-only-salt"
    leak = leakcheck.build_manifest(
        ["zzfixture forbidden phrase", pilot.PUBLISHED_COMPARISON_REPORT.as_posix()],
        salt,
        gold_version="gold_v1",
        gold_content_sha256="a" * 64,
    )
    gold = tmp_path / "gold_v1.manifest.json"
    gold.write_text(json.dumps({"gold_version": 1, "gold_content_sha256": "a" * 64}))
    monkeypatch.setattr(leakcheck, "DEFAULT_LOCAL_GOLD_MANIFEST_GLOB", gold)
    config = AnswerRuleConfig()
    corpus_manifest = build_corpus(
        [
            SyntheticItem(
                "generated-one",
                "brief",
                "US",
                "Generated ordinary text",
                ("ordinary",),
                frozenset({"iri:one"}),
                "human",
            ),
            SyntheticItem(
                "generated-none",
                "brief",
                "US",
                "Unrelated ordinary text",
                provenance={"no_match": True},
            ),
        ],
        version=1,
        answer_rule_config=config,
        leak_manifest=leak,
        salt=salt,
        out_dir=tmp_path / "corpus",
        seed=1,
        ontology_cache_sha256="b" * 64,
        created="2026-10-26",
        non_lexical_floor=0,
    )
    corpus = load_corpus(corpus_manifest.manifest_path)
    args = SimpleNamespace(
        corpus_manifest=corpus_manifest.manifest_path,
        config=tmp_path / "config.json",
        out=candidate / pilot.PUBLISHED_COMPARISON_REPORT,
        checkpoint_dir=tmp_path / "checkpoint",
        leak_manifest=tmp_path / "leak.json",
        salt_file=tmp_path / "salt",
        public_metadata=tmp_path / "public.json",
        mapper_root=tmp_path / "mapper",
        enrich_root=tmp_path / "enrich",
        limit=1,
    )
    args.config.write_text(json.dumps(config.to_json()))
    args.leak_manifest.write_text(json.dumps(leak.to_json()))
    args.salt_file.write_bytes(salt)
    values = {
        ("kind",): "synthetic_comparison",
        ("provenance", "comparison_invocation", "argv", "2"): "run_synthetic_comparison",
        ("provenance", "config_selection", "answer_rule_config", "rationale"): config.rationale,
        ("stacks", "folio-resolve:candidate", "config", "rationale"): config.rationale,
    }
    for option in ("corpus-manifest", "config", "leak-manifest", "public-metadata"):
        values[("provenance", "comparison_invocation", "argv", "--" + option)] = (
            pilot._command_path(getattr(args, option.replace("-", "_")))
        )
    for stack in ("folio-enrich", "folio-mapper"):
        values[("stacks", stack + ":incumbent", "invocation", "argv", "1")] = (
            "/generated/" + stack + "/runner.py"
        )
    args.public_metadata.write_text(
        json.dumps(
            {
                "kind": comparison.COMPARISON_PUBLIC_METADATA_KIND,
                "version": 1,
                "answer_rule_config_sha256": config.content_sha256(),
                "fields": [{"path": list(path), "value": value} for path, value in values.items()],
            }
        )
    )
    identity = {
        "kind": pilot.FINALIZATION_REPAIR_KIND,
        "git_sha": "c" * 40,
        "source_sha256": "d" * 64,
    }
    monkeypatch.setattr(repair, "_repair_identity", lambda *_: dict(identity))
    repositories = {
        name: {"git_sha": char * 40, "initial_status_clean": True}
        for name, char in [("candidate", "e"), ("mapper", "f"), ("enrich", "a")]
    }

    def fingerprint(_args, current):
        return {
            "corpus_version": current.manifest.version,
            "corpus_content_sha256": current.manifest.content_sha256,
            "nomatch_content_sha256": current.manifest.nomatch_content_sha256,
            "answer_rule_config_sha256": config.content_sha256(),
            "folio_python_version": "0.3.6",
            "folio_resolve_version": "0.4.0",
            **{name + "_repository": value for name, value in repositories.items()},
            **{
                name + "_sha256": pilot._sha256_file(getattr(args, name))
                for name in ("config", "leak_manifest", "salt_file", "public_metadata")
            },
        }

    monkeypatch.setattr(pilot, "_fingerprint_for_args", fingerprint)

    def forbidden(*args, **kwargs):
        pytest.fail("finalization must not prepare or execute providers/shards")

    for name in ("_prepare_incumbents_for_new_checkpoint", "_prepare_incumbents", "_run_shard"):
        monkeypatch.setattr(pilot, name, forbidden)
    fp = fingerprint(args, corpus)
    ids = pilot._pilot_ids(corpus, args.limit)
    manifest = pilot._checkpoint_manifest(fingerprint=fp, item_ids=ids)
    args.checkpoint_dir.mkdir()
    pilot._create_or_validate_manifest(args.checkpoint_dir / "manifest.json", manifest)
    for item in (*corpus.corpus_items, *corpus.nomatch_items):
        runs = []
        for stack, lane in pilot.PILOT_STACK_LANES:
            repo = "candidate" if lane == "candidate" else stack.removeprefix("folio-")
            runs.append(
                comparison.StackRun(
                    stack=stack,
                    lane=lane,
                    folio_resolve_version="0.4.0",
                    folio_python_version="0.3.6",
                    config=config.to_json()
                    if lane == "candidate"
                    else comparison.CONSUMER_DETERMINISTIC_CONFIGS[stack],
                    rows={item.item_id: item.gold_iris},
                    stages={item.item_id: {"committed": sorted(item.gold_iris)}},
                    invocation=("python", "/generated/" + stack + "/runner.py"),
                    invocation_working_directory="/generated/" + stack,
                    repository=repositories[repo],
                )
            )
        payload = comparison.build_comparison(corpus, runs, config, item_ids=[item.item_id])
        report = pilot._shard_paths(args.checkpoint_dir, item.item_id)[0]
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(payload))
        pilot._publish_shard_completion(args.checkpoint_dir, item.item_id, fp)
    argv = [
        value
        for name in (
            "corpus_manifest",
            "config",
            "out",
            "checkpoint_dir",
            "leak_manifest",
            "salt_file",
            "public_metadata",
            "mapper_root",
            "enrich_root",
        )
        for value in ("--" + name.replace("_", "-"), str(getattr(args, name)))
    ] + ["--limit", "1", "--finalize-only"]
    return SimpleNamespace(
        args=args,
        corpus=corpus,
        identity=identity,
        manifest=manifest,
        fingerprint=fp,
        ids=ids,
        argv=argv,
        candidate=candidate,
        root=tmp_path,
        leak=leak,
        salt=salt,
        config=config,
    )


def test_first_finalization_and_ordinary_receipt_shortcut(generated_checkpoint, monkeypatch):
    fixture = generated_checkpoint
    assert repair.main(["--candidate-root", str(fixture.candidate), *fixture.argv]) == 0
    payload = json.loads(fixture.args.out.read_bytes())
    assert payload["scoreable_items"] == 1
    assert payload["nomatch_items"] == 1
    assert payload["stacks"]["folio-resolve:candidate"]["repository"]["git_sha"] == "e" * 40
    assert payload["stacks"]["folio-resolve:candidate"]["metrics"]["f1"] == 1.0
    before = fixture.args.out.read_bytes()
    monkeypatch.setattr(
        pilot, "_finalize", lambda *a, **k: pytest.fail("ordinary receipt shortcut rebuilt")
    )
    assert repair.main(["--candidate-root", str(fixture.candidate), *fixture.argv]) == 0
    assert fixture.args.out.read_bytes() == before


def test_replay_regenerates(generated_checkpoint):
    fixture = generated_checkpoint
    assert repair.main(["--candidate-root", str(fixture.candidate), *fixture.argv]) == 0
    destination = fixture.root / "replay"
    assert (
        repair.main(
            [
                "--candidate-root",
                str(fixture.candidate),
                *fixture.argv,
                "--verify-replay-dir",
                str(destination),
            ]
        )
        == 0
    )
    assert (destination / "report.json").read_bytes() == fixture.args.out.read_bytes()


def _invoke(fixture, destination=None):
    argv = ["--candidate-root", str(fixture.candidate), *fixture.argv]
    if destination is not None:
        argv.extend(["--verify-replay-dir", str(destination)])
    return repair.main(argv)


def _snapshot(fixture):
    paths = [*fixture.args.checkpoint_dir.rglob("*"), fixture.args.out]
    return {path: path.read_bytes() for path in paths if path.is_file()}


def test_replay_complete_inventory_and_preserves_baseline(generated_checkpoint, monkeypatch):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    calls = []
    original = pilot._finalize

    def record(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot, "_finalize", record)
    destination = fixture.root / "replay"
    _invoke(fixture, destination)
    assert len(calls) == 1
    assert calls[0]["artifact_root"] == destination
    expected = {
        "final-items.jsonl",
        "report.json",
        "final-complete.json",
        *{f"final-stages/{stack}/{lane}/stages.json" for stack, lane in pilot.PILOT_STACK_LANES},
    }
    assert {
        str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()
    } == expected
    for category, path in pilot._replay_artifacts(
        SimpleNamespace(checkpoint_dir=destination, out=destination / "report.json")
    ).items():
        assert path.read_bytes() == before[pilot._replay_artifacts(fixture.args)[category]]
    assert _snapshot(fixture) == before


@pytest.mark.parametrize(
    "target", ["manifest", "shard_receipt", "shard_report", "receipt", "report", "items", "stage"]
)
@pytest.mark.parametrize("damage", ["missing", "malformed", "byte_drift"])
def test_damaged_evidence_refuses_before_staging(generated_checkpoint, target, damage):
    fixture = generated_checkpoint
    _invoke(fixture)
    paths = {
        "manifest": fixture.args.checkpoint_dir / "manifest.json",
        "shard_receipt": pilot._shard_completion_path(fixture.args.checkpoint_dir, fixture.ids[0]),
        "shard_report": pilot._shard_paths(fixture.args.checkpoint_dir, fixture.ids[0])[0],
        **pilot._replay_artifacts(fixture.args),
    }
    paths["stage"] = paths["stage:folio-resolve:candidate"]
    path = paths[target]
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_bytes(b"{")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    with pytest.raises(pilot.PilotCheckpointError, match="replay verification refused or unequal"):
        _invoke(fixture, destination)
    assert _snapshot(fixture) == before
    assert not destination.exists()
    assert not (destination / "final-complete.json").exists()


@pytest.mark.parametrize(
    "change", ["extra_stage", "missing_inventory_entry", "wrong_size", "wrong_path"]
)
def test_complete_generated_artifact_inventory_authenticated(generated_checkpoint, change):
    fixture = generated_checkpoint
    _invoke(fixture)
    if change == "extra_stage":
        (fixture.args.checkpoint_dir / "final-stages" / "extra.json").write_text("{}")
    else:
        payload = json.loads(fixture.args.out.read_bytes())
        if change == "missing_inventory_entry":
            del payload["stacks"]["folio-mapper:incumbent"]
        elif change == "wrong_size":
            payload["provenance"]["items_file"]["bytes"] += 1
        else:
            payload["provenance"]["items_file"]["path"] = "/generated/incorrect"
        fixture.args.out.write_text(json.dumps(payload))
        pilot._final_completion_path(fixture.args.checkpoint_dir).unlink()
        pilot._publish_final_completion(
            fixture.args.checkpoint_dir,
            fixture.args.out,
            fixture.fingerprint,
            repair_identity=fixture.identity,
        )
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert not destination.exists()
    assert _snapshot(fixture) == before


@pytest.mark.parametrize("category", ["items", "stage", "report", "receipt"])
def test_byte_only_regenerated_difference_fails(generated_checkpoint, monkeypatch, category):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    if category == "receipt":
        original = pilot._atomic_create

        def changed(path, payload):
            result = original(path, payload)
            if path.name == "reconstructed-receipt.json":
                path.write_bytes(path.read_bytes() + b" ")
            return result

        monkeypatch.setattr(pilot, "_atomic_create", changed)
    else:
        original = pilot._finalize

        def changed(*args, **kwargs):
            original(*args, **kwargs)
            path = {
                "items": destination / "final-items.jsonl",
                "stage": destination / "final-stages/folio-resolve/candidate/stages.json",
                "report": destination / "report.json",
            }[category]
            old = path.read_bytes()
            path.write_bytes(old + b" ")
            if category != "items":
                assert json.loads(old) == json.loads(path.read_bytes())

        monkeypatch.setattr(pilot, "_finalize", changed)
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert _snapshot(fixture) == before
    assert (destination / "final-items.jsonl").exists()
    assert not (destination / "final-complete.json").exists()


@pytest.mark.parametrize(
    "destination_kind",
    [
        "existing",
        "checkpoint",
        "candidate",
        "repair",
        "mapper",
        "enrich",
        "runtime",
        "input_parent",
        "input_file",
        "symlink",
        "symlink_parent",
    ],
)
def test_unsafe_replay_destination(generated_checkpoint, destination_kind):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    paths = {
        "checkpoint": fixture.args.checkpoint_dir / "replay",
        "candidate": fixture.candidate / "replay",
        "repair": repair.REPAIR_REPOSITORY_ROOT / "replay",
        "mapper": fixture.args.mapper_root / "replay",
        "enrich": fixture.args.enrich_root / "replay",
        "runtime": Path(sys.prefix) / "replay",
        "input_parent": fixture.root,
        "input_file": fixture.args.config,
        "existing": fixture.root / "existing",
    }
    paths["existing"].mkdir()
    link = fixture.root / "alias"
    link.symlink_to(fixture.args.checkpoint_dir, target_is_directory=True)
    paths["symlink"] = link
    paths["symlink_parent"] = link / "replay"
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, paths[destination_kind])
    assert _snapshot(fixture) == before
    assert not (fixture.args.checkpoint_dir / "replay").exists()


@pytest.mark.parametrize("phase", ["admission", "after_build"])
@pytest.mark.parametrize("change", ["repair", "runtime", "metadata", "source", "baseline"])
def test_drift_refuses_without_success(generated_checkpoint, monkeypatch, phase, change):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    destination = fixture.root / "replay"

    def drift():
        if change == "repair":
            monkeypatch.setattr(
                repair, "_repair_identity", lambda *_: {**fixture.identity, "git_sha": "f" * 40}
            )
        elif change == "runtime":
            original = pilot._fingerprint_for_args
            monkeypatch.setattr(
                pilot, "_fingerprint_for_args", lambda *a: {**original(*a), "runtime": "drift"}
            )
        elif change == "metadata":
            fixture.args.public_metadata.write_bytes(
                fixture.args.public_metadata.read_bytes() + b" "
            )
        elif change == "source":
            path = pilot._shard_paths(fixture.args.checkpoint_dir, fixture.ids[0])[0]
            path.write_bytes(path.read_bytes() + b" ")
        else:
            fixture.args.out.write_bytes(fixture.args.out.read_bytes() + b" ")

    if phase == "admission":
        drift()
        before = _snapshot(fixture)
    else:
        original = pilot._finalize

        def changed(*a, **kw):
            original(*a, **kw)
            drift()

        monkeypatch.setattr(pilot, "_finalize", changed)
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert not (destination / "final-complete.json").exists()
    if phase == "admission":
        assert not destination.exists()
        assert _snapshot(fixture) == before
    elif change not in ("source", "baseline"):
        assert _snapshot(fixture) == before


def test_interruption_retains_partial_without_success(generated_checkpoint, monkeypatch):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    original = pilot.write_stage_snapshots

    def interrupted(*a, **kw):
        original(*a, **kw)
        raise KeyboardInterrupt()

    monkeypatch.setattr(pilot, "write_stage_snapshots", interrupted)
    destination = fixture.root / "replay"
    with pytest.raises(KeyboardInterrupt):
        _invoke(fixture, destination)
    assert (destination / "final-items.jsonl").exists()
    assert not (destination / "final-complete.json").exists()
    assert _snapshot(fixture) == before


def test_orphan_report_replay_refuses_and_ordinary_recovery_characterized(generated_checkpoint):
    fixture = generated_checkpoint
    _invoke(fixture)
    pilot._final_completion_path(fixture.args.checkpoint_dir).unlink()
    fixture.args.out.write_bytes(b"preserved orphan evidence")
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert _snapshot(fixture) == before
    assert not destination.exists()
    # Existing behavior, characterized only on disposable generated output.
    _invoke(fixture)
    assert fixture.args.out.read_bytes() != before[fixture.args.out]
    assert pilot._final_completion_path(fixture.args.checkpoint_dir).exists()


@pytest.mark.parametrize(
    "change", ["copied_output", "duplicate_output", "custom_output", "metadata_bytes"]
)
def test_generated_metadata_policy_stays_strict(generated_checkpoint, monkeypatch, change):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    if change in ("copied_output", "duplicate_output"):
        original = pilot.build_comparison

        def changed(*a, **kw):
            payload = original(*a, **kw)
            if change == "copied_output":
                payload["copied"] = pilot.PUBLISHED_COMPARISON_REPORT.as_posix()
            else:
                payload["provenance"]["comparison_invocation"]["argv"].extend(
                    ["--out", pilot.PUBLISHED_COMPARISON_REPORT.as_posix()]
                )
            return payload

        monkeypatch.setattr(pilot, "build_comparison", changed)
    elif change == "custom_output":
        index = fixture.argv.index("--out") + 1
        fixture.argv[index] = str(fixture.root / "custom.json")
    else:
        fixture.args.public_metadata.write_bytes(fixture.args.public_metadata.read_bytes() + b" ")
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert _snapshot(fixture) == before
    assert not (destination / "final-complete.json").exists()


def test_replay_requires_finalize_only_and_first_finalization(generated_checkpoint):
    fixture = generated_checkpoint
    destination = fixture.root / "replay"
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, destination)
    assert not destination.exists()
    fixture.argv.remove("--finalize-only")
    with pytest.raises(pilot.PilotCheckpointError, match="requires --finalize-only"):
        _invoke(fixture, destination)
    assert not destination.exists()


@pytest.mark.parametrize("internal", [False, True])
def test_items_provenance_preserves_original_fingerprint_path(generated_checkpoint, internal):
    fixture = generated_checkpoint
    if internal:
        new_checkpoint = fixture.candidate / "checkpoint"
        fixture.args.checkpoint_dir.rename(new_checkpoint)
        fixture.args.checkpoint_dir = new_checkpoint
    pilot._finalize(
        fixture.args,
        fixture.corpus,
        fixture.ids,
        fixture.manifest,
        repair_identity=fixture.identity,
    )
    payload = json.loads(fixture.args.out.read_bytes())
    expected = comparison._file_fingerprint(
        fixture.args.checkpoint_dir / "final-items.jsonl", root=fixture.candidate
    )
    assert payload["provenance"]["items_file"] == expected
    assert Path(expected["path"]).is_absolute() != internal
    destination = fixture.root / "replay"
    pilot._verify_replay(
        fixture.args,
        fixture.corpus,
        fixture.ids,
        fixture.manifest,
        destination,
        fixture.identity,
        lambda: fixture.identity,
        repair.REPAIR_REPOSITORY_ROOT,
    )
    assert (destination / "report.json").read_bytes() == fixture.args.out.read_bytes()


def test_symlink_alias_of_runtime_destination_is_rejected(generated_checkpoint):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    alias = fixture.root / "runtime-alias"
    alias.symlink_to(Path(sys.prefix), target_is_directory=True)
    with pytest.raises(pilot.PilotCheckpointError):
        _invoke(fixture, alias / "replay")
    assert _snapshot(fixture) == before


def test_failed_completion_durability_leaves_no_success(generated_checkpoint, monkeypatch):
    fixture = generated_checkpoint
    _invoke(fixture)
    before = _snapshot(fixture)
    destination = fixture.root / "replay"
    original = pilot.fsync_directory

    def interrupted(path):
        if path == destination and (destination / "final-complete.json").exists():
            raise KeyboardInterrupt()
        original(path)

    monkeypatch.setattr(pilot, "fsync_directory", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _invoke(fixture, destination)
    assert not (destination / "final-complete.json").exists()
    assert (destination / "reconstructed-receipt.json").exists()
    assert _snapshot(fixture) == before


def test_audit_scope_does_not_change_other_test_modules():
    global _AUDIT_ACTIVE
    assert _AUDIT_ACTIVE
    _AUDIT_ACTIVE = False
    try:
        # Call the audit function directly: no protected file is opened.
        _offline_audit("open", (str(_PROTECTED[0] / "example.json"), "r", 0))
    finally:
        _AUDIT_ACTIVE = True
    with pytest.raises(AssertionError, match="protected evaluation"):
        _offline_audit("open", (str(_PROTECTED[0] / "example.json"), "r", 0))


@pytest.fixture
def generated_campaign(generated_checkpoint, monkeypatch, request):
    """Bind the real campaign validators to the generated replay and local gold."""
    fixture = generated_checkpoint
    invocation = ['--candidate-root', str(fixture.candidate), *fixture.argv]
    assert repair.main(invocation) == 0
    assert repair.main([*invocation, '--verify-replay-dir', str(fixture.root / 'replay')]) == 0
    ledger = fixture.root / 'ledger.jsonl'
    records = [{
        'attempt_id': f'attempt-{index:04d}',
        'decision': 'keep' if index == 1 else 'park',
        'lever_scope': 'shared',
        'hypothesis': 'Generated fixture evidence only.',
        'commit_sha': str(index) * 40,
        'scores_before': {'synthetic': {'aggregate': {'f1': 0.5}}},
        'scores_after': {'synthetic': {'aggregate': {'f1': 0.5}}},
        'item_count': 2,
        'bootstrap_ci': {'low': 0.0, 'high': 0.0, 'n_units': 2},
    } for index in range(1, 5)]
    ledger.write_text(''.join(json.dumps(record) + '\n' for record in records))
    v1 = fixture.root / 'comparison-v1.json'
    v1.write_bytes(fixture.args.out.read_bytes())
    parity = fixture.root / 'parity.md'
    parity.write_text(
        '## U10 v8 runtime attribution (generated fixture)\n\n'
        'Candidate F1 is `1.0`, versus `1.0` for enrich and `1.0` for mapper.\n'
        'Generated rank summary (median 1).\n\n'
        + ''.join(f'| {name} | 1 | 0 | 0 | 1.0 | 0.0 |\n' for name in (
            'Baseline IRI tie-order', 'Whole-passage definition tie-order',
            'Local-window definition tie-order',
        ))
    )
    canonical_eval = fixture.root / 'campaign-tree' / 'eval'
    canonical_corpus = canonical_eval / 'synthetic'
    canonical_corpus.mkdir(parents=True)
    (canonical_corpus / 'corpus_v1.jsonl').write_bytes(fixture.corpus.manifest.corpus_path.read_bytes())
    (canonical_corpus / 'nomatch_v1.jsonl').write_bytes(fixture.corpus.manifest.nomatch_path.read_bytes())
    # Only discovery paths/pins change; the item-ID reader, freshness check and scanner stay real.
    monkeypatch.setattr(campaign_report, '__file__', str(canonical_eval / 'folio_eval' / 'campaign_report.py'))
    monkeypatch.setattr(campaign_report, 'CANONICAL_SURFACE_MANIFEST_SHA256', pilot._sha256_file(fixture.args.leak_manifest))
    campaign_report._committed_benchmark_item_ids.cache_clear()
    request.addfinalizer(campaign_report._committed_benchmark_item_ids.cache_clear)
    inputs = dict(ledger_path=ledger, comparison_v1_path=v1,
                  comparison_v2_path=fixture.args.out, parity_map_path=parity)
    return SimpleNamespace(fixture=fixture, ledger=ledger, v1=v1, parity=parity, inputs=inputs)


def _render_generated_campaign(campaign):
    fixture = campaign.fixture
    return campaign_report.render_campaign_report(
        campaign_report.load_campaign_inputs(**campaign.inputs),
        generator_command='generated fixture evidence only',
        surface_manifest_path=fixture.args.leak_manifest,
        salt_file_path=fixture.args.salt_file,
    )


def test_replay_verified_comparison_feeds_real_campaign_renderer(generated_campaign):
    campaign = generated_campaign
    fixture = campaign.fixture
    first = _render_generated_campaign(campaign)
    assert first.encode() == _render_generated_campaign(campaign).encode()
    assert 'Generated fixture evidence only.' in first
    assert f'| comparison v2 | `{pilot._sha256_file(fixture.args.out)}` |' in first
    assert 'owner-run: pending' in first
    assert 'owner-decision-required' in first
    assert all(item.item_id not in first for item in (*fixture.corpus.corpus_items, *fixture.corpus.nomatch_items))
    argv = [value for option, path in (
        ('ledger', campaign.ledger), ('comparison-v1', campaign.v1),
        ('comparison-v2', fixture.args.out), ('parity-map', campaign.parity),
        ('surface-manifest', fixture.args.leak_manifest), ('salt-file', fixture.args.salt_file),
    ) for value in ('--' + option, str(path))]
    outputs = [fixture.root / 'campaign-first.md', fixture.root / 'campaign-second.md']
    for output in outputs:
        assert campaign_cli.main([*argv, '--out', str(output)]) == 0
    assert outputs[0].read_bytes() == outputs[1].read_bytes()


@pytest.mark.parametrize('failure', ['changed-manifest', 'stale-gold', 'wrong-pin', 'leak', 'item-id'])
def test_campaign_validators_reject_generated_drift(generated_campaign, monkeypatch, failure):
    campaign = generated_campaign
    fixture = campaign.fixture
    if failure == 'changed-manifest':
        fixture.args.leak_manifest.write_bytes(fixture.args.leak_manifest.read_bytes() + b' ')
        expected = 'surface manifest digest mismatch'
    elif failure == 'wrong-pin':
        monkeypatch.setattr(campaign_report, 'CANONICAL_SURFACE_MANIFEST_SHA256', '0' * 64)
        expected = 'surface manifest digest mismatch'
    elif failure == 'stale-gold':
        (fixture.root / 'gold_v1.manifest.json').write_text(json.dumps({'gold_version': 1, 'content_sha256': 'b' * 64}))
        expected = 'manifest stale'
    else:
        records = [json.loads(line) for line in campaign.ledger.read_text().splitlines()]
        records[0]['hypothesis'] = 'zzfixture forbidden phrase' if failure == 'leak' else fixture.ids[0]
        campaign.ledger.write_text(''.join(json.dumps(record) + '\n' for record in records))
        expected = 'firm-surface leak scan' if failure == 'leak' else 'benchmark item ID'
    with pytest.raises(campaign_report.CampaignReportError, match=expected):
        _render_generated_campaign(campaign)


@pytest.mark.parametrize(('verdict', 'low', 'point', 'high', 'excludes_zero', 'expected'), [
    ('loss', -0.2, -0.1, -0.01, True, 'no-adopt'),
    ('win', 0.01, 0.1, 0.2, True, 'owner-decision-required'),
    ('hold', -0.1, 0.0, 0.1, False, 'owner-decision-required'),
    ('loss', -0.2, -0.1, -0.0, True, 'owner-decision-required'),
    ('win', 0.0, 0.1, 0.2, True, 'owner-decision-required'),
])
def test_generated_adoption_cases_preserve_existing_gate(verdict, low, point, high, excludes_zero, expected):
    entry = {'verdict': verdict, 'escalate': verdict == 'hold', 'metric': 'paired_item_f1_delta',
             'ci': {'point': point, 'low': low, 'high': high, 'excludes_zero': excludes_zero,
                    'n_units': 2, 'n_resamples': 10000, 'seed': 17, 'alpha': 0.05}}
    payload = {'verdicts': {stack: entry for stack in campaign_report.DOWNSTREAM_STACKS}}
    parsed = campaign_report._parse_comparison(json.dumps(payload), path=Path('generated.json'))
    assert [campaign_report.derive_adoption_verdict(value) for value in parsed] == [expected, expected]
