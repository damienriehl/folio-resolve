#!/usr/bin/env python3
"""Finalize a pinned U10 checkpoint using separately reviewed repair code."""

import sys

if not (sys.flags.isolated and sys.flags.safe_path and sys.flags.dont_write_bytecode):
    raise SystemExit("finalization repair requires Python isolated mode (-I -B)")

import os
import subprocess
import tempfile
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

_REPAIR_ROOT = Path(__file__).resolve().parents[1]
_IMPORTABLE_SUFFIXES = {".py", ".pyc", ".pyd", ".pyo", ".so"}


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(_REPAIR_ROOT), *args],
        capture_output=True,
        check=False,
    )


def _assert_no_ignored_importables() -> None:
    completed = _git(
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        "eval",
        "src",
    )
    if completed.returncode:
        raise SystemExit("finalization repair could not inspect ignored importables")
    ignored = completed.stdout.split(b"\0")
    if any(
        Path(os.fsdecode(path)).suffix.casefold() in _IMPORTABLE_SUFFIXES
        for path in ignored
        if path
    ):
        raise SystemExit("finalization repair checkout contains ignored importable files")


def _assert_repair_checkout_clean() -> None:
    completed = _git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if completed.returncode:
        raise SystemExit("finalization repair could not inspect checkout status")
    if completed.stdout:
        raise SystemExit("finalization repair checkout must be clean before project import")


def _assert_loaded_project_modules_are_tracked() -> None:
    for name, module in tuple(sys.modules.items()):
        if name != "folio_eval" and not name.startswith("folio_eval."):
            continue
        raw_path = getattr(module, "__file__", None)
        if not isinstance(raw_path, str):
            raise SystemExit("finalization repair loaded an unbound project module")
        path = Path(raw_path).resolve()
        try:
            relative = path.relative_to(_REPAIR_ROOT)
        except ValueError:
            raise SystemExit(
                "finalization repair loaded a project module outside its checkout"
            ) from None
        if path.suffix != ".py":
            raise SystemExit("finalization repair loaded a non-source project module")
        completed = _git("ls-files", "--error-unmatch", "--", relative.as_posix())
        if completed.returncode:
            raise SystemExit("finalization repair loaded an untracked project module")


_assert_no_ignored_importables()
_assert_repair_checkout_clean()
_BYTECODE_CACHE = tempfile.TemporaryDirectory(prefix="folio-u10-repair-bytecode-")
sys.pycache_prefix = _BYTECODE_CACHE.name
sys.path.insert(0, str(_REPAIR_ROOT / "eval"))

from folio_eval.comparison_pilot_repair import main  # noqa: E402

_assert_loaded_project_modules_are_tracked()

if __name__ == "__main__":
    raise SystemExit(main())
