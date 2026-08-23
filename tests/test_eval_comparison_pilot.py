"""Power-loss-safe U10 comparison pilot checkpoints."""

from __future__ import annotations

import json
import venv
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import folio_eval.comparison_pilot as pilot_module
import pytest
from folio_eval.answer_rule import AnswerRuleConfig
from folio_eval.comparison_pilot import (
    PilotCheckpointError,
    _checkpoint_manifest,
    _consumer_environment_fingerprint,
    _create_or_validate_manifest,
    _finalization_invocation,
    _fingerprint,
    _load_shard,
    _merge_stack_runs,
    _pilot_ids,
    _run_shard,
)
from folio_eval.synthesize import CorpusManifest, LoadedCorpus, SyntheticItem


def _corpus(tmp_path: Path) -> LoadedCorpus:
    config = AnswerRuleConfig()
    manifest = CorpusManifest(
        version=1,
        content_sha256="corpus",
        nomatch_content_sha256="nomatch",
        ontology_cache_sha256="ontology",
        answer_rule_config_sha256=config.content_sha256(),
        item_counts={},
        non_lexical_fraction=0.0,
        non_lexical_floor=0.0,
        scoreable=True,
        seed=1,
        created="2026-08-23",
        manifest_path=tmp_path / "manifest.json",
    )
    return LoadedCorpus(
        manifest,
        (
            SyntheticItem("one", "brief", "US", "One", (), frozenset({"iri:one"}), "human"),
            SyntheticItem("two", "brief", "US", "Two", (), frozenset({"iri:two"}), "human"),
        ),
        (SyntheticItem("none", "brief", "US", "None", provenance={"no_match": True}),),
    )


def _isolated_python_site(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    version = f"python{pilot_module.sys.version_info.major}.{pilot_module.sys.version_info.minor}"
    return runtime / "bin" / "python", runtime / "lib" / version / "site-packages"


def _stack(stack: str, lane: str, item_id: str) -> dict[str, object]:
    return {
        "stack": stack,
        "lane": lane,
        "versions": {"folio-resolve": "0.4.0", "folio-python": "0.3.6"},
        "config": {"top_k": 3},
        "invocation": {
            "kind": "executed_process" if lane == "incumbent" else "in_process",
            "argv": ["python", f"/repo/{stack}/runner.py"],
            "working_directory": f"/repo/{stack}",
        },
        "repository": {"git_sha": "a" * 40, "initial_status_clean": True},
        "items": {item_id: [f"iri:{item_id}"]},
        "stage_snapshot": {"by_item": {item_id: {"committed": [f"iri:{item_id}"]}}},
    }


def _shard(item_id: str, *, nomatch: bool = False) -> dict[str, object]:
    return {
        "kind": "synthetic_comparison",
        "run_kind": "shard",
        "provenance": {
            "cohort_selection": {
                "scoreable_item_ids": [] if nomatch else [item_id],
                "nomatch_item_ids": [item_id] if nomatch else [],
            }
        },
        "stacks": {
            "folio-enrich:incumbent": _stack("folio-enrich", "incumbent", item_id),
            "folio-mapper:incumbent": _stack("folio-mapper", "incumbent", item_id),
            "folio-resolve:candidate": _stack("folio-resolve", "candidate", item_id),
        },
    }


def test_pilot_ids_are_fixed_scoreable_prefix_plus_all_nomatch(tmp_path: Path) -> None:
    assert _pilot_ids(_corpus(tmp_path), 1) == ("one", "none")


def test_durable_directory_creation_syncs_each_new_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    target = existing / "items" / "opaque" / "stages"
    synced: list[Path] = []
    monkeypatch.setattr(pilot_module, "fsync_directory", synced.append)

    pilot_module._durably_create_directory(target)

    assert target.is_dir()
    assert synced == [existing, existing / "items", existing / "items" / "opaque"]


def test_mutable_python_import_overrides_are_rejected_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/mutable/source")
    with pytest.raises(PilotCheckpointError, match="PYTHONPATH"):
        pilot_module._assert_clean_runtime_environment()
    assert "PYTHONPATH" not in pilot_module._runtime_environment()


def test_native_runtime_overrides_are_rejected_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_PRELOAD", "/mutable/liboverride.so")

    with pytest.raises(PilotCheckpointError, match="LD_PRELOAD"):
        pilot_module._assert_clean_runtime_environment()
    assert "LD_PRELOAD" not in pilot_module._runtime_environment()


def test_path_arguments_are_resolved_before_child_working_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(
        corpus_manifest=Path("corpus.json"),
        config=Path("config.json"),
        out=Path("reports/pilot.json"),
        checkpoint_dir=Path("checkpoints/pilot"),
        leak_manifest=Path("leaks.json"),
        salt_file=Path("salt"),
        public_metadata=Path("public.json"),
        mapper_root=Path("mapper"),
        enrich_root=Path("enrich"),
    )

    pilot_module._resolve_path_arguments(args)

    assert args.checkpoint_dir == tmp_path / "checkpoints" / "pilot"
    assert args.mapper_root == tmp_path / "mapper"
    assert all(
        getattr(args, name).is_absolute()
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
    )
    assert pilot_module._command_path(args.corpus_manifest) == str(
        args.corpus_manifest
    )


def test_command_path_preserves_repository_relative_spelling() -> None:
    path = pilot_module.FOLIO_RESOLVE_ROOT / "eval" / "synthetic" / "corpus.json"

    assert pilot_module._command_path(path) == "eval/synthetic/corpus.json"


def test_checkpoint_manifest_is_create_once_and_fingerprint_bound(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    expected = _checkpoint_manifest(fingerprint={"git": "abc"}, item_ids=("one", "none"))

    _create_or_validate_manifest(path, expected)
    _create_or_validate_manifest(path, expected)

    with pytest.raises(PilotCheckpointError, match="fingerprint"):
        _create_or_validate_manifest(
            path,
            _checkpoint_manifest(fingerprint={"git": "changed"}, item_ids=("one", "none")),
        )


def test_consumer_environment_fingerprint_uses_probe_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "schema_version": 1,
        "interpreter_path_sha256": "a" * 64,
        "interpreter_sha256": "b" * 64,
        "python_version": "3.11",
        "distribution_count": 10,
        "distributions_sha256": "c" * 64,
        "installed_file_count": 50,
        "installed_file_bytes": 1_000,
        "installed_files_sha256": "e" * 64,
        "site_file_count": 60,
        "site_file_bytes": 1_200,
        "site_files_sha256": "3" * 64,
        "stdlib_file_count": 500,
        "stdlib_file_bytes": 2_000_000,
        "stdlib_files_sha256": "6" * 64,
        "editable_source_files": 7,
        "editable_source_bytes": 700,
        "editable_sources_sha256": "f" * 64,
        "import_path_entries": 5,
        "import_path_sha256": "1" * 64,
        "meta_path_entries": 5,
        "meta_path_sha256": "4" * 64,
        "path_hook_entries": 2,
        "path_hooks_sha256": "5" * 64,
        "model_asset_files": 5,
        "model_asset_bytes": 100,
        "model_assets_present": True,
        "model_assets_complete": True,
        "model_assets_sha256": "d" * 64,
        "model_embedding_dimension": 384,
        "model_snapshot_revision_sha256": "2" * 64,
    }
    observed: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        observed.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(pilot_module.subprocess, "run", fake_run)
    interpreter = tmp_path / "consumer" / "bin" / "python"
    venv_path_sha256 = pilot_module.sha256_bytes(str(interpreter.absolute()).encode())

    assert _consumer_environment_fingerprint(interpreter) == {
        **payload,
        "venv_path_sha256": venv_path_sha256,
    }
    assert observed[0][:4] == [str(interpreter), "-B", "-P", "-c"]

    payload["model_assets_complete"] = False
    with pytest.raises(PilotCheckpointError, match="load completely offline"):
        _consumer_environment_fingerprint(interpreter, require_model_assets=True)


def test_environment_probe_hashes_editable_source_bytes(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    dist_info = site / "demo-1.0.dist-info"
    source_root = tmp_path / "project" / "src"
    package = source_root / "demo"
    dist_info.mkdir(parents=True)
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    (site / "demo.pth").write_text(f"{source_root}\n", encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": (tmp_path / "project").as_uri(), "dir_info": {"editable": True}}),
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "demo.pth,,\n"
        "demo-1.0.dist-info/METADATA,,\n"
        "demo-1.0.dist-info/RECORD,,\n"
        "demo-1.0.dist-info/direct_url.json,,\n",
        encoding="utf-8",
    )

    def probe() -> dict[str, object]:
        completed = pilot_module.subprocess.run(
            [
                interpreter,
                "-B",
                "-P",
                "-c",
                pilot_module._CONSUMER_ENVIRONMENT_PROBE,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    before = probe()
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after = probe()

    assert before["editable_source_files"] == 1
    assert before["editable_sources_sha256"] != after["editable_sources_sha256"]


def test_environment_probe_rejects_unowned_pth_import_root(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    dist_info = site / "demo-1.0.dist-info"
    source_root = tmp_path / "unowned"
    dist_info.mkdir(parents=True)
    source_root.mkdir()
    (source_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site / "demo.pth").write_text(f"{source_root}\n", encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "demo.pth,,\n"
        "demo-1.0.dist-info/METADATA,,\n"
        "demo-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unowned .pth import root" in completed.stderr


def test_environment_probe_rejects_executable_pth_path_injection(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    dist_info = site / "hook-1.0.dist-info"
    source_root = tmp_path / "mutable-hook-source"
    dist_info.mkdir(parents=True)
    source_root.mkdir()
    (source_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site / "hook.pth").write_text(
        f"import sys; sys.path.insert(0, {str(source_root)!r})\n",
        encoding="utf-8",
    )
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: hook\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "hook.pth,,\n"
        "hook-1.0.dist-info/METADATA,,\n"
        "hook-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unsupported executable .pth line" in completed.stderr


def test_environment_probe_rejects_executable_pth_meta_path_hook(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    (site / "_virtualenv.pth").write_text("import _virtualenv\n", encoding="utf-8")
    (site / "_virtualenv.py").write_text(
        "import sys\n"
        "sys.meta_path.insert(0, type('InjectedFinder', (), "
        "{'find_spec': lambda self, *args: None})())\n",
        encoding="utf-8",
    )

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unsupported meta-path finder" in completed.stderr


def test_environment_probe_rejects_sitecustomize(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    (site / "sitecustomize.py").write_text("MARKER = True\n", encoding="utf-8")

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "startup customization module" in completed.stderr


def test_environment_probe_rejects_unowned_executable_pth(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    (site / "unowned.pth").write_text(
        "import sys; sys.audit('unowned-hook')\n", encoding="utf-8"
    )

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unsupported executable .pth line" in completed.stderr


def test_environment_probe_rejects_symlinked_package_directory(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    external_package = tmp_path / "mutable-package"
    external_package.mkdir()
    (external_package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site / "linked_package").symlink_to(external_package, target_is_directory=True)

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "symlinked site-packages entry" in completed.stderr


def test_environment_probe_rejects_symlinked_model_cache_directory(
    tmp_path: Path,
) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    hub = site / "huggingface_hub"
    model_root = tmp_path / "cache" / "models--sentence-transformers--all-MiniLM-L6-v2"
    external = tmp_path / "mutable-model-directory"
    hub.mkdir()
    model_root.mkdir(parents=True)
    external.mkdir()
    (hub / "__init__.py").write_text("", encoding="utf-8")
    (hub / "constants.py").write_text(
        f"HF_HUB_CACHE = {str(tmp_path / 'cache')!r}\n", encoding="utf-8"
    )
    (external / "config.json").write_text("{}\n", encoding="utf-8")
    (model_root / "linked").symlink_to(external, target_is_directory=True)

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "symlinked model-cache directory" in completed.stderr


def test_environment_probe_rejects_symlinked_editable_directory(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    project = tmp_path / "project"
    source_root = project / "src"
    package = source_root / "demo"
    external_package = tmp_path / "mutable-package"
    dist_info = site / "demo-1.0.dist-info"
    package.mkdir(parents=True)
    external_package.mkdir()
    dist_info.mkdir()
    (external_package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "linked").symlink_to(external_package, target_is_directory=True)
    (site / "demo.pth").write_text(f"{source_root}\n", encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": project.as_uri(), "dir_info": {"editable": True}}),
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "demo.pth,,\n"
        "demo-1.0.dist-info/METADATA,,\n"
        "demo-1.0.dist-info/RECORD,,\n"
        "demo-1.0.dist-info/direct_url.json,,\n",
        encoding="utf-8",
    )

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "symlinked editable source entry" in completed.stderr


def test_environment_probe_hashes_unowned_site_package_files(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    module = site / "manually_copied.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")

    def probe() -> dict[str, object]:
        completed = pilot_module.subprocess.run(
            [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    before = probe()
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after = probe()

    assert before["site_file_count"] == after["site_file_count"]
    assert before["site_files_sha256"] != after["site_files_sha256"]


def test_environment_probe_hashes_standard_library(tmp_path: Path) -> None:
    interpreter, _site = _isolated_python_site(tmp_path)

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["stdlib_file_count"] > 0
    assert payload["stdlib_file_bytes"] > 0
    assert len(payload["stdlib_files_sha256"]) == 64


def test_incumbents_are_prepared_once_before_read_only_fingerprinting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    mapper = SimpleNamespace(
        name="folio-mapper",
        repo_root=tmp_path / "mapper",
        venv_python=tmp_path / "mapper" / "backend" / ".venv" / "bin" / "python",
    )
    enrich = SimpleNamespace(
        name="folio-enrich",
        repo_root=tmp_path / "enrich",
        venv_python=tmp_path / "enrich" / "backend" / ".venv" / "bin" / "python",
    )
    monkeypatch.setattr(pilot_module, "mapper_spec", lambda _root: mapper)
    monkeypatch.setattr(pilot_module, "enrich_spec", lambda _root: enrich)
    monkeypatch.setattr(
        pilot_module,
        "prepare_incumbent",
        lambda spec, _version: events.append(f"prepare:{spec.name}"),
    )
    monkeypatch.setattr(
        pilot_module,
        "_consumer_environment_fingerprint",
        lambda path, **_kwargs: events.append(f"probe:{path}") or {},
    )
    monkeypatch.setattr(pilot_module, "_git_repository_state", lambda _root: {})
    monkeypatch.setattr(pilot_module, "_sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(pilot_module, "load_config", lambda _path: AnswerRuleConfig())
    monkeypatch.setattr(
        pilot_module,
        "assert_ontology_pin",
        lambda expected: events.append(f"pin:{expected}")
        or SimpleNamespace(sha256=expected),
    )
    monkeypatch.setattr(pilot_module.importlib.metadata, "version", lambda _name: "0.4.0")

    pilot_module._prepare_incumbents(mapper.repo_root, enrich.repo_root)
    fingerprint = _fingerprint(
        corpus=_corpus(tmp_path),
        config_path=Path("config.json"),
        leak_manifest_path=Path("leaks.json"),
        salt_file_path=Path("salt"),
        public_metadata_path=Path("public.json"),
        mapper_root=mapper.repo_root,
        enrich_root=enrich.repo_root,
        limit=1,
    )

    assert events == [
        "prepare:folio-mapper",
        "prepare:folio-enrich",
        "pin:ontology",
        f"probe:{Path(pilot_module.sys.executable)}",
        f"probe:{mapper.venv_python}",
        f"probe:{enrich.venv_python}",
    ]
    assert fingerprint["incumbent_version"] == "0.4.0"
    assert fingerprint["ontology_cache_sha256"] == "ontology"
    assert fingerprint["salt_file_sha256"] == "a" * 64


def test_existing_checkpoint_skips_incumbent_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "manifest.json").write_text("{}\n", encoding="utf-8")
    prepared: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        pilot_module,
        "_prepare_incumbents",
        lambda mapper, enrich: prepared.append((mapper, enrich)),
    )
    args = SimpleNamespace(
        checkpoint_dir=checkpoint,
        mapper_root=tmp_path / "mapper",
        enrich_root=tmp_path / "enrich",
    )

    pilot_module._prepare_incumbents_for_new_checkpoint(args)

    assert prepared == []


def test_environment_probe_ignores_inactive_base_package_trees(tmp_path: Path) -> None:
    interpreter, site = _isolated_python_site(tmp_path)
    inactive = site.parent / "dist-packages"
    external = tmp_path / "external-package"
    inactive.mkdir()
    external.mkdir()
    (external / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (inactive / "linked").symlink_to(external, target_is_directory=True)

    completed = pilot_module.subprocess.run(
        [interpreter, "-B", "-P", "-c", pilot_module._CONSUMER_ENVIRONMENT_PROBE],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["stdlib_file_count"] > 0


def test_finalization_invocation_records_supplied_input_paths(tmp_path: Path) -> None:
    args = SimpleNamespace(
        corpus_manifest=Path("custom/corpus.json"),
        config=Path("custom/config.json"),
        out=Path("custom/report.json"),
        checkpoint_dir=tmp_path / "checkpoint",
        leak_manifest=Path("custom/leaks.json"),
        salt_file=Path("custom/salt"),
        public_metadata=Path("custom/public.json"),
        mapper_root=Path("custom/mapper"),
        enrich_root=Path("custom/enrich"),
        limit=7,
    )
    receipt = _finalization_invocation(args, tmp_path / "combined.jsonl")
    argv = receipt["argv"]

    assert argv[argv.index("--corpus-manifest") + 1] == "custom/corpus.json"
    assert argv[argv.index("--config") + 1] == "custom/config.json"
    assert argv[argv.index("--leak-manifest") + 1] == "custom/leaks.json"
    assert argv[argv.index("--public-metadata") + 1] == "custom/public.json"
    assert argv[argv.index("--mapper-root") + 1] == "custom/mapper"
    assert argv[argv.index("--enrich-root") + 1] == "custom/enrich"
    assert argv[argv.index("--incumbent-version") + 1] == "0.4.0"
    assert "--skip-incumbent-prepare" in argv
    assert receipt["environment"] == {
        "ACCELERATE_USE_CPU": "true",
        "CUDA_VISIBLE_DEVICES": "",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "HIP_VISIBLE_DEVICES": "",
        "MKL_DYNAMIC": "FALSE",
        "MKL_NUM_THREADS": "1",
        "NVIDIA_VISIBLE_DEVICES": "none",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_DYNAMIC": "FALSE",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": pilot_module.os.environ.get("PYTHONHASHSEED", ""),
        "ROCR_VISIBLE_DEVICES": "",
        "TOKENIZERS_PARALLELISM": "false",
        "TRANSFORMERS_OFFLINE": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    }


def test_load_shard_rejects_item_or_stack_drift(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(_shard("one")), encoding="utf-8")
    assert _load_shard(path, "one")["run_kind"] == "shard"

    with pytest.raises(PilotCheckpointError, match="item mismatch"):
        _load_shard(path, "two")

    payload = _shard("one")
    del payload["stacks"]["folio-mapper:incumbent"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PilotCheckpointError, match="stack set"):
        _load_shard(path, "one")


def test_load_shard_is_bound_to_checkpoint_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    payload = _shard("one")
    payload["corpus"] = {"content_sha256": "corpus", "nomatch_content_sha256": "nomatch"}
    payload["folio_python_version"] = "0.3.6"
    payload["provenance"]["config_selection"] = {"answer_rule_config_sha256": "config"}
    fingerprint = {
        "corpus_content_sha256": "corpus",
        "nomatch_content_sha256": "nomatch",
        "answer_rule_config_sha256": "config",
        "folio_python_version": "0.3.6",
        "folio_resolve_version": "0.4.0",
        "candidate_repository": payload["stacks"]["folio-resolve:candidate"]["repository"],
        "enrich_repository": payload["stacks"]["folio-enrich:incumbent"]["repository"],
        "mapper_repository": payload["stacks"]["folio-mapper:incumbent"]["repository"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert _load_shard(path, "one", fingerprint)["run_kind"] == "shard"
    fingerprint["answer_rule_config_sha256"] = "changed"
    with pytest.raises(PilotCheckpointError, match="answer-rule config"):
        _load_shard(path, "one", fingerprint)


def test_merge_stack_runs_preserves_every_item_and_rejects_static_drift() -> None:
    shards = [_shard("one"), _shard("none", nomatch=True)]
    runs = _merge_stack_runs(shards)

    assert len(runs) == 3
    assert all(set(run.rows) == {"one", "none"} for run in runs)
    assert all(set(run.stages) == {"one", "none"} for run in runs)
    assert all(run.invocation_kind == "equivalent_checkpoint_aggregate" for run in runs)
    mapper = next(run for run in runs if run.stack == "folio-mapper")
    assert mapper.invocation[0] == "folio_eval.comparison_pilot.aggregate_consumer_stack"
    assert mapper.invocation[mapper.invocation.index("--source-shard-count") + 1] == "2"

    changed_invocation = deepcopy(shards)
    changed_invocation[1]["stacks"]["folio-mapper:incumbent"]["invocation"]["argv"].append(
        "changed"
    )
    changed_mapper = next(
        run for run in _merge_stack_runs(changed_invocation) if run.stack == "folio-mapper"
    )
    assert changed_mapper.invocation[-1] != mapper.invocation[-1]

    drifted = deepcopy(shards)
    drifted[1]["stacks"]["folio-mapper:incumbent"]["repository"] = {"git_sha": "b" * 40}
    with pytest.raises(PilotCheckpointError, match="repository drifted"):
        _merge_stack_runs(drifted)


def test_run_shard_uses_one_explicit_item_and_suppresses_large_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}
    events: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pilot_module.subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", "/mutable/source")
    monkeypatch.setattr(
        pilot_module,
        "_durably_create_directory",
        lambda path: events.append(f"directory:{path.name}"),
    )
    monkeypatch.setattr(
        pilot_module,
        "_durably_sync_file",
        lambda _path: events.append("durable"),
    )
    monkeypatch.setattr(
        pilot_module,
        "_load_shard",
        lambda *_args, **_kwargs: events.append("loaded") or {},
    )
    args = SimpleNamespace(
        checkpoint_dir=tmp_path / "checkpoint",
        corpus_manifest=Path("eval/synthetic/corpus_v1.manifest.json"),
        config=Path("eval/synthetic/answer_rule_config_synthetic_v1.json"),
        leak_manifest=Path("eval/synthetic/firm-surface-manifest-v1.json"),
        salt_file=Path("eval/data/leakcheck-salt"),
        public_metadata=Path("eval/synthetic/public_comparison_metadata_v1.json"),
        mapper_root=Path("/repos/mapper"),
        enrich_root=Path("/repos/enrich"),
    )

    _run_shard(args, "one", {})

    command = observed["command"]
    assert command[command.index("--item-id") + 1] == "one"
    assert "--skip-incumbent-prepare" in command
    assert command[command.index("--corpus-manifest") + 1] == (
        "eval/synthetic/corpus_v1.manifest.json"
    )
    assert command[command.index("--config") + 1] == (
        "eval/synthetic/answer_rule_config_synthetic_v1.json"
    )
    assert command[command.index("--leak-manifest") + 1] == (
        "eval/synthetic/firm-surface-manifest-v1.json"
    )
    assert observed["stdout"] is pilot_module.subprocess.DEVNULL
    assert observed["cwd"] == pilot_module.FOLIO_RESOLVE_ROOT
    assert "PYTHONPATH" not in observed["env"]
    assert observed["env"]["HF_HUB_OFFLINE"] == "1"
    assert observed["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert observed["env"]["ACCELERATE_USE_CPU"] == "true"
    assert observed["env"]["CUDA_VISIBLE_DEVICES"] == ""
    assert observed["env"]["OMP_NUM_THREADS"] == "1"
    assert events == ["directory:stages", "durable", "loaded"]


def test_finalize_durably_creates_output_parent_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    salt_file = tmp_path / "salt"
    salt_file.write_bytes(b"salt")
    output = tmp_path / "new-report-directory" / "report.json"
    args = SimpleNamespace(
        checkpoint_dir=checkpoint,
        leak_manifest=tmp_path / "leaks.json",
        salt_file=salt_file,
        limit=1,
        config=tmp_path / "config.json",
        out=output,
        public_metadata=tmp_path / "public.json",
    )
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(pilot_module, "_load_shard", lambda *_args: _shard("one"))
    monkeypatch.setattr(
        pilot_module,
        "_merge_stack_runs",
        lambda _shards: [SimpleNamespace(stack="folio-mapper", lane="incumbent")],
    )
    monkeypatch.setattr(pilot_module, "load_manifest", lambda _path: object())
    monkeypatch.setattr(pilot_module, "emit_items_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pilot_module, "write_stage_snapshots", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(pilot_module, "_finalization_invocation", lambda *_args: {})
    monkeypatch.setattr(pilot_module, "load_config", lambda _path: object())
    monkeypatch.setattr(
        pilot_module,
        "build_comparison",
        lambda *_args, **_kwargs: {"provenance": {}},
    )
    monkeypatch.setattr(pilot_module, "_file_fingerprint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(pilot_module, "_sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(
        pilot_module, "load_public_comparison_metadata", lambda _path: object()
    )
    monkeypatch.setattr(
        pilot_module,
        "_require_current_fingerprint",
        lambda _args, _corpus, _expected, *, boundary: events.append(
            ("fingerprint", boundary)
        ),
    )

    def durable_create(path: Path) -> None:
        events.append(("directory", path))
        path.mkdir(parents=True)

    def publish(path: Path, *_args: object, **_kwargs: object) -> None:
        assert path.parent.is_dir()
        events.append(("publish", path))

    monkeypatch.setattr(pilot_module, "_durably_create_directory", durable_create)
    monkeypatch.setattr(pilot_module, "write_comparison", publish)

    pilot_module._finalize(args, _corpus(tmp_path), ("one",), {"fingerprint": {}})

    assert events == [
        ("directory", checkpoint / "final-stages" / "folio-mapper" / "incumbent"),
        ("fingerprint", "before final report publication"),
        ("directory", output.parent),
        ("publish", output),
    ]


def test_main_revalidates_fingerprint_before_and_after_each_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint"
    fingerprint_calls: list[int] = []
    fingerprint = {"stable": True}
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setattr(pilot_module, "load_corpus", lambda _path: _corpus(tmp_path))
    monkeypatch.setattr(pilot_module, "_prepare_incumbents", lambda *_args: None)
    monkeypatch.setattr(pilot_module, "_durably_create_directory", lambda _path: None)
    monkeypatch.setattr(
        pilot_module,
        "_fingerprint",
        lambda **_kwargs: fingerprint_calls.append(1) or fingerprint,
    )
    monkeypatch.setattr(pilot_module, "_create_or_validate_manifest", lambda *_args: None)

    def run_shard(args: SimpleNamespace, item_id: str, _fingerprint: object) -> None:
        report = pilot_module._shard_paths(args.checkpoint_dir, item_id)[0]
        report.parent.mkdir(parents=True)
        report.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pilot_module, "_run_shard", run_shard)

    assert pilot_module.main([
        "--corpus-manifest", "corpus.json",
        "--config", "config.json",
        "--out", "pilot.json",
        "--checkpoint-dir", str(checkpoint),
        "--leak-manifest", "leaks.json",
        "--salt-file", "salt",
        "--public-metadata", "public.json",
        "--mapper-root", "/repos/mapper",
        "--enrich-root", "/repos/enrich",
        "--limit", "1",
        "--max-new-items", "1",
    ]) == 0
    assert len(fingerprint_calls) == 3


def test_main_can_initialize_checkpoint_without_starting_an_expensive_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setattr(pilot_module, "load_corpus", lambda _path: _corpus(tmp_path))
    monkeypatch.setattr(pilot_module, "_prepare_incumbents", lambda *_args: None)
    monkeypatch.setattr(pilot_module, "_durably_create_directory", lambda _path: None)
    monkeypatch.setattr(pilot_module, "_fingerprint", lambda **_kwargs: {})
    monkeypatch.setattr(pilot_module, "_create_or_validate_manifest", lambda *_args: None)
    monkeypatch.setattr(
        pilot_module,
        "_run_shard",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not run a shard")),
    )

    assert pilot_module.main([
        "--corpus-manifest", "corpus.json",
        "--config", "config.json",
        "--out", "pilot.json",
        "--checkpoint-dir", str(tmp_path / "checkpoint"),
        "--leak-manifest", "leaks.json",
        "--salt-file", "salt",
        "--public-metadata", "public.json",
        "--mapper-root", "/repos/mapper",
        "--enrich-root", "/repos/enrich",
        "--limit", "1",
        "--max-new-items", "0",
    ]) == 0
