"""Determinism self-test and the ontology pin (U3, R7, KTD7).

KTD7 makes two demands of every real scoring invocation, and both are enforced here before a
single item is scored:

1. **The ontology is pinned.** :func:`assert_ontology_pin` resolves the concrete
   ``~/.folio/cache/github/<blake2b>.owl`` file folio-python will load, hashes it, and compares
   against the hash recorded in the gold manifest. An absent file aborts (never a network fetch);
   a changed hash aborts unless the caller explicitly re-baselines.
2. **The run is hash-order independent.** :func:`run_determinism_selftest` executes a scoring
   pass twice — the second time in a *subprocess* under a different ``PYTHONHASHSEED`` — and
   compares the SHA-256 of the produced payload. Any set-iteration or dict-ordering dependence
   that leaked into scoring, ranking, or serialization shows up as a mismatch and aborts the run.

The default self-test target is :func:`synthetic_scoring_payload`: a fixed synthetic ontology and
a fixed gold sample driven through the *real* answer-rule, scoring, and report-serialization code,
so the check is cheap enough to run at the top of every invocation while still exercising the code
that would carry the nondeterminism. A caller that wants the stronger (and far slower) check can
point ``--determinism-target`` at any ``module:callable`` returning a ``str`` — including one that
runs the real pass.

``PYTHONHASHSEED=0`` is set for the parent process by :func:`ensure_hash_seed`, which re-execs
once if the environment did not already pin it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .resolve_labels import OntologyCacheError, folio_cache_file, ontology_cache_sha256

DEFAULT_SELFTEST_TARGET = "folio_eval.selftest:synthetic_scoring_payload"


class DeterminismError(RuntimeError):
    """Raised when two passes under different hash seeds disagree (KTD7: abort the run)."""


class OntologyPinError(RuntimeError):
    """Raised when the pinned ontology cache is absent or its hash moved."""


# --------------------------------------------------------------------------------------
# PYTHONHASHSEED
# --------------------------------------------------------------------------------------

_REEXEC_FLAG = "FOLIO_EVAL_HASH_SEED_SET"


def ensure_hash_seed(seed: str = "0") -> None:
    """Re-exec once under ``PYTHONHASHSEED=seed`` when the environment did not pin it (KTD7)."""
    if os.environ.get("PYTHONHASHSEED") == seed or os.environ.get(_REEXEC_FLAG) == "1":
        return
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env[_REEXEC_FLAG] = "1"
    # ``sys.orig_argv`` preserves ``-m folio_eval.score``; rebuilding from ``sys.argv`` would
    # re-exec the module's file path directly and its relative imports would fail.
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.execve(argv[0], argv, env)


# --------------------------------------------------------------------------------------
# Ontology pin
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OntologyPin:
    """The concrete ontology bytes a run scored against."""

    path: Path
    sha256: str


def assert_ontology_pin(expected_sha256: str, *, cache_path: Path | None = None) -> OntologyPin:
    """Resolve and hash the pinned FOLIO cache file; abort on absence or on a hash change."""
    path = cache_path or folio_cache_file()
    try:
        observed = ontology_cache_sha256(path)
    except OntologyCacheError as error:
        raise OntologyPinError(str(error)) from error
    if expected_sha256 and observed != expected_sha256:
        raise OntologyPinError(
            "ontology pin mismatch: "
            f"expected={expected_sha256} observed={observed} path={path} — "
            "a hash change aborts the run unless it is an explicit re-baseline (KTD7)"
        )
    return OntologyPin(path=path, sha256=observed)


# --------------------------------------------------------------------------------------
# Determinism self-test
# --------------------------------------------------------------------------------------


def resolve_target(target: str) -> Callable[[], str]:
    """Resolve a ``module:callable`` string into the zero-argument payload factory it names."""
    if ":" not in target:
        raise ValueError(f"determinism target must be 'module:callable': {target!r}")
    module_name, attribute = target.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise ValueError(f"determinism target is not callable: {target!r}")
    return factory  # type: ignore[no-any-return]


def payload_sha256(target: str) -> str:
    return hashlib.sha256(resolve_target(target)().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    """The two passes' hashes and the seeds that produced them."""

    target: str
    first_sha256: str
    second_sha256: str
    first_seed: str
    second_seed: str

    @property
    def deterministic(self) -> bool:
        return self.first_sha256 == self.second_sha256

    def to_json(self) -> dict[str, object]:
        return {
            "target": self.target,
            "deterministic": self.deterministic,
            "first_sha256": self.first_sha256,
            "second_sha256": self.second_sha256,
            "first_seed": self.first_seed,
            "second_seed": self.second_seed,
        }


def _child_seed() -> str:
    """A hash seed guaranteed to differ from the parent's."""
    return "2" if os.environ.get("PYTHONHASHSEED") == "1" else "1"


def run_determinism_selftest(
    target: str = DEFAULT_SELFTEST_TARGET,
    *,
    timeout: float = 300.0,
    raise_on_mismatch: bool = True,
) -> SelfTestResult:
    """Run the payload in-process, then again in a subprocess under a different hash seed."""
    first = payload_sha256(target)
    seed = _child_seed()
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env[_REEXEC_FLAG] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(Path(__file__).resolve().parent.parent), env.get("PYTHONPATH", "")) if part
    )
    completed = subprocess.run(
        [sys.executable, "-m", "folio_eval.selftest", "--emit", target],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise DeterminismError(
            f"determinism self-test subprocess failed (rc={completed.returncode}): "
            f"{completed.stderr.strip()[-2000:]}"
        )
    second = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    result = SelfTestResult(
        target=target,
        first_sha256=first,
        second_sha256=second,
        first_seed=os.environ.get("PYTHONHASHSEED", "<unset>"),
        second_seed=seed,
    )
    if raise_on_mismatch and not result.deterministic:
        raise DeterminismError(
            "determinism self-test FAILED: two passes under different PYTHONHASHSEED values "
            f"produced different output ({first} != {second}) for target {target!r} — "
            "the run is aborted rather than reported (KTD7)"
        )
    return result


# --------------------------------------------------------------------------------------
# Payload targets
# --------------------------------------------------------------------------------------

_SAMPLE_STRINGS = tuple(f"folio-eval-determinism-probe-{index:02d}" for index in range(32))


def stable_payload() -> str:
    """A hash-order-independent payload: the reference the self-test must pass on."""
    return json.dumps(sorted(set(_SAMPLE_STRINGS)), ensure_ascii=False)


def unstable_payload() -> str:
    """Deliberately hash-order *dependent* — the negative control the self-test must catch."""
    return json.dumps(list(set(_SAMPLE_STRINGS)), ensure_ascii=False)


def synthetic_scoring_payload() -> str:
    """A full scoring pass over a fixed synthetic ontology, through the real code paths.

    Imported lazily so that resolving this module never drags the pipeline in for callers that
    only want the ontology pin.
    """
    from folio_resolve import Concept, InMemoryOntology

    from .answer_rule import AnswerRuleConfig
    from .score import Hierarchy, PipelineAdapter, build_pipeline, score_items
    from .splits import GoldItemRecord

    concepts = [
        Concept(iri="R-root", label="Legal Practice"),
        Concept(iri="R-arb", label="Arbitration Rules", parent_iris=("R-root",)),
        Concept(iri="R-arb-int", label="International Arbitration Rules", parent_iris=("R-arb",)),
        Concept(iri="R-defenses", label="Litigation Defenses", parent_iris=("R-root",)),
        Concept(
            iri="R-burdens",
            label="Litigation Burdens of Proof",
            definition="Allocation of the burden of proof, including presumptions.",
            parent_iris=("R-root",),
        ),
        Concept(iri="R-findings", label="Proposed Findings of Fact", parent_iris=("R-root",)),
        Concept(iri="R-conclusions", label="Proposed Conclusions of Law", parent_iris=("R-root",)),
        Concept(iri="R-auction", label="Auction", parent_iris=("R-root",)),
    ]
    ontology = InMemoryOntology(concepts)
    pipeline = build_pipeline(ontology, with_entity_ruler=True)
    items = [
        GoldItemRecord(
            item_id="selftest-0001",
            firm="synthetic",
            stratum="synthetic",
            stratum_id="selftest",
            ancestor_path=("Dispute Resolution",),
            leaf="Arbitration Rules",
            input_text="Dispute Resolution > Arbitration Rules",
            gold_iris=frozenset({"R-arb"}),
            flags=frozenset(),
            blank=False,
        ),
        GoldItemRecord(
            item_id="selftest-0002",
            firm="synthetic",
            stratum="synthetic",
            stratum_id="selftest",
            ancestor_path=("Litigation",),
            leaf="Proposed Findings of Fact and Conclusions of Law",
            input_text="Litigation > Proposed Findings of Fact and Conclusions of Law",
            gold_iris=frozenset({"R-findings", "R-conclusions"}),
            flags=frozenset(),
            blank=False,
        ),
    ]
    run = score_items(
        items,
        PipelineAdapter(pipeline),
        config=AnswerRuleConfig(),
        hierarchy=Hierarchy.from_concepts(concepts),
        slice_name="selftest",
    )
    return json.dumps(
        {
            "overall": run.overall.to_json(),
            "items": [score.to_json() for score in run.item_scores],
            "near_miss": {key: run.near_miss[key] for key in sorted(run.near_miss)},
            "recall_at_k": {str(k): round(value, 6) for k, value in sorted(run.recall_at_k.items())},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


# --------------------------------------------------------------------------------------
# CLI (the subprocess entry point)
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.selftest",
        description="Determinism self-test and ontology pin (KTD7).",
    )
    parser.add_argument(
        "--emit",
        metavar="MODULE:CALLABLE",
        help="print the SHA-256 of the named payload and exit (the subprocess entry point)",
    )
    parser.add_argument("--target", default=DEFAULT_SELFTEST_TARGET)
    parser.add_argument(
        "--ontology-sha256",
        default="",
        help="expected FOLIO cache hash; when given, the pin is asserted too",
    )
    args = parser.parse_args(argv)

    if args.emit:
        print(payload_sha256(args.emit))
        return 0

    if args.ontology_sha256:
        pin = assert_ontology_pin(args.ontology_sha256)
        print(f"ontology pin OK: {pin.sha256}  ({pin.path})")
    result = run_determinism_selftest(args.target, raise_on_mismatch=False)
    print(json.dumps(result.to_json(), indent=2, sort_keys=True))
    return 0 if result.deterministic else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
