"""The scorecard entry point reads only generated, explicitly supplied evidence."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from folio_eval import adoption_scorecard as scorecard
from folio_eval.leakcheck import build_manifest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "eval/run_adoption_scorecard.py"
FIXTURE = ROOT / "eval/fixtures/adoption-scorecard-v1.json"


def explicit_args(tmp_path: Path, *, collision: bool = False) -> list[str]:
    salt = b"generated-scorecard-test-salt"
    salt_path = tmp_path / "generated.salt"
    salt_path.write_bytes(salt)
    manifest = build_manifest(
        ["Mechanical prerequisites" if collision else "generated confidential canary"],
        salt,
        gold_version="generated",
        gold_content_sha256="0" * 64,
    )
    manifest_path = tmp_path / "generated-manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_json()))
    return [
        "--input",
        str(FIXTURE),
        "--expected-sha256",
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "--leak-manifest",
        str(manifest_path),
        "--salt-file",
        str(salt_path),
    ]


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True, check=False
    )


def test_fixture_cli_outside_checkout_and_byte_identical_replay(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for out in (first, second):
        result = run_cli(["--fixture-demo", "--out-dir", str(out)], tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "scorecards_written\n"
        assert result.stderr == ""
    assert sorted(path.name for path in first.iterdir()) == ["scorecards.json", "scorecards.md"]
    for name in ("scorecards.json", "scorecards.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    report = json.loads((first / "scorecards.json").read_bytes())
    expected = scorecard.evaluate(
        scorecard.load_bundle(FIXTURE, hashlib.sha256(FIXTURE.read_bytes()).hexdigest()),
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
    )
    assert report == expected
    markdown = (first / "scorecards.md").read_text()
    assert "generated_fixture" in markdown
    assert "adoption is not authorized" in markdown
    assert "consumer_interval_not_positive" in markdown
    assert "2000" in markdown and "20260727" in markdown
    assert all(
        f"folio-mapper / {model}" in markdown
        for model in ("deterministic", "gemini-3-flash-preview", "gpt-6-luna")
    )


def test_explicit_input_and_real_leakcheck(tmp_path: Path) -> None:
    args = explicit_args(tmp_path)
    out = tmp_path / "safe"
    result = run_cli([*args, "--out-dir", str(out)], tmp_path)
    assert result.returncode == 0, result.stderr
    colliding = tmp_path / "colliding"
    args = explicit_args(tmp_path, collision=True)
    result = run_cli([*args, "--out-dir", str(colliding)], tmp_path)
    assert result.returncode == 2
    assert result.stderr.startswith("scorecard_error: ")
    assert result.stderr == "scorecard_error: leak_check_failed\n"
    assert not colliding.exists()
    assert (out / "scorecards.json").is_file()


@pytest.mark.parametrize(
    "case",
    [
        "missing_digest",
        "wrong_digest",
        "missing_manifest",
        "missing_salt",
        "missing_input",
        "both_modes",
        "unknown_argument",
    ],
)
def test_invalid_cli_is_content_free_and_publishes_nothing(tmp_path: Path, case: str) -> None:
    args = explicit_args(tmp_path)
    if case == "missing_digest":
        del args[2:4]
    elif case == "wrong_digest":
        args[3] = "DO-NOT-PRINT-CANARY"
    elif case == "missing_manifest":
        del args[4:6]
    elif case == "missing_salt":
        del args[6:8]
    elif case == "missing_input":
        args[1] = str(tmp_path / "DO-NOT-PRINT-CANARY")
    elif case == "both_modes":
        args.append("--fixture-demo")
    else:
        args.append("--DO-NOT-PRINT-CANARY")
    out = tmp_path / "out"
    result = run_cli([*args, "--out-dir", str(out)], tmp_path)
    assert result.returncode == 2
    assert result.stderr.startswith("scorecard_error: ")
    assert not out.exists()
    assert result.stdout == ""
    assert "CANARY" not in result.stderr
    assert str(tmp_path) not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("kind", ["directory", "file", "empty_directory", "dangling_symlink"])
def test_occupied_destination_is_untouched(tmp_path: Path, kind: str) -> None:
    out = tmp_path / "out"
    if kind in ("directory", "empty_directory"):
        out.mkdir()
        if kind == "directory":
            (out / "original").write_bytes(b"original")
    elif kind == "file":
        out.write_bytes(b"original")
    else:
        out.symlink_to(tmp_path / "missing")
    result = run_cli(["--fixture-demo", "--out-dir", str(out)], tmp_path)
    assert result.returncode == 2
    assert result.stderr.startswith("scorecard_error: ")
    if kind == "directory":
        assert (out / "original").read_bytes() == b"original"
    elif kind == "file":
        assert out.read_bytes() == b"original"
    elif kind == "empty_directory":
        assert list(out.iterdir()) == []
    else:
        assert out.is_symlink()


def test_no_network_process_or_protected_input_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unexpected external operation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    read_bytes, read_text = Path.read_bytes, Path.read_text

    def bounded_bytes(path: Path) -> bytes:
        assert path == FIXTURE
        return read_bytes(path)

    def bounded_text(path: Path, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("unexpected implicit file read")

    monkeypatch.setattr(Path, "read_bytes", bounded_bytes)
    monkeypatch.setattr(Path, "read_text", bounded_text)
    out = tmp_path / "out"
    assert scorecard.main(["--fixture-demo", "--out-dir", str(out)]) == 0
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


def test_output_omits_item_keys_and_invalid_metadata(tmp_path: Path) -> None:
    args = explicit_args(tmp_path)
    data = json.loads(FIXTURE.read_bytes())
    raw = json.dumps(data).replace("probe-1", "DO-NOT-PRINT-CANARY").encode()
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    args[1], args[3] = str(path), hashlib.sha256(raw).hexdigest()
    out = tmp_path / "out"
    result = run_cli([*args, "--out-dir", str(out)], tmp_path)
    assert result.returncode == 0, result.stderr
    for file in out.iterdir():
        assert b"DO-NOT-PRINT-CANARY" not in file.read_bytes()
        assert b"generated-concept" not in file.read_bytes()
    data["secret_metadata"] = "DO-NOT-PRINT-CANARY"
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    args[3] = hashlib.sha256(raw).hexdigest()
    result = run_cli([*args, "--out-dir", str(tmp_path / "bad")], tmp_path)
    assert result.returncode == 2
    assert result.stderr.startswith("scorecard_error: ")
    assert "CANARY" not in result.stderr


def test_failed_staging_and_publish_preserve_prior_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"

    def fail_publish(stage: Path, destination: Path) -> None:
        # Simulate another invocation winning the destination race.
        destination.mkdir()
        (destination / "original").write_bytes(b"original")
        raise OSError("DO-NOT-PRINT-CANARY")

    monkeypatch.setattr(scorecard, "_publish_directory", fail_publish)
    assert scorecard.main(["--fixture-demo", "--out-dir", str(out)]) != 0
    assert (out / "original").read_bytes() == b"original"
    assert not (out / "scorecards.json").exists()


def test_atomic_publish_refuses_an_existing_empty_directory(tmp_path: Path) -> None:
    stage, out = tmp_path / "stage", tmp_path / "out"
    stage.mkdir()
    (stage / "complete").write_bytes(b"complete")
    out.mkdir()
    with pytest.raises(OSError):
        scorecard._publish_directory(stage, out)
    assert list(out.iterdir()) == []
    assert (stage / "complete").read_bytes() == b"complete"


def test_failed_second_write_does_not_expose_half_a_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_open = Path.open

    def fail_markdown(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == "scorecards.md":
            raise OSError("DO-NOT-PRINT-CANARY")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_markdown)
    out = tmp_path / "out"
    assert scorecard.main(["--fixture-demo", "--out-dir", str(out)]) == 2
    assert not out.exists()
    assert capsys.readouterr().err == "scorecard_error: operation_failed\n"
    assert len(list(tmp_path.glob(".adoption-scorecard-*/scorecards.json"))) == 1


def test_fixture_mode_rejects_modified_fixture_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "edited-fixture.json"
    path.write_bytes(FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(scorecard, "FIXTURE_PATH", path)
    out = tmp_path / "out"
    assert scorecard.main(["--fixture-demo", "--out-dir", str(out)]) == 2
    assert not out.exists()
    assert capsys.readouterr().err == "scorecard_error: invalid_evidence\n"
