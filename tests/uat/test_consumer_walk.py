"""Fast harness contract tests: fake processes, no wheel builds or virtualenvs."""

import subprocess

import pytest

from . import consumer_walk as walk

pytestmark = pytest.mark.uat


@pytest.mark.parametrize(
    ("verdicts", "expected", "code"),
    [
        (["works", "works"], "works", 0),
        (["works", "friction"], "friction", 0),
        (["skipped", "works"], "skipped", 0),
        (["broken", "friction", "works"], "broken", 1),
    ],
)
def test_aggregate_and_exit_code(verdicts, expected, code):
    steps = [walk.result(str(i), verdict, finding=verdict) for i, verdict in enumerate(verdicts)]
    combined = walk.aggregate("workflow", steps)
    assert combined["verdict"] == expected
    assert walk.exit_code(steps) == code
    assert walk.exit_code([combined]) == code
    if "broken" in verdicts:
        assert "broken" in combined["finding"]


def test_readme_extractor_preserves_drift_and_markdown_indentation():
    readme = (
        "# Usage\n```python\nprint('ok')\n```\n"
        "```bash\necho ignored\n```\n"
        "  ```python\n  missing_provider.resolve('law')\n  ```\n"
        "````text\n```python\nnot a usage block\n```\n````\n"
    )
    blocks = walk.extract_python_blocks(readme)
    assert [(b.line, b.source) for b in blocks] == [
        (3, "print('ok')\n"), (9, "missing_provider.resolve('law')\n"),
    ]


@pytest.mark.parametrize("returncode, expected", [(0, "works"), (1, "broken"), (2, "friction")])
def test_fake_process_verdict(monkeypatch, tmp_path, returncode, expected):
    def fake_command(args, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert "assert False" in (tmp_path / "probe.py").read_text()
        return subprocess.CompletedProcess(args, returncode, "", "AssertionError: consumer failed")

    monkeypatch.setattr(walk, "command", fake_command)
    step = walk.run_script("probe", "assert False\n", tmp_path / "python", tmp_path)
    assert step["verdict"] == expected
    assert walk.exit_code([step]) == (1 if expected == "broken" else 0)
    if returncode:
        assert "consumer failed" in step["evidence"]


def test_drifted_readme_is_friction_and_later_blocks_run(monkeypatch, tmp_path):
    blocks = walk.extract_python_blocks("```python\nmissing_name()\n```\n```python\nprint(1)\n```\n")
    calls = []

    def fake_command(args, **kwargs):
        calls.append(args)
        code = int(len(calls) == 1)
        return subprocess.CompletedProcess(args, code, "1" if not code else "", "NameError: missing_name" if code else "")

    monkeypatch.setattr(walk, "command", fake_command)
    steps = walk.run_readme_blocks(blocks, tmp_path / "python", tmp_path)
    assert len(calls) == 2
    assert [s["verdict"] for s in steps] == ["friction", "works"]
    assert "line 2" in steps[0]["finding"]
    assert walk.exit_code(steps) == 0
