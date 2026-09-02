from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from folio_eval import synthetic_score as synthetic_score_module
from folio_eval.answer_rule import AnswerRuleConfig, load_config
from folio_eval.experiment import (
    ExperimentRecord,
    ItemOutcome,
    SliceOutcome,
    finish_attempt,
    load_raw_records,
    start_attempt,
)
from folio_eval.leakcheck import Manifest, ScryptParams, build_manifest, scan_file
from folio_eval.synthesize import SynthesisError, SyntheticItem, build_corpus, load_corpus
from folio_eval.synthetic_checkpoint import CheckpointFingerprint, SyntheticCheckpointStore
from folio_eval.synthetic_score import (
    DEPTH_PROBE_MAX,
    PublicReportMetadata,
    SyntheticScoreResult,
    build_synthetic_report,
    depth_probe,
    load_public_report_metadata,
    score_corpus,
    score_corpus_checkpointed,
    write_report,
)

from folio_resolve import AliasBlocklist, Concept, InMemoryOntology, OntologyProvider

from .conftest import audit_open_paths, audited_python_process


@dataclass(frozen=True)
class _OperatorInputs:
    config: AnswerRuleConfig
    corpus_manifest: Path
    leak_manifest: Manifest
    leak_manifest_path: Path
    public_metadata: PublicReportMetadata
    salt: bytes
    salt_path: Path


def _ontology() -> InMemoryOntology:
    return InMemoryOntology(
        [
            Concept(iri="R-arb", label="Arbitration Rules"),
            Concept(iri="R-proof", label="Burden of Proof", definition="evidentiary burden"),
        ]
    )


def _public_metadata(tmp_path: Path, config: AnswerRuleConfig) -> PublicReportMetadata:
    payload = {
        "kind": "synthetic-report-public-metadata",
        "version": 1,
        "answer_rule_config_sha256": config.content_sha256(),
        "fields": [
            {"path": ["kind"], "value": "synthetic_baseline"},
            {"path": ["label"], "value": "synthetic-baseline-v1"},
            {"path": ["answer_rule_config", "rationale"], "value": config.rationale},
            {
                "path": ["determinism_selftest", "target"],
                "value": "folio_eval.selftest:synthetic_scoring_payload",
            },
        ],
    }
    path = tmp_path / "public-report-metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_public_report_metadata(path)


def _operator_inputs(tmp_path: Path, config: AnswerRuleConfig | None = None) -> _OperatorInputs:
    config = config or AnswerRuleConfig(threshold=0.5, top_k=5)
    salt = b"uat-public-lane-test-secret"
    salt_path = tmp_path / "secret.bin"
    salt_path.write_bytes(salt)
    leak_manifest = build_manifest(
        ["private manifest surface"],
        salt,
        gold_version="test",
        gold_content_sha256="d" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
    )
    leak_manifest_path = tmp_path / "surface-manifest.json"
    leak_manifest_path.write_text(
        json.dumps(leak_manifest.to_json(), sort_keys=True) + "\n", encoding="utf-8"
    )
    public_metadata = _public_metadata(tmp_path, config)
    manifest = build_corpus(
        [
            SyntheticItem(
                item_id="score-deterministic",
                doc_type="motion",
                jurisdiction="US",
                text="The motion invokes Arbitration Rules.",
                gold_labels=("Arbitration Rules",),
                gold_iris=frozenset({"R-arb"}),
                verification="deterministic",
            ),
            SyntheticItem(
                item_id="score-human",
                doc_type="brief",
                jurisdiction="US",
                text="The brief discusses allocation of an evidentiary burden.",
                gold_labels=("Burden of Proof",),
                gold_iris=frozenset({"R-proof"}),
                verification="human",
            ),
            SyntheticItem(
                item_id="needs-review",
                doc_type="brief",
                jurisdiction="US",
                text="A disputed classification remains visible for review.",
                verification="needs_review",
            ),
            SyntheticItem(
                item_id="intentional-no-match",
                doc_type="contract",
                jurisdiction="US",
                text="The parties exchanged ordinary notices.",
                provenance={"no_match": True},
            ),
        ],
        version=1,
        answer_rule_config=config,
        leak_manifest=leak_manifest,
        salt=salt,
        out_dir=tmp_path,
        seed=7,
        ontology_cache_sha256="c" * 64,
        created="2026-09-02T00:00:00Z",
    )
    return _OperatorInputs(
        config=config,
        corpus_manifest=manifest.manifest_path,
        leak_manifest=leak_manifest,
        leak_manifest_path=leak_manifest_path,
        public_metadata=public_metadata,
        salt=salt,
        salt_path=salt_path,
    )


def _fingerprint(inputs: _OperatorInputs) -> CheckpointFingerprint:
    corpus = load_corpus(inputs.corpus_manifest)
    return CheckpointFingerprint(
        corpus_content_sha256=corpus.manifest.content_sha256,
        nomatch_content_sha256=corpus.manifest.nomatch_content_sha256,
        answer_rule_config_sha256=inputs.config.content_sha256(),
        ontology_cache_sha256=corpus.manifest.ontology_cache_sha256,
        git_head="d" * 40,
        python_hash_seed="0",
        python_version="3.11.0",
        folio_python_version="test",
        folio_resolve_version="0.4.0",
        lockfile_sha256="e" * 64,
    )


def _report(result: SyntheticScoreResult, inputs: _OperatorInputs) -> dict[str, object]:
    corpus = load_corpus(inputs.corpus_manifest)
    return build_synthetic_report(
        result,
        corpus=corpus,
        config=inputs.config,
        label="synthetic-baseline-v1",
        ontology_pin=corpus.manifest.ontology_cache_sha256,
        depth_probe_result=depth_probe(result.run, inputs.config),
        determinism_selftest={
            "target": "folio_eval.selftest:synthetic_scoring_payload",
            "deterministic": True,
        },
    )


def test_us_eo_01_versioned_cohort_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-EO-01 verifies versioned local slices and fails closed on a changed hash."""
    with audit_open_paths(monkeypatch, tmp_path):
        inputs = _operator_inputs(tmp_path)
        loaded = load_corpus(inputs.corpus_manifest)

        assert {item.item_id for item in loaded.scoreable_items} == {
            "score-deterministic",
            "score-human",
        }
        assert [item.item_id for item in loaded.needs_review_items] == ["needs-review"]
        assert [item.item_id for item in loaded.nomatch_items] == ["intentional-no-match"]
        assert inputs.leak_manifest_path.is_file()
        assert inputs.public_metadata.source_path.is_file()
        assert inputs.salt_path.is_file()

        corpus_path = loaded.manifest.corpus_path
        corpus_path.write_bytes(corpus_path.read_bytes() + b" ")
        with pytest.raises(SynthesisError, match="corpus verification failed") as failure:
            load_corpus(inputs.corpus_manifest)
        assert "Arbitration Rules" not in str(failure.value)


def test_us_eo_02_shards_finalize_to_identical_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-EO-02 scores only eligible rows and makes sharded output byte-identical."""
    with audit_open_paths(monkeypatch, tmp_path):
        inputs = _operator_inputs(tmp_path)
        corpus = load_corpus(inputs.corpus_manifest)
        direct_adapter = synthetic_score_module.DocumentAdapter(
            _ontology(), blocklist=AliasBlocklist([])
        )
        direct = score_corpus(corpus, _ontology(), inputs.config, adapter=direct_adapter)
        store = SyntheticCheckpointStore.create(
            tmp_path / "checkpoint",
            fingerprint=_fingerprint(inputs),
            shard_count=2,
            expected_item_count=len(corpus.gold_item_records()) + len(corpus.nomatch_items),
            retained_limit=max(DEPTH_PROBE_MAX, inputs.config.top_k),
        )

        score_corpus_checkpointed(
            corpus,
            _ontology(),
            inputs.config,
            store=store,
            shard_index=0,
            adapter=synthetic_score_module.DocumentAdapter(
                _ontology(), blocklist=AliasBlocklist([])
            ),
        )
        score_corpus_checkpointed(
            corpus,
            _ontology(),
            inputs.config,
            store=store,
            shard_index=1,
            adapter=synthetic_score_module.DocumentAdapter(
                _ontology(), blocklist=AliasBlocklist([])
            ),
        )
        finalized = score_corpus_checkpointed(
            corpus, None, inputs.config, store=store, finalize_only=True
        )
        assert finalized is not None
        assert {item.item_id for item in finalized.run.item_scores} == {
            "score-deterministic",
            "score-human",
        }

        direct_path = write_report(
            tmp_path / "direct-report.json",
            _report(direct, inputs),
            inputs.leak_manifest,
            inputs.salt,
            public_metadata=inputs.public_metadata,
        )
        sharded_path = write_report(
            tmp_path / "sharded-report.json",
            _report(finalized, inputs),
            inputs.leak_manifest,
            inputs.salt,
            public_metadata=inputs.public_metadata,
        )
        assert sharded_path.read_bytes() == direct_path.read_bytes()


def test_us_eo_03_leak_check_and_experiment_are_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-EO-03 detects report collisions and completes a local experiment record."""
    with audit_open_paths(monkeypatch, tmp_path):
        inputs = _operator_inputs(tmp_path)
        collision_report = tmp_path / "collision-report.json"
        collision_report.write_text(
            json.dumps({"summary": "private manifest surface"}), encoding="utf-8"
        )
        leak_result = scan_file(collision_report, inputs.leak_manifest, inputs.salt)
        assert leak_result.collision_count > 0

        before = SliceOutcome(
            slice_name="synthetic",
            items=(ItemOutcome(item_id="score-1", tp=1, fp=0, fn=0, exact=True),),
        )
        experiments_log = tmp_path / "experiments.jsonl"
        pending_path = tmp_path / "pending.json"
        live_suspects_path = tmp_path / "live-suspects.jsonl"
        start_attempt(
            hypothesis="confirm deterministic public-lane bookkeeping",
            cluster_targeted="synthetic acceptance",
            cluster_size=1,
            gold_version=0,
            ontology_hash="c" * 64,
            config_hash=inputs.config.content_sha256(),
            surfaces=(),
            manifest_checker=(inputs.leak_manifest, inputs.salt),
            prior_scores={"synthetic": before},
            lever_scope="shared",
            corpus_version="synthetic-v1",
            run_selftest=False,
            experiments_log=experiments_log,
            pending_path=pending_path,
            now="2026-09-02T00:00:00Z",
        )
        record = finish_attempt(
            decision="keep",
            reason="public acceptance payload remained stable",
            surfaces=(),
            manifest_checker=(inputs.leak_manifest, inputs.salt),
            after_scores={"synthetic": before},
            commit_sha="cafebabe",
            live_suspects_path=live_suspects_path,
            experiments_log=experiments_log,
            pending_path=pending_path,
            now="2026-09-02T00:01:00Z",
            ci_resamples=20,
            ci_seed=1,
        )
        loaded_record = ExperimentRecord.from_json(load_raw_records(experiments_log)[0])
        assert loaded_record == record
        assert loaded_record.decision == "keep"
        assert not pending_path.exists()

    eval_root = Path(__file__).resolve().parents[2] / "eval"
    source = f"""
import sys
sys.path.insert(0, {str(eval_root)!r})
from folio_eval.selftest import DEFAULT_SELFTEST_TARGET, payload_sha256

print(payload_sha256(DEFAULT_SELFTEST_TARGET))
"""
    outputs: list[str] = []
    for seed in ("0", "1"):
        child = audited_python_process(
            tmp_path,
            f"operator-determinism-{seed}",
            source,
            extra_env={"PYTHONHASHSEED": seed},
        )
        completed = child.run(timeout=20.0)
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout.strip())

    assert outputs[0] == outputs[1]


def test_us_eo_02_cli_real_ontology_runs_one_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_ontology: OntologyProvider,
) -> None:
    """US-EO-02 CLI scenario requires the real-ontology opt-in and runs one shard."""
    assert isinstance(real_ontology, OntologyProvider)
    repo_root = Path(__file__).resolve().parents[2]
    eval_root = repo_root / "eval"
    schema_root = eval_root / "synthetic"
    with audit_open_paths(monkeypatch, tmp_path, allowed_roots=(schema_root,)):
        config_path = schema_root / "answer_rule_config_synthetic_v1.json"
        metadata_path = schema_root / "public_report_metadata_v1.json"
        config = load_config(config_path)
        inputs = _operator_inputs(tmp_path, config)
        fingerprint = _fingerprint(inputs)

    cli_args = [
        "--corpus-manifest",
        str(inputs.corpus_manifest),
        "--config",
        str(config_path),
        "--out",
        str(tmp_path / "cli-report.json"),
        "--leak-manifest",
        str(inputs.leak_manifest_path),
        "--salt-file",
        str(inputs.salt_path),
        "--public-metadata",
        str(metadata_path),
        "--checkpoint-dir",
        str(tmp_path / "cli-checkpoint"),
        "--shard-count",
        "1",
        "--shard-index",
        "0",
    ]
    source = f"""
import sys
from types import SimpleNamespace

sys.path.insert(0, {str(eval_root)!r})
from folio_eval import synthetic_score as synthetic_score_module
from folio_eval.synthetic_checkpoint import CheckpointFingerprint
from folio_eval.selftest import SelfTestResult

fingerprint = CheckpointFingerprint(**{fingerprint.to_json()!r})
synthetic_score_module.assert_ontology_pin = lambda _sha: SimpleNamespace(sha256={"c" * 64!r})
synthetic_score_module.build_checkpoint_fingerprint = (
    lambda _corpus, _config, repo_root: fingerprint
)
selftest = SelfTestResult(
    target="folio_eval.selftest:synthetic_scoring_payload",
    first_sha256="a" * 64,
    second_sha256="a" * 64,
    first_seed="0",
    second_seed="1",
)
synthetic_score_module.run_determinism_selftest = lambda: selftest
raise SystemExit(synthetic_score_module.main({cli_args!r}))
"""
    child = audited_python_process(
        tmp_path,
        "real-ontology-cli",
        source,
        allowed_roots=(schema_root,),
        extra_env={"PYTHONHASHSEED": "0", "FOLIO_RESOLVE_UAT_REAL_ONTOLOGY": "1"},
    )
    completed = child.run(timeout=120.0)

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "cli-report.json").is_file()
