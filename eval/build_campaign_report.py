"""Build the deterministic, aggregate-only U13 campaign report."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.campaign_report import (
    CampaignReportError,
    load_campaign_inputs,
    render_campaign_report,
)
from folio_eval.report import _atomic_write_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble the aggregate-only U13 campaign report from committed inputs."
    )
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--comparison-v1", type=Path, required=True)
    parser.add_argument("--comparison-v2", type=Path)
    parser.add_argument("--parity-map", type=Path, required=True)
    parser.add_argument("--surface-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def _generator_command(args: argparse.Namespace) -> str:
    command = [
        "uv",
        "run",
        "python",
        "eval/build_campaign_report.py",
        "--ledger",
        str(args.ledger),
        "--comparison-v1",
        str(args.comparison_v1),
    ]
    if args.comparison_v2 is not None:
        command.extend(("--comparison-v2", str(args.comparison_v2)))
    command.extend(
        (
            "--parity-map",
            str(args.parity_map),
            "--surface-manifest",
            str(args.surface_manifest),
        )
    )
    return (
        f"{shlex.join(command)} --salt-file <OWNER_LOCAL_LEAKCHECK_SALT_FILE> "
        "--out <OUTPUT_PATH>"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_campaign_inputs(
            ledger_path=args.ledger,
            comparison_v1_path=args.comparison_v1,
            comparison_v2_path=args.comparison_v2,
            parity_map_path=args.parity_map,
        )
        report = render_campaign_report(
            inputs,
            generator_command=_generator_command(args),
            surface_manifest_path=args.surface_manifest,
            salt_file_path=args.salt_file,
        )
        _atomic_write_text(args.out, report)
    except (CampaignReportError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
