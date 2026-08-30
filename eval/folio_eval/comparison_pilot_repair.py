"""Finalize a checkpoint against its pinned candidate using separately reviewed code."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .comparison import ComparisonError, _git_repository_state
from .comparison_pilot import (
    FINALIZATION_REPAIR_KIND,
    PUBLISHED_COMPARISON_REPORT,
    PilotCheckpointError,
)
from .comparison_pilot import (
    main as pilot_main,
)

REPAIR_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _repair_identity(root: Path = REPAIR_REPOSITORY_ROOT) -> dict[str, str]:
    """Bind finalization to one clean commit and its complete tracked tree."""
    try:
        repository = _git_repository_state(root)
    except ComparisonError as exc:
        raise PilotCheckpointError("finalization repair repository must be clean") from exc
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--full-tree", "HEAD"],
        capture_output=True,
        check=False,
    )
    if completed.returncode or not completed.stdout:
        raise PilotCheckpointError("could not fingerprint finalization repair source")
    return {
        "git_sha": str(repository["git_sha"]),
        "kind": FINALIZATION_REPAIR_KIND,
        "source_sha256": hashlib.sha256(completed.stdout).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--candidate-root", type=Path, required=True)
    return parser


def _option_value(argv: Sequence[str], option: str) -> str:
    values: list[str] = []
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise PilotCheckpointError(f"finalization repair requires exactly one {option}")
            values.append(argv[index + 1])
        elif token.startswith(f"{option}="):
            values.append(token.split("=", 1)[1])
    if len(values) != 1 or not values[0]:
        raise PilotCheckpointError(f"finalization repair requires exactly one {option}")
    return values[0]


def main(argv: Sequence[str] | None = None) -> int:
    candidate_args, pilot_argv = _parser().parse_known_args(argv)
    candidate_root = candidate_args.candidate_root.resolve()
    repair_root = REPAIR_REPOSITORY_ROOT.resolve()
    if candidate_root == repair_root:
        raise PilotCheckpointError("repair and candidate roots must be separate checkouts")
    if "--finalize-only" not in pilot_argv:
        raise PilotCheckpointError("finalization repair requires --finalize-only")
    if any(
        token == "--max-new-items" or token.startswith("--max-new-items=")
        for token in pilot_argv
    ):
        raise PilotCheckpointError("finalization repair cannot configure shard execution")
    output_path = Path(_option_value(pilot_argv, "--out")).resolve()
    if output_path != candidate_root / PUBLISHED_COMPARISON_REPORT:
        raise PilotCheckpointError("finalization repair requires the canonical report path")
    identity = _repair_identity(repair_root)
    return pilot_main(
        pilot_argv,
        candidate_root=candidate_root,
        repair_identity=identity,
    )
