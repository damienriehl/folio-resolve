"""Offline validation of historical evidence with the current library.

Use ``offline(module).validate_collection(...)`` or ``offline(module).run_score(...)``
for committed evidence. Collection and CLI entry points remain the original strict
functions. No receipt, hashed source, imported module, or source identity is mutated.
"""

from __future__ import annotations

import hashlib
import json
from functools import wraps
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

# SHA-256 of the committed historical bytes, independently checked against origin/main.
FROZEN_SHA256 = {
    "docs/benchmarks/embedding-gate-ablation-disabled.json": "92a9bedd1564c7b41743e99cb4e2f41a852aabe39ba2977d38d49421a2374e9f",
    "docs/benchmarks/embedding-gate-ablation-hashing.json": "123aea07faaf993ce856a72e8e0252544c568177ac5866d872b74799e3deaa1b",
    "docs/benchmarks/embedding-gate-ablation-local.json": "8d605aba45356a40a131298b721eae9b03c5c60ad39a7f8dcf2dfbced5a2cd92",
    "docs/benchmarks/embedding-baseline-disabled.json": "b21087d8fb14973928c1598db7c667739d387ab6cbd060ffec80159375e5f789",
    "docs/benchmarks/embedding-baseline-hashing.json": "21a241bb67b136dcbc9fb652680df99b94235b06116274d08ad3b1f027ffe97f",
    "docs/benchmarks/embedding-baseline-local.json": "da5834e0cae37872119aa27e5e8b0326a47c5730644c11677013116b784f145a",
    "docs/benchmarks/embedding-semantic-gate-replay.json": "97f061fe72b4b66b97800646c0e10f6bb2daac0c7f720edbd5ee97a556304201",
    "docs/benchmarks/embedding-precision-collection.json": "a3f257034657a26657141f97c9a6e883a98e692556bf608d14309f7e0d726bb5",
    "docs/benchmarks/embedding-precision-judgments.json": "d9439752fbff65c74605d878f37f15afeebc9fe98653c1da3d49bbf215ea8e51",
    "docs/benchmarks/embedding-lexical-boundaries-collection.json": "77467822b82315813455bebfc28832a0202dd6e66ca7256c105b1e5169f06b6f",
    "benchmarks/fixtures/embedding_recall.json": "3add005e2d46ce5317973355e4ddb9fd2435382e3100fb974d8c501ad8938ade",
    "benchmarks/fixtures/embedding_precision.json": "541d00289038d026161aafe9314baeb2b7d0ee4329e1f36ea79a5f285498e51c",
    "benchmarks/fixtures/embedding_model_files.json": "b394e82a9ca50b4d475146ea023c82481f16052ccd2cf9617b26ef9f67924aae",
}


def _authenticated(root, relative):
    data = (root / relative).read_bytes()
    if hashlib.sha256(data).hexdigest() != FROZEN_SHA256[relative]:
        raise ValueError(f"Frozen artifact SHA-256 differs: {relative}")
    return json.loads(data)


def _authenticate_inputs(module):
    root = _replay_module(module).ROOT
    return {name: _authenticated(root, name) for name in FROZEN_SHA256}


def _require_frozen(value, artifacts):
    if not any(value == saved for saved in artifacts.values()):
        raise ValueError("Input provenance/metadata differs from authenticated frozen artifact")


def _entry(function, module):
    @wraps(function)
    def authenticated(*args, **kwargs):
        artifacts = _authenticate_inputs(module)
        for value in (*args, *kwargs.values()):
            if isinstance(value, dict):
                _require_frozen(value, artifacts)
        return function(*args, **kwargs)

    return authenticated


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
    artifacts = _authenticate_inputs(module)
    recorded = None
    # Authenticate each immutable input and prove the original controls reproduce
    # BEFORE supplying the historical identity to any existing validator.
    for variant, digest in module.INPUT_SHA256.items():
        path = module.ROOT / f"docs/benchmarks/embedding-gate-ablation-{variant}.json"
        if module.baseline.file_digest(path) != digest:
            raise ValueError(f"Frozen artifact SHA-256 differs: {variant}")
        saved = artifacts[f"docs/benchmarks/embedding-gate-ablation-{variant}.json"]
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
    frozen = artifacts["docs/benchmarks/embedding-semantic-gate-replay.json"]
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
    return _offline(module, public=True)


def _offline(module, *, public=False):
    # Internal calls also handle derived judgment projections; authenticate user
    # dictionaries only at the public boundary, before any validator or replay.
    namespace = dict(vars(module))
    for dependency in ("replay", "precision", "relations"):
        if dependency in namespace:
            namespace[dependency] = _offline(namespace[dependency])
    for name in _OFFLINE_FUNCTIONS:
        function = namespace.get(name)
        if isinstance(function, FunctionType) and function.__globals__ is vars(module):
            namespace[name] = _bind(function, namespace)
    if "validate_collection" in namespace:
        namespace["validate_collection"] = _checked(namespace["validate_collection"], module)
    if "run_replay" in namespace:
        namespace["run_replay"] = lambda: _replay(module)
    if public:
        namespace = dict(namespace)
        for dependency in ("replay", "precision", "relations"):
            if dependency in namespace:
                namespace[dependency] = offline(getattr(module, dependency))
        for name in _OFFLINE_FUNCTIONS:
            if name in namespace:
                namespace[name] = _entry(namespace[name], module)
    return SimpleNamespace(**namespace)
