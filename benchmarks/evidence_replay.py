"""Offline validation of historical evidence with the current library.

Use ``offline(module).validate_collection(...)`` or ``offline(module).run_score(...)``
for committed evidence. Collection and CLI entry points remain the original strict
functions. No receipt, hashed source, imported module, or source identity is mutated.
"""

from __future__ import annotations

import json
from types import FunctionType, SimpleNamespace

# Only these offline entry points receive isolated dependency bindings. In particular,
# run_collect, run_ablation, verify_pins, verify_control and main stay untouched.
_OFFLINE_FUNCTIONS = (
    "validate_collection",
    "validate_judgments",
    "load_frozen_inputs",
    "transfer_judgments",
    "run_score",
)
_REPLAY_ERRORS = (
    "control candidates differ",
    "control mutations differ",
    "ranked snapshot",
    "retrieval score/path differs",
    "saved retrieval replay differs",
    "Semantic replay query/window differs",
    "trace scorer replay differs",
    "trace coverage/order differs",
)


def _bind(function, namespace):
    """Reuse a frozen function's code with private globals, never patch its module."""
    bound = FunctionType(
        function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__
    )
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def _drift(recorded, current, detail):
    return ValueError(
        "library source drift changed replayed results "
        f"(recorded={recorded}, current={current}): {detail}"
    )


def _replay(module):
    current = module.source_identity()
    recorded = None
    # Authenticate each immutable input and prove the original controls reproduce
    # BEFORE supplying the historical identity to any existing validator.
    for variant, digest in module.INPUT_SHA256.items():
        path = module.ROOT / f"docs/benchmarks/embedding-gate-ablation-{variant}.json"
        if module.baseline.file_digest(path) != digest:
            raise ValueError(f"Frozen artifact SHA-256 differs: {variant}")
        saved = json.loads(path.read_text())
        source = saved["provenance"]["library_source_sha256"]
        if recorded is None:
            recorded = source
        elif source != recorded:
            raise ValueError("Frozen library source identities differ")
        try:
            module.replay_controls(saved)
        except ValueError as error:
            raise _drift(source, current, error) from error
    # The original implementation checks every remaining identity and recomputes
    # all arms, metrics, guards, configuration digests and conclusions.
    result = _bind(module.run_replay, {**vars(module), "source_identity": lambda: recorded})()
    frozen = json.loads(
        (module.ROOT / "docs/benchmarks/embedding-semantic-gate-replay.json").read_text()
    )
    if result != frozen:
        raise _drift(recorded, current, "Frozen semantic replay differs")
    return result


def _replay_module(module):
    while hasattr(module, "precision"):
        module = module.precision
    return getattr(module, "replay", module)


def _checked(function, module):
    def validate(collection, *args, **kwargs):
        try:
            return function(collection, *args, **kwargs)
        except ValueError as error:
            if not str(error).startswith("library source drift") and any(
                message in str(error) for message in _REPLAY_ERRORS
            ):
                recorded = collection["provenance"]["library_source_sha256"]
                current = _replay_module(module).source_identity()
                raise _drift(recorded, current, error) from error
            raise

    return validate


def offline(module):
    """Return an isolated offline view; collection functions retain strict globals.

    The caller supplies the existing benchmark module, so its source-file identity
    checks and test instrumentation still apply. Build a fresh view after changing
    instrumentation; views do not modify or cache anything in the source module.
    """
    namespace = dict(vars(module))
    for dependency in ("replay", "precision", "relations"):
        if dependency in namespace:
            namespace[dependency] = offline(namespace[dependency])
    for name in _OFFLINE_FUNCTIONS:
        function = namespace.get(name)
        if isinstance(function, FunctionType) and function.__globals__ is vars(module):
            namespace[name] = _bind(function, namespace)
    if "validate_collection" in namespace:
        namespace["validate_collection"] = _checked(namespace["validate_collection"], module)
    if "run_replay" in namespace:
        namespace["run_replay"] = lambda: _replay(module)
    return SimpleNamespace(**namespace)
