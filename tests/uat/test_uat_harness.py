from __future__ import annotations

import platform
from pathlib import Path

import pytest

from folio_resolve import OntologyProvider

from . import conftest as uat_conftest
from .build_report import main
from .conftest import blocked_optional_imports


def test_uat_marker_is_applied_to_tests_in_this_package(request: pytest.FixtureRequest) -> None:
    assert request.node.get_closest_marker("uat") is not None


def test_real_ontology_requires_the_extra_and_explicit_opt_in(
    real_ontology: OntologyProvider,
) -> None:
    assert isinstance(real_ontology, OntologyProvider)


def test_real_ontology_propagates_installed_package_import_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOLIO_RESOLVE_UAT_REAL_ONTOLOGY", "1")
    monkeypatch.setattr("uat.conftest.importlib.util.find_spec", lambda _module: object())

    def fail_import(_module: str) -> None:
        raise ImportError("transitive folio dependency failed")

    monkeypatch.setattr("uat.conftest.importlib.import_module", fail_import)

    with pytest.raises(ImportError, match="transitive folio dependency failed"):
        uat_conftest.load_real_ontology()


def test_blocked_optional_imports_keeps_the_public_core_importable(tmp_path: Path) -> None:
    completed = blocked_optional_imports(
        """
try:
    import spacy
except ImportError:
    print("BLOCKED:spacy")
else:
    raise AssertionError("spacy import unexpectedly succeeded")

print(f"IMPORTED:{folio_resolve.__version__}")
""",
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert "BLOCKED:spacy" in completed.stdout
    assert "IMPORTED:" in completed.stdout


def _write_story_catalog(path: Path) -> None:
    path.write_text(
        """# Stories

### US-PI-01 — Passing in extras, failing in core
### US-SI-01 — Strict classified failure
### US-RM-01 — Plain skip
""",
        encoding="utf-8",
    )


def test_build_report_uses_strict_classification_metadata_and_lane_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stories = tmp_path / "stories.md"
    _write_story_catalog(stories)
    target = "docs/plans/" + "classified-repair-" * 8 + ".md"
    extras_junit = tmp_path / "extras.xml"
    extras_junit.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="4" failures="1" skipped="2">
  <properties>
    <property name="python_version" value="3.13.7" />
    <property name="extras_folio" value="true" />
    <property name="extras_spacy" value="true" />
    <property name="extras_embedding" value="false" />
    <property name="real_ontology_enabled" value="true" />
    <property name="commit" value="abc123" />
  </properties>
  <testcase classname="tests.uat.test_sample" name="test_us_pi_01_passes" />
  <testcase classname="tests.uat.test_sample" name="test_us_si_01_classified">
    <skipped type="pytest.xfail" message="library defect: {target}" />
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_us_rm_01_plain_skip">
    <skipped message="library defect: docs/plans/not-an-xfail.md" />
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_without_a_story">
    <failure message="extras harness broke">traceback details</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    core_junit = tmp_path / "core.xml"
    core_junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="4" failures="2" skipped="1">
  <testcase classname="tests.uat.test_sample" name="test_us_pi_01_fails">
    <failure message="core lane broke">traceback details</failure>
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_us_si_01_passes" />
  <testcase classname="tests.uat.test_sample" name="test_us_rm_01_skips">
    <skipped message="optional extra unavailable" />
  </testcase>
  <testcase classname="tests.uat.test_sample" name="test_core_without_a_story">
    <error message="core harness broke">traceback details</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    report = tmp_path / "report.md"

    assert (
        main(
            [
                "--junit",
                str(extras_junit),
                "--stories",
                str(stories),
                "--out",
                str(report),
                "--core-junit",
                str(core_junit),
            ]
        )
        == 0
    )

    markdown = report.read_text(encoding="utf-8")
    assert "- Commit: `abc123`" in markdown
    assert "- Python: `3.13.7`" in markdown
    assert "`folio=true`, `spacy=true`, `embedding=false`" in markdown
    assert "- Real ontology enabled: `true`" in markdown
    assert "- Metadata source: JUnit testsuite properties." in markdown
    assert f"library defect: [{target}]({target})" in markdown
    assert "| US-RM-01 | RM | skip | skip | library defect:" in markdown
    assert "| US-PI-01 | PI | pass | fail | core: core lane broke |" in markdown
    assert (
        "extras: pass 1 fail 1 skip 1, core: pass 1 fail 1 skip 1, harness failures: 2" in markdown
    )

    core_command = f"uv run --isolated --extra dev pytest tests/uat -m uat --junitxml={core_junit}"
    extras_command = (
        "FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1 .venv/bin/python -m pytest tests/uat -m uat "
        f"--junitxml={extras_junit}"
    )
    report_command = (
        f".venv/bin/python tests/uat/build_report.py --junit {extras_junit} "
        f"--stories {stories} --out {report} --core-junit {core_junit}"
    )
    assert (
        markdown.index(core_command)
        < markdown.index(extras_command)
        < markdown.index(report_command)
    )
    assert markdown in capsys.readouterr().out


def test_build_report_labels_interpreter_metadata_fallback(
    tmp_path: Path,
) -> None:
    stories = tmp_path / "stories.md"
    _write_story_catalog(stories)
    extras_junit = tmp_path / "extras.xml"
    extras_junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1">
  <testcase classname="tests.uat.test_sample" name="test_us_pi_01_passes" />
</testsuite>
""",
        encoding="utf-8",
    )
    core_junit = tmp_path / "core.xml"
    core_junit.write_text(extras_junit.read_text(encoding="utf-8"), encoding="utf-8")
    report = tmp_path / "report.md"

    assert (
        main(
            [
                "--junit",
                str(extras_junit),
                "--stories",
                str(stories),
                "--out",
                str(report),
                "--core-junit",
                str(core_junit),
            ]
        )
        == 0
    )

    markdown = report.read_text(encoding="utf-8")
    assert f"- Python: `{platform.python_version()}`" in markdown
    assert "- Metadata source: report-interpreter fallback (JUnit properties absent)." in markdown
