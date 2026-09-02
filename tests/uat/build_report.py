from __future__ import annotations

import argparse
import importlib.util
import platform
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_ROOT = _REPO_ROOT / "eval"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from folio_eval.downstream import parse_junitxml  # noqa: E402

_STORY_HEADING = re.compile(r"^###\s+(US-([A-Z]{2})-\d{2})\b", re.MULTILINE)
_STORY_NAME = re.compile(r"(?:test_)?us_([a-z]{2})_(\d{2})(?:_|$)", re.IGNORECASE)
_STORY_TEXT = re.compile(r"\bUS-([A-Z]{2})-(\d{2})\b", re.IGNORECASE)
_CLASSIFIED_REASON = re.compile(
    r"^(library defect|documentation drift|test defect):\s+(.+)$",
    re.IGNORECASE,
)
_REASON_LIMIT = 120
_VERDICT_PRIORITY = {"untested": 0, "skip": 1, "pass": 2, "fail (classified)": 3, "fail": 4}


@dataclass(frozen=True)
class TestResult:
    nodeid: str
    story_id: str | None
    verdict: str
    reason: str


def _story_catalog(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    stories: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _STORY_HEADING.finditer(text):
        story_id = match.group(1)
        if story_id in seen:
            raise ValueError(f"duplicate story ID: {story_id}")
        seen.add(story_id)
        stories.append((story_id, match.group(2)))
    return stories


def _nodeid(case: ET.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "")
    return f"{classname}::{name}" if classname else name


def _property_story_id(case: ET.Element) -> str | None:
    for prop in case.findall("./properties/property"):
        if (
            prop.get("name") == "story_id"
            and (value := prop.get("value"))
            and (match := _STORY_TEXT.search(value))
        ):
            return f"US-{match.group(1).upper()}-{match.group(2)}"
    return None


def _name_story_id(case: ET.Element) -> str | None:
    if match := _STORY_NAME.search(case.get("name", "")):
        return f"US-{match.group(1).upper()}-{match.group(2)}"
    return None


def _case_reason(case: ET.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        child = case.find(tag)
        if child is not None:
            return child.get("message") or (child.text or "")
    return ""


def _truncate_reason(reason: str) -> str:
    compact = " ".join(reason.split())
    if len(compact) > _REASON_LIMIT:
        compact = compact[: _REASON_LIMIT - 1].rstrip() + "…"
    return compact.replace("|", "\\|")


def _results(path: Path) -> list[TestResult]:
    outcomes = parse_junitxml(path)
    root = ET.parse(path).getroot()
    results: list[TestResult] = []
    for case in root.iter("testcase"):
        nodeid = _nodeid(case)
        raw_verdict = outcomes.get(nodeid, "passed")
        verdict = {
            "passed": "pass",
            "failed": "fail",
            "error": "fail",
            "skipped": "skip",
        }.get(raw_verdict, "fail")
        reason = _case_reason(case)
        if verdict == "skip" and _CLASSIFIED_REASON.match(" ".join(reason.split())):
            verdict = "fail (classified)"
        results.append(
            TestResult(
                nodeid=nodeid,
                story_id=_property_story_id(case) or _name_story_id(case),
                verdict=verdict,
                reason=_truncate_reason(reason),
            )
        )
    return results


def _story_result(story_id: str, results: Sequence[TestResult]) -> tuple[str, str]:
    matching = [result for result in results if result.story_id == story_id]
    if not matching:
        return "untested", ""
    selected = max(matching, key=lambda result: _VERDICT_PRIORITY[result.verdict])
    reasons = [result.reason for result in matching if result.reason]
    return selected.verdict, _truncate_reason("; ".join(reasons))


def _extras_present() -> dict[str, bool]:
    modules = {
        "folio": ("folio",),
        "spacy": ("spacy",),
        "embedding": ("faiss", "sentence_transformers", "numpy"),
    }

    def available(module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    return {
        extra: all(available(module) for module in required) for extra, required in modules.items()
    }


def _commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _rerun_commands(
    junit: Path,
    stories: Path,
    output: Path,
    core_junit: Path | None,
) -> list[str]:
    pytest_command = [
        ".venv/bin/python",
        "-m",
        "pytest",
        "tests/uat",
        "-m",
        "uat",
        f"--junitxml={junit}",
    ]
    report_command = [
        ".venv/bin/python",
        "tests/uat/build_report.py",
        "--junit",
        str(junit),
        "--stories",
        str(stories),
        "--out",
        str(output),
    ]
    if core_junit is not None:
        report_command.extend(["--core-junit", str(core_junit)])
    return [shlex.join(pytest_command), shlex.join(report_command)]


def build_markdown(
    *,
    junit: Path,
    stories_path: Path,
    output: Path,
    core_junit: Path | None = None,
) -> str:
    stories = _story_catalog(stories_path)
    results = _results(junit)
    core_results = _results(core_junit) if core_junit is not None else []
    extras = _extras_present()

    lines = [
        "# User acceptance test report",
        "",
        f"- Commit: `{_commit()}`",
        f"- Python: `{platform.python_version()}`",
        "- Extras present: "
        + ", ".join(f"`{name}={str(value).lower()}`" for name, value in extras.items()),
        "",
        "## Rerun commands",
        "",
    ]
    lines.extend(
        f"- `{command}`" for command in _rerun_commands(junit, stories_path, output, core_junit)
    )
    lines.extend(
        [
            "",
            "## Story verdicts",
            "",
            "| Story ID | Persona | Verdict | Core-run verdict | Skip/fail reason |",
            "|---|---|---|---|---|",
        ]
    )

    summary = {"pass": 0, "fail": 0, "skip": 0, "untested": 0}
    for story_id, persona in stories:
        verdict, reason = _story_result(story_id, results)
        core_verdict = _story_result(story_id, core_results)[0] if core_junit is not None else ""
        summary["fail" if verdict.startswith("fail") else verdict] += 1
        lines.append(f"| {story_id} | {persona} | {verdict} | {core_verdict} | {reason} |")

    unmapped = [result for result in [*results, *core_results] if result.story_id is None]
    lines.extend(["", "## Unmapped tests", ""])
    if unmapped:
        seen_unmapped: set[tuple[str, str]] = set()
        for result in unmapped:
            key = (result.nodeid, result.verdict)
            if key in seen_unmapped:
                continue
            seen_unmapped.add(key)
            lines.append(f"- `{result.nodeid}` ({result.verdict})")
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            ", ".join(f"{name}: {count}" for name, count in summary.items()),
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the persona UAT markdown report")
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--stories", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--core-junit", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    markdown = build_markdown(
        junit=args.junit,
        stories_path=args.stories,
        output=args.out,
        core_junit=args.core_junit,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
