from __future__ import annotations

from pathlib import Path

import pytest

from folio_resolve import OntologyProvider

from .build_report import main
from .conftest import blocked_optional_imports


def test_uat_marker_is_applied_to_tests_in_this_package(request: pytest.FixtureRequest) -> None:
    assert request.node.get_closest_marker("uat") is not None


def test_real_ontology_requires_the_extra_and_explicit_opt_in(
    real_ontology: OntologyProvider,
) -> None:
    assert isinstance(real_ontology, OntologyProvider)


def test_blocked_optional_imports_keeps_the_public_core_importable() -> None:
    completed = blocked_optional_imports(
        """
try:
    import spacy
except ImportError:
    print("BLOCKED:spacy")
else:
    raise AssertionError("spacy import unexpectedly succeeded")

print(f"IMPORTED:{folio_resolve.__version__}")
"""
    )

    assert completed.returncode == 0, completed.stderr
    assert "BLOCKED:spacy" in completed.stdout
    assert "IMPORTED:" in completed.stdout


def test_build_report_maps_pass_fail_skip_and_unmapped_tests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stories = tmp_path / "stories.md"
    stories.write_text(
        """# Stories

### US-PI-01 — Passing story
### US-SI-01 — Failing story
### US-RM-01 — Skipped story
""",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="4" failures="1" skipped="1">
  <testcase classname="tests.uat.test_sample" name="test_us_pi_01_passes" />
  <testcase classname="tests.uat.test_sample" name="test_failure_by_property">
    <properties><property name="story_id" value="US-SI-01" /></properties>
    <failure message="expected 99.0 but got 98.0">traceback details</failure>
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_us_rm_01_skips">
    <skipped message="optional extra unavailable" />
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_without_a_story" />
</testsuite>
""",
        encoding="utf-8",
    )
    report = tmp_path / "report.md"

    assert main(["--junit", str(junit), "--stories", str(stories), "--out", str(report)]) == 0

    markdown = report.read_text(encoding="utf-8")
    assert "| US-PI-01 | PI | pass |" in markdown
    assert "| US-SI-01 | SI | fail |" in markdown
    assert "| US-RM-01 | RM | skip |" in markdown
    assert "expected 99.0 but got 98.0" in markdown
    assert "## Unmapped tests" in markdown
    assert "tests.uat.test_sample::test_without_a_story" in markdown
    assert "pass: 1, fail: 1, skip: 1, untested: 0" in markdown
    assert markdown in capsys.readouterr().out
