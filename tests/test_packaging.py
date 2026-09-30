"""Verify typing metadata in the artifact consumers actually install."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def test_wheel_contains_py_typed(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv build unavailable: uv is not installed")
    built = subprocess.run(
        [uv, "build", "--offline", "--wheel", "--out-dir", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    output = built.stdout + built.stderr
    if built.returncode and any(
        reason in output.lower()
        for reason in (
            "not found in the cache", "not available in the cache",
            "network connectivity is disabled", "network is disabled", "not found in cache",
        )
    ):
        pytest.skip(f"uv build unavailable offline: {output.strip()}")
    assert built.returncode == 0, output
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert "folio_resolve/py.typed" in wheel.namelist()
