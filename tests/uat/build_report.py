from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
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
    r"^(library defect|documentation drift|test defect): (\S(?:.*\S)?)$",
)
_LINK_TARGET = re.compile(r"(?:https?://\S+|(?:\.?\.?/)?(?:[^/\s]+/)+[^/\s]+)$")
_REASON_LIMIT = 120
_VERDICT_PRIORITY = {"untested": 0, "skip": 1, "pass": 2, "fail (classified)": 3, "fail": 4}
_METADATA_PROPERTIES = (
    "python_version",
    "extras_folio",
    "extras_spacy",
    "extras_embedding",
    "real_ontology_enabled",
    "commit",
)


@dataclass(frozen=True)
class TestResult:
    nodeid: str
    story_id: str | None
    verdict: str
    reason: str


@dataclass(frozen=True)
class ReportMetadata:
    python_version: str
    extras: Mapping[str, str]
    real_ontology_enabled: str
    commit: str
    source: str


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


def _classified_skip_reason(case: ET.Element) -> str | None:
    skipped = case.find("skipped")
    if skipped is None or skipped.get("type") != "pytest.xfail":
        return None
    match = _CLASSIFIED_REASON.fullmatch(skipped.get("message") or "")
    if match is None:
        return None
    classification, target = match.groups()
    rendered_target = target.replace("|", "\\|")
    if _LINK_TARGET.fullmatch(target):
        rendered_target = f"[{rendered_target}]({target})"
    return f"{classification}: {rendered_target}"


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
        classified_reason = _classified_skip_reason(case) if verdict == "skip" else None
        if classified_reason is not None:
            verdict = "fail (classified)"
            reason = classified_reason
        else:
            reason = _truncate_reason(reason)
        results.append(
            TestResult(
                nodeid=nodeid,
                story_id=_property_story_id(case) or _name_story_id(case),
                verdict=verdict,
                reason=reason,
            )
        )
    return results


def _story_result(story_id: str, results: Sequence[TestResult]) -> tuple[str, str]:
    matching = [result for result in results if result.story_id == story_id]
    if not matching:
        return "untested", ""
    selected = max(matching, key=lambda result: _VERDICT_PRIORITY[result.verdict])
    reasons = list(dict.fromkeys(result.reason for result in matching if result.reason))
    combined = "; ".join(reasons)
    if any(result.verdict == "fail (classified)" for result in matching):
        return selected.verdict, combined
    return selected.verdict, _truncate_reason(combined)


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


def _testsuite_properties(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    properties: dict[str, str] = {}
    for suite in suites:
        for prop in suite.findall("./properties/property"):
            name = prop.get("name")
            value = prop.get("value")
            if name in _METADATA_PROPERTIES and value is not None:
                properties.setdefault(name, value)
    return properties


def _metadata(path: Path) -> ReportMetadata:
    properties = _testsuite_properties(path)
    missing = [name for name in _METADATA_PROPERTIES if name not in properties]
    fallback: dict[str, str] = {}
    if "python_version" in missing:
        fallback["python_version"] = platform.python_version()
    missing_extras = [name for name in missing if name.startswith("extras_")]
    if missing_extras:
        detected_extras = _extras_present()
        for name in missing_extras:
            fallback[name] = str(detected_extras[name.removeprefix("extras_")]).lower()
    if "real_ontology_enabled" in missing:
        fallback["real_ontology_enabled"] = str(
            os.environ.get("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY") == "1"
        ).lower()
    if "commit" in missing:
        fallback["commit"] = _commit()
    values = {
        name: properties[name] if name in properties else fallback[name]
        for name in _METADATA_PROPERTIES
    }
    if not properties:
        source = "report-interpreter fallback (JUnit properties absent)"
    elif missing:
        source = "JUnit testsuite properties; report-interpreter fallback for " + ", ".join(missing)
    else:
        source = "JUnit testsuite properties"
    return ReportMetadata(
        python_version=values["python_version"],
        extras={
            "folio": values["extras_folio"],
            "spacy": values["extras_spacy"],
            "embedding": values["extras_embedding"],
        },
        real_ontology_enabled=values["real_ontology_enabled"],
        commit=values["commit"],
        source=source,
    )


def _rerun_commands(
    junit: Path,
    stories: Path,
    output: Path,
    core_junit: Path,
) -> list[str]:
    core_command = [
        "uv",
        "run",
        "--isolated",
        "--extra",
        "dev",
        "pytest",
        "tests/uat",
        "-m",
        "uat",
        f"--junitxml={core_junit}",
    ]
    extras_command = [
        "FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1",
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
        "--core-junit",
        str(core_junit),
    ]
    return [shlex.join(core_command), shlex.join(extras_command), shlex.join(report_command)]


def build_markdown(
    *,
    junit: Path,
    stories_path: Path,
    output: Path,
    core_junit: Path,
) -> str:
    stories = _story_catalog(stories_path)
    results = _results(junit)
    core_results = _results(core_junit)
    metadata = _metadata(junit)

    lines = [
        "# User acceptance test report",
        "",
        f"- Commit: `{metadata.commit}`",
        f"- Python: `{metadata.python_version}`",
        "- Extras present: "
        + ", ".join(f"`{name}={value}`" for name, value in metadata.extras.items()),
        f"- Real ontology enabled: `{metadata.real_ontology_enabled}`",
        f"- Metadata source: {metadata.source}.",
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

    summaries = {
        "extras": {"pass": 0, "fail": 0, "skip": 0},
        "core": {"pass": 0, "fail": 0, "skip": 0},
    }
    for story_id, persona in stories:
        verdict, reason = _story_result(story_id, results)
        core_verdict, core_reason = _story_result(story_id, core_results)
        for lane, lane_verdict in (("extras", verdict), ("core", core_verdict)):
            summary_key = "fail" if lane_verdict.startswith("fail") else lane_verdict
            if summary_key in summaries[lane]:
                summaries[lane][summary_key] += 1
        if core_verdict != verdict and core_reason:
            reason = "; ".join(part for part in (reason, f"core: {core_reason}") if part)
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

    harness_failures = sum(result.verdict.startswith("fail") for result in unmapped)
    lines.extend(
        [
            "",
            "## Summary",
            "",
            ", ".join(
                [
                    "extras: "
                    + " ".join(
                        f"{verdict} {summaries['extras'][verdict]}"
                        for verdict in ("pass", "fail", "skip")
                    ),
                    "core: "
                    + " ".join(
                        f"{verdict} {summaries['core'][verdict]}"
                        for verdict in ("pass", "fail", "skip")
                    ),
                    f"harness failures: {harness_failures}",
                ]
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the persona UAT markdown report")
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--stories", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--core-junit", required=True, type=Path)
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
