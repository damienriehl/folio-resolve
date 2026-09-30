#!/usr/bin/env python3
"""Walk consumer workflows against a fresh installed wheel, never the source tree.

Run: python tests/uat/consumer_walk.py --out report.json [--offline]
     [--keep-venv /absolute/path/outside/checkout]

README snippets are independent, verbatim scripts (no injected imports or fixtures).
All python fences are usage examples in the current README. Documentation drift and
missing remediation are P2 friction; functional failures are P1 broken. Incomplete
checks are skipped with a reason. Exit 1 means at least one broken step.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEP_IDS = (
    "readme_quickstart", "scoring_only", "resolve", "annotate", "judge",
    "missing_extra", "typing", "import_cost", "build_metadata",
)


def result(step_id: str, verdict: str, evidence: str = "", finding: str | None = None) -> dict:
    return {
        "id": step_id, "verdict": verdict,
        "severity": {"broken": "P1", "friction": "P2"}.get(verdict),
        "finding": finding, "evidence": evidence,
    }


def aggregate(step_id: str, steps: list[dict]) -> dict:
    """Keep every subfinding; a skipped subcheck cannot become a complete pass."""
    if not steps:
        return result(step_id, "skipped", finding="No runnable checks found")
    order = {"works": 0, "skipped": 1, "friction": 2, "broken": 3}
    verdict = max(steps, key=lambda step: order[step["verdict"]])["verdict"]
    findings = [f'{s["id"]}: {s["finding"]}' for s in steps if s["finding"]]
    evidence = [f'{s["id"]}: {s["evidence"]}' for s in steps if s["evidence"]]
    return result(step_id, verdict, "\n".join(evidence), "; ".join(findings) or None)


def exit_code(steps: list[dict]) -> int:
    return int(any(s["verdict"] == "broken" for s in steps))


@dataclass(frozen=True)
class ReadmeBlock:
    line: int
    source: str


def extract_python_blocks(readme: str) -> list[ReadmeBlock]:
    """Handle indented and variable-length fences without parsing nested examples."""
    blocks = []
    opening = None
    lines: list[str] = []
    for number, line in enumerate(readme.splitlines(keepends=True), 1):
        if opening is None:
            match = re.match(r"^( *)(`{3,}|~{3,})([^\n]*)\n?$", line)
            if match:
                indent, fence, info = match.groups()
                opening = (len(indent), fence, info.strip(), number + 1)
                lines = []
        else:
            indent, fence, info, first_line = opening
            if re.fullmatch(r" *" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                if info == "python":
                    blocks.append(ReadmeBlock(first_line, "".join(lines)))
                opening = None
            else:
                # Strip only the Markdown fence indentation, not Python indentation.
                count = min(indent, len(line) - len(line.lstrip(" ")))
                lines.append(line[count:])
    return blocks


def command(args: list[str], *, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "MYPYPATH", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        env.pop(key, None)
    env.update(PYTHONNOUSERSITE="1", UV_PYTHON_DOWNLOADS="never")
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def tail(completed: subprocess.CompletedProcess) -> str:
    return "\n".join((completed.stderr or completed.stdout or "completed successfully").splitlines()[-6:])[-1800:]


def run_script(step_id: str, source: str, python: Path, cwd: Path, *, failure: str = "broken") -> dict:
    script = cwd / f"{step_id}.py"
    script.write_text(source, encoding="utf-8")
    completed = command([str(python), "-I", str(script)], cwd=cwd)
    verdict = "works" if completed.returncode == 0 else ("friction" if completed.returncode == 2 else failure)
    return result(step_id, verdict, tail(completed), None if verdict == "works" else f"{step_id} failed: {tail(completed)}")


def run_readme_blocks(blocks: list[ReadmeBlock], python: Path, cwd: Path) -> list[dict]:
    steps = []
    for index, block in enumerate(blocks, 1):
        step = run_script(f"readme_block_{index}", block.source, python, cwd, failure="friction")
        if step["verdict"] != "works":
            step["finding"] = f"README line {block.line} cannot run verbatim: {step['evidence']}"
        steps.append(step)
    return steps


def offline_unavailable(completed: subprocess.CompletedProcess, offline: bool) -> bool:
    message = (completed.stderr + completed.stdout).lower()
    return offline and any(token in message for token in (
        "not found in the cache", "not available in the cache", "network connectivity is disabled",
        "network is disabled", "not found in cache",
    ))


def missing_extras(python: Path, cwd: Path) -> dict:
    # There is no spaCy ruler class in this release: FOLIOEntityRuler is pure
    # Python; augment_labels is the actual optional spaCy index-build seam.
    probes = {
        "folio": "from folio_resolve import FolioPythonProvider\nFolioPythonProvider().all_labels()",
        "embedding": "from folio_resolve.embedding import LocalEmbeddingProvider\nLocalEmbeddingProvider()",
        "spacy": "from folio_resolve import Concept, InMemoryOntology, augment_labels\naugment_labels(InMemoryOntology([Concept(iri='R-agreements', label='Agreements')]).all_labels())",
    }
    steps = []
    for extra, body in probes.items():
        source = (
            "try:\n" + textwrap.indent(body, "    ") + "\n"
            "except ImportError as exc:\n"
            "    message = str(exc)\n"
            "    print(type(exc).__name__ + ': ' + message)\n"
            f"    actionable = '[{extra}]' in message or ('extra' in message.lower() and {extra!r} in message.lower())\n"
            "    raise SystemExit(0 if actionable else 2)\n"
            "else:\n"
            "    raise AssertionError('extras-only entry point unexpectedly succeeded in core venv')\n"
        )
        step = run_script(f"missing_{extra}", source, python, cwd)
        if step["verdict"] == "friction":
            step["finding"] = f"Missing {extra} extra error has no installation remediation"
        steps.append(step)
    steps.append(result("spacy_api", "works", "FOLIOEntityRuler is pure Python; checked augment_labels, the spaCy index-build path"))
    return aggregate("missing_extra", steps)


def typing_step(python: Path, cwd: Path, offline: bool) -> dict:
    marker = run_script("py_typed", "from importlib.resources import files\nassert files('folio_resolve').joinpath('py.typed').is_file(), 'installed wheel is missing py.typed'\n", python, cwd, failure="friction")
    installed = command(["uv", "pip", "install", *(["--offline"] if offline else []), "--python", str(python), "mypy"], cwd=cwd)
    if installed.returncode:
        check = result("mypy", "skipped" if offline_unavailable(installed, offline) else "broken", tail(installed), "mypy installation unavailable" + (" offline" if offline else ""))
    else:
        snippet = cwd / "typed_consumer.py"
        snippet.write_text(
            "from folio_resolve import Concept, InMemoryOntology, MatchPipeline, compute_relevance_score, content_words, generate_search_terms\n"
            "ontology = InMemoryOntology([Concept(iri='R-arb', label='Arbitration Rules')])\n"
            "terms: list[str] = generate_search_terms('litigation')\n"
            "score: float = compute_relevance_score(content_words('arbitration rules'), 'arbitration rules', 'Arbitration Rules')\n"
            "for candidate in MatchPipeline(ontology=ontology).match('rules of arbitration'):\n"
            "    iri: str = candidate.iri\n", encoding="utf-8",
        )
        completed = command([str(python), "-I", "-m", "mypy", "--strict", "--no-incremental", str(snippet)], cwd=cwd)
        check = result("mypy", "friction" if completed.returncode else "works", tail(completed), "Strict consumer typing failed" if completed.returncode else None)
    return aggregate("typing", [marker, check])


def import_cost(python: Path, cwd: Path) -> dict:
    completed = command([str(python), "-I", "-X", "importtime", "-c", "import folio_resolve"], cwd=cwd)
    if completed.returncode:
        return result("import_cost", "broken", tail(completed), "Core import failed")
    imports = []
    total = None
    for line in completed.stderr.splitlines():
        match = re.match(r"import time:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\S+)", line)
        if match:
            _, cumulative, module = match.groups()
            imports.append(module)
            if module == "folio_resolve":
                total = int(cumulative)
    heavy = sorted({m.split('.')[0] for m in imports} & {"faiss", "torch", "sentence_transformers", "spacy", "folio"})
    evidence = f"folio_resolve cumulative import time: {total} us; heavy imports: {heavy}"
    if heavy or total is None:
        return result("import_cost", "broken", evidence, "Heavy optional import or missing import-time measurement")
    return result("import_cost", "works", evidence)


def walk(work: Path, venv: Path, offline: bool) -> tuple[list[dict], str]:
    flags = ["--offline"] if offline else []
    dist = work / "dist"
    built = command(["uv", "build", *flags, "--out-dir", str(dist), str(ROOT)], cwd=work)
    if built.returncode:
        verdict = "skipped" if offline_unavailable(built, offline) else "broken"
        return [result(s, verdict if s == "build_metadata" else "skipped", tail(built), "Wheel build unavailable; installed-wheel checks could not run") for s in STEP_IDS], sys.version.split()[0]
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [result(s, "broken" if s == "build_metadata" else "skipped", finding="uv build must produce one wheel and one sdist") for s in STEP_IDS], sys.version.split()[0]
    created = command(["uv", "venv", *flags, "--python", sys.executable, str(venv)], cwd=work)
    python = venv / "bin" / "python"
    installed = created if created.returncode else command(["uv", "pip", "install", *flags, "--python", str(python), str(wheels[0])], cwd=work)
    if installed.returncode:
        verdict = "skipped" if offline_unavailable(installed, offline) else "broken"
        return [result(s, verdict if s == "build_metadata" else "skipped", tail(installed), "Fresh core wheel installation unavailable; checks could not run") for s in STEP_IDS], sys.version.split()[0]
    version = command([str(python), "-I", "-c", "import platform; print(platform.python_version())"], cwd=work).stdout.strip()
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    metadata = run_script("build_metadata", textwrap.dedent(f"""\
        import sys
        import zipfile
        from email.parser import Parser
        from pathlib import Path
        import folio_resolve
        from importlib.metadata import version
        with zipfile.ZipFile({str(wheels[0])!r}) as wheel:
            names = [n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
            assert len(names) == 1, names
            metadata = Parser().parsestr(wheel.read(names[0]).decode())
        assert {{'folio', 'embedding', 'spacy'}} <= set(metadata.get_all('Provides-Extra') or []), metadata
        assert folio_resolve.__version__ == version('folio-resolve') == metadata['Version'] == {expected!r}
        package = Path(folio_resolve.__file__).resolve()
        assert package.is_relative_to(Path(sys.prefix).resolve()), package
        assert not package.is_relative_to(Path({str(ROOT)!r})), package
        print('sdist + wheel built; extras and version verified; imported ' + str(package))
        """), python, work)
    blocks = extract_python_blocks((ROOT / "README.md").read_text())
    readme_steps = run_readme_blocks(blocks, python, work)
    # Assert the prose's documented scores, plus the quick-start output comment.
    readme_steps.append(run_script("readme_documented_output", textwrap.dedent('''\
        from folio_resolve import compute_relevance_score, content_words
        for query, expected in [('arbitration rules', 99.0), ('rules of arbitration', 88.0)]:
            actual = compute_relevance_score(content_words(query), query, 'Arbitration Rules')
            assert actual == expected, (query, actual, expected)
        print('README scores: 99.0 and 88.0')
        '''), python, work, failure="friction"))
    for i, block in enumerate(blocks, 1):
        if 'pipe.match("rules of arbitration")' in block.source:
            readme_steps.append(run_script(f"readme_output_{i}", f"import runpy\nns = runpy.run_path({str(work / f'readme_block_{i}.py')!r})\nassert ns['pipe'].match('rules of arbitration')[0].label == 'Arbitration Rules', 'README quick-start output drifted'\n", python, work, failure="friction"))
    steps = [aggregate("readme_quickstart", readme_steps)]
    for name in ("scoring_only", "resolve", "annotate", "judge"):
        steps.append(run_script(name, (ROOT / "tests" / "uat" / "consumer_steps" / f"{name}.py").read_text(), python, work))
    steps.extend([missing_extras(python, work), import_cost(python, work), metadata])
    # Install mypy only after all core-only checks have finished.
    steps.append(typing_step(python, work, offline))
    return sorted(steps, key=lambda step: STEP_IDS.index(step["id"])), version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep-venv", type=Path, help="New, nonexistent directory outside checkout; retained after run")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if args.keep_venv:
        args.keep_venv = args.keep_venv.expanduser().resolve()
        if args.keep_venv.is_relative_to(ROOT) or args.keep_venv.exists():
            parser.error("--keep-venv must be a new directory outside the checkout")
    commit = command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip() or "unknown"
    # /tmp is explicit: TMPDIR may point inside the checkout.
    with tempfile.TemporaryDirectory(prefix="folio-consumer-walk-", dir="/tmp") as directory:
        work = Path(directory)
        steps, python_version = walk(work, args.keep_venv or work / "venv", args.offline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"commit": commit, "python": python_version, "steps": steps}, indent=2) + "\n", encoding="utf-8")
    for step in steps:
        print(f"{step['id']}: {step['verdict']}")
    return exit_code(steps)


if __name__ == "__main__":
    raise SystemExit(main())
