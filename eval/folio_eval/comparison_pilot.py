"""Power-loss-safe item sharding and finalization for the U10 comparison pilot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .answer_rule import load_config
from .comparison import (
    IncumbentInstallMismatch,
    PublicComparisonMetadata,
    StackRun,
    _file_fingerprint,
    _git_repository_state,
    _probe_environment,
    assert_incumbent_probe,
    build_comparison,
    emit_items_file,
    load_public_comparison_metadata,
    prepare_incumbent,
    write_comparison,
    write_stage_snapshots,
)
from .downstream import FOLIO_RESOLVE_ROOT, enrich_spec, mapper_spec
from .intake import sha256_bytes
from .leakcheck import load_manifest
from .selftest import assert_ontology_pin
from .synthesize import LoadedCorpus, load_corpus
from .synthetic_checkpoint import _atomic_create, fsync_directory

PILOT_CHECKPOINT_KIND = "synthetic-comparison-pilot-checkpoint"
PILOT_CHECKPOINT_VERSION = 1
SHARD_COMPLETION_KIND = "synthetic-comparison-pilot-shard-completion"
SHARD_COMPLETION_VERSION = 1
FINAL_COMPLETION_KIND = "synthetic-comparison-pilot-final-completion"
FINAL_COMPLETION_VERSION = 1
DEFAULT_LIMIT = 30
INCUMBENT_VERSION = "0.4.0"
PUBLISHED_COMPARISON_REPORT = Path("eval/reports/synthetic-comparison-v1.json")
PILOT_STACK_LANES = (
    ("folio-enrich", "incumbent"),
    ("folio-mapper", "incumbent"),
    ("folio-resolve", "candidate"),
)
_OFFLINE_RUNTIME_OVERRIDES = {
    "ACCELERATE_USE_CPU": "true",
    "CUDA_VISIBLE_DEVICES": "",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "HIP_VISIBLE_DEVICES": "",
    "MKL_DYNAMIC": "FALSE",
    "MKL_NUM_THREADS": "1",
    "NVIDIA_VISIBLE_DEVICES": "none",
    "NUMEXPR_NUM_THREADS": "1",
    "OMP_DYNAMIC": "FALSE",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "ROCR_VISIBLE_DEVICES": "",
    "TOKENIZERS_PARALLELISM": "false",
    "TRANSFORMERS_OFFLINE": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
_MUTABLE_RUNTIME_OVERRIDE_KEYS = (
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "GOMP_CPU_AFFINITY",
    "KMP_AFFINITY",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "HF_ASSETS_CACHE",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HF_MODULES_CACHE",
    "HF_XET_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "MKL_CBWR",
    "MKL_DEBUG_CPU_TYPE",
    "OMP_PLACES",
    "OMP_PROC_BIND",
    "OPENBLAS_CORETYPE",
    "PYTHONHOME",
    "PYTHONOPTIMIZE",
    "PYTHONPYCACHEPREFIX",
    "PYTHONPATH",
    "PYTORCH_PRETRAINED_BERT_CACHE",
    "PYTORCH_TRANSFORMERS_CACHE",
    "SENTENCE_TRANSFORMERS_HOME",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "XDG_CACHE_HOME",
)

_STARTUP_CUSTOMIZATION_PROBE = r"""
import importlib.machinery
import json
import site
import sys
import sysconfig
from pathlib import Path


venv_root = Path(sys.argv[1])
version_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
site_roots = {
    venv_root / "Lib" / "site-packages",
    venv_root / "lib" / version_dir / "site-packages",
}
config_path = venv_root / "pyvenv.cfg"
include_system_site = not config_path.is_file()
if config_path.is_file():
    for line in config_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() == "include-system-site-packages":
            include_system_site = value.strip().lower() == "true"
if include_system_site:
    site_roots.update(
        Path(path)
        for key in ("purelib", "platlib")
        if (path := sysconfig.get_path(key))
    )
    user_site = site.getusersitepackages()
    site_roots.update(
        Path(path)
        for path in ([user_site] if isinstance(user_site, str) else user_site)
    )

search_paths = [Path(path) for path in sys.path if path]
for site_root in sorted(site_roots):
    if not site_root.is_dir():
        continue
    search_paths.append(site_root)
    for pth_path in sorted(site_root.glob("*.pth")):
        for line in pth_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith(("import ", "import\t"))
            ):
                continue
            candidate = Path(stripped)
            if not candidate.is_absolute():
                candidate = site_root / candidate
            if candidate.exists():
                search_paths.append(candidate)

found = []
for name in ("sitecustomize", "usercustomize"):
    spec = importlib.machinery.PathFinder.find_spec(
        name, [str(path) for path in search_paths]
    )
    if spec is not None:
        found.append({"name": name, "origin": str(spec.origin or "")})
print(json.dumps({"found": found}, ensure_ascii=True, separators=(",", ":")))
"""


_CONSUMER_ENVIRONMENT_PROBE = r"""
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import marshal
import os
import platform
import re
import struct
import sys
import sysconfig
import tomllib
import types
from pathlib import Path
from urllib.parse import unquote, urlparse


if any(name in sys.modules for name in ("sitecustomize", "usercustomize")):
    raise RuntimeError("startup customization module is not allowed")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def stat_identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


code_fields = (
    "co_argcount",
    "co_posonlyargcount",
    "co_kwonlyargcount",
    "co_nlocals",
    "co_stacksize",
    "co_flags",
    "co_code",
    "co_names",
    "co_varnames",
    "co_filename",
    "co_name",
    "co_qualname",
    "co_firstlineno",
    "co_linetable",
    "co_exceptiontable",
    "co_freevars",
    "co_cellvars",
)


def constant_signature(value):
    if isinstance(value, types.CodeType):
        return ("code", code_signature(value))
    if isinstance(value, tuple):
        return ("tuple", tuple(constant_signature(item) for item in value))
    if isinstance(value, frozenset):
        return (
            "frozenset",
            tuple(constant_signature(item) for item in value),
        )
    if value is None:
        return ("none",)
    if value is Ellipsis:
        return ("ellipsis",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", struct.pack("!d", value))
    if isinstance(value, complex):
        return (
            "complex",
            struct.pack("!d", value.real),
            struct.pack("!d", value.imag),
        )
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value)
    return (type(value).__qualname__, marshal.dumps(value))


def code_signature(code):
    constants = tuple(constant_signature(value) for value in code.co_consts)
    return tuple(getattr(code, field) for field in code_fields) + (constants,)


def is_regenerable_bytecode_cache(path):
    if path.suffix not in {".pyc", ".pyo"}:
        return False
    if not path.is_file():
        return True
    if path.is_symlink():
        raise RuntimeError("symlinked bytecode cache is not allowed")
    if path.suffix == ".pyo" or "__pycache__" not in path.parts:
        raise RuntimeError("legacy bytecode cache is not allowed")
    cache_tag = sys.implementation.cache_tag
    tool_cache_pattern = rf".+\.{re.escape(cache_tag)}-[^.]+(?:\.[^.]+)*\.pyc"
    if re.fullmatch(tool_cache_pattern, path.name):
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path)))
    except ValueError as exc:
        raise RuntimeError("malformed bytecode cache is not allowed") from exc
    if not source.is_file():
        raise RuntimeError("sourceless bytecode cache is not allowed")
    if source.is_symlink():
        raise RuntimeError("symlinked bytecode source is not allowed")

    default_name = f"{source.stem}.{cache_tag}.pyc"
    optimized_name = re.fullmatch(
        rf"{re.escape(source.stem)}\.{re.escape(cache_tag)}\.opt-([0-9]+)\.pyc",
        path.name,
    )
    if path.name == default_name:
        optimization = 0
    elif optimized_name and optimized_name.group(1) in {"1", "2"}:
        optimization = int(optimized_name.group(1))
    elif optimized_name:
        return False
    else:
        raise RuntimeError("malformed bytecode cache is not allowed")

    source_before = source.stat()
    source_bytes = source.read_bytes()
    source_after = source.stat()
    if stat_identity(source_before) != stat_identity(source_after):
        raise RuntimeError("bytecode source changed during validation")

    cache_before = path.stat()
    cache_bytes = path.read_bytes()
    cache_after = path.stat()
    if stat_identity(cache_before) != stat_identity(cache_after):
        raise RuntimeError("bytecode cache changed during validation")
    if len(cache_bytes) < 16 or cache_bytes[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError("malformed bytecode cache is not allowed")
    flags = int.from_bytes(cache_bytes[4:8], "little")
    if flags & ~0b11:
        raise RuntimeError("malformed bytecode cache is not allowed")
    if flags & 0b1:
        if cache_bytes[8:16] != importlib.util.source_hash(source_bytes):
            raise RuntimeError("stale bytecode cache is not allowed")
    else:
        cached_mtime = int.from_bytes(cache_bytes[8:12], "little")
        cached_size = int.from_bytes(cache_bytes[12:16], "little")
        if cached_mtime != int(source_after.st_mtime) & 0xFFFFFFFF or cached_size != len(
            source_bytes
        ) & 0xFFFFFFFF:
            raise RuntimeError("stale bytecode cache is not allowed")

    payload = io.BytesIO(cache_bytes[16:])
    try:
        cached_code = marshal.load(payload)
    except (EOFError, TypeError, ValueError) as exc:
        raise RuntimeError("malformed bytecode cache is not allowed") from exc
    if payload.read(1) or not isinstance(cached_code, types.CodeType):
        raise RuntimeError("malformed bytecode cache is not allowed")
    try:
        source_code = compile(
            source_bytes,
            str(source),
            "exec",
            dont_inherit=True,
            optimize=optimization,
        )
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError("bytecode source cannot be compiled") from exc

    source_code = marshal.loads(marshal.dumps(source_code))
    if code_signature(cached_code) != code_signature(source_code):
        raise RuntimeError("bytecode cache does not match its source")
    return True


allowed_executable_pth_lines = {
    "import _cuda_bindings_redirector",
    "import _virtualenv",
    "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') == 'local'; enabled and __import__('_distutils_hack').add_shim();",
}


def read_pth_paths(path):
    paths = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "import\t")):
            if stripped not in allowed_executable_pth_lines:
                raise RuntimeError("unsupported executable .pth line is not allowed")
            continue
        if stripped and not stripped.startswith("#"):
            candidate = Path(stripped)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if candidate.is_dir():
                paths.append(candidate.resolve())
    return paths


distributions = []
installed_entries = []
editable_entries = []
owned_import_roots = []
resolved_digests = {}
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name", "")
    canonical_name = re.sub(r"[-_.]+", "-", name).lower()
    record = (distribution.read_text("RECORD") or "").encode()
    direct_url_text = distribution.read_text("direct_url.json") or ""
    direct_url = direct_url_text.encode()
    distribution_files = []
    pth_paths = []
    for relative in distribution.files or ():
        path = Path(distribution.locate_file(relative))
        if is_regenerable_bytecode_cache(path):
            continue
        if not path.is_file():
            distribution_files.append([str(relative), "missing", 0])
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        entry = [str(relative), resolved_digests[cache_key], resolved.stat().st_size]
        distribution_files.append(entry)
        installed_entries.append([canonical_name, *entry])
        if path.suffix == ".pth":
            pth_paths.extend(read_pth_paths(path))

    editable_sources = []
    excluded_source_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
    }
    if direct_url_text:
        direct_url_payload = json.loads(direct_url_text)
        if direct_url_payload.get("dir_info", {}).get("editable") is True:
            parsed = urlparse(direct_url_payload.get("url", ""))
            editable_root = Path(unquote(parsed.path)).resolve()
            source_roots = []
            for candidate in pth_paths:
                try:
                    candidate.relative_to(editable_root)
                except ValueError as exc:
                    raise RuntimeError("editable .pth import root is outside its project") from exc
                source_roots.append(candidate)
                owned_import_roots.append(candidate)
            if not source_roots:
                pyproject = editable_root / "pyproject.toml"
                if pyproject.is_file():
                    project_config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                    hatch_packages = (
                        project_config.get("tool", {})
                        .get("hatch", {})
                        .get("build", {})
                        .get("targets", {})
                        .get("wheel", {})
                        .get("packages", [])
                    )
                    source_roots.extend(
                        (editable_root / package).resolve()
                        for package in hatch_packages
                        if isinstance(package, str) and (editable_root / package).is_dir()
                    )
            if not source_roots and (editable_root / "src").is_dir():
                source_roots.append((editable_root / "src").resolve())
            if not source_roots:
                package_names = {
                    canonical_name.replace("-", "_"),
                    canonical_name.split("-", 1)[0],
                }
                source_roots.extend(
                    candidate.resolve()
                    for package_name in package_names
                    for candidate in (editable_root / package_name,)
                    if candidate.is_dir()
                )
            for source_root in sorted(set(source_roots)):
                for path in sorted(source_root.rglob("*")):
                    if any(part in excluded_source_parts for part in path.parts):
                        continue
                    if path.is_symlink():
                        raise RuntimeError("symlinked editable source entry is not allowed")
                    if not path.is_file():
                        continue
                    if is_regenerable_bytecode_cache(path):
                        continue
                    resolved = path.resolve()
                    cache_key = str(resolved)
                    if cache_key not in resolved_digests:
                        resolved_digests[cache_key] = digest_file(resolved)
                    entry = [
                        path.relative_to(editable_root).as_posix(),
                        resolved_digests[cache_key],
                        resolved.stat().st_size,
                    ]
                    editable_sources.append(entry)
                    editable_entries.append([canonical_name, *entry])
    elif pth_paths:
        raise RuntimeError("unowned .pth import root is not allowed")
    distributions.append([
        canonical_name,
        distribution.version,
        digest_bytes(record),
        digest_bytes(direct_url),
        digest_bytes(json.dumps(distribution_files, separators=(",", ":")).encode()),
        digest_bytes(json.dumps(editable_sources, separators=(",", ":")).encode()),
    ])
distributions.sort()
installed_entries.sort()
editable_entries.sort()
distribution_payload = json.dumps(
    distributions, ensure_ascii=True, separators=(",", ":")
).encode()
installed_payload = json.dumps(
    installed_entries, ensure_ascii=True, separators=(",", ":")
).encode()
editable_payload = json.dumps(
    editable_entries, ensure_ascii=True, separators=(",", ":")
).encode()

stdlib_roots = {
    Path(path).resolve()
    for key in ("stdlib", "platstdlib")
    if (path := sysconfig.get_path(key))
}
site_roots = {
    Path(path).resolve()
    for key in ("purelib", "platlib")
    if (path := sysconfig.get_path(key))
}
stdlib_zips = {
    root.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    for root in stdlib_roots
}
stdlib_entries = []
for stdlib_root in sorted(stdlib_roots):
    if not stdlib_root.is_dir():
        continue
    for path in sorted(stdlib_root.rglob("*")):
        relative_parts = path.relative_to(stdlib_root).parts
        if any(part in {"site-packages", "dist-packages"} for part in relative_parts):
            continue
        if any(path == site_root or site_root in path.parents for site_root in site_roots):
            continue
        if path.is_symlink():
            raise RuntimeError("symlinked standard-library entry is not allowed")
        if not path.is_file():
            continue
        if is_regenerable_bytecode_cache(path):
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        stdlib_entries.append([
            str(stdlib_root),
            path.relative_to(stdlib_root).as_posix(),
            resolved_digests[cache_key],
            resolved.stat().st_size,
        ])
for stdlib_zip in sorted(stdlib_zips):
    if stdlib_zip.is_symlink():
        raise RuntimeError("symlinked standard-library zip is not allowed")
    if stdlib_zip.is_file():
        resolved = stdlib_zip.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        stdlib_entries.append([
            str(stdlib_zip.parent),
            stdlib_zip.name,
            resolved_digests[cache_key],
            resolved.stat().st_size,
        ])
stdlib_entries.sort()
stdlib_payload = json.dumps(
    stdlib_entries, ensure_ascii=True, separators=(",", ":")
).encode()
site_entries = []
for site_root in sorted(site_roots):
    if not site_root.is_dir():
        continue
    for pth_path in sorted(site_root.glob("*.pth")):
        if pth_path.is_file():
            read_pth_paths(pth_path)
    for path in sorted(site_root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("symlinked site-packages entry is not allowed")
        if not path.is_file():
            continue
        if is_regenerable_bytecode_cache(path):
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        site_entries.append([
            site_root.name,
            path.relative_to(site_root).as_posix(),
            resolved_digests[cache_key],
            resolved.stat().st_size,
        ])
site_entries.sort()
site_payload = json.dumps(site_entries, ensure_ascii=True, separators=(",", ":")).encode()
effective_import_paths = []
for entry in sys.path:
    if not entry:
        raise RuntimeError("unsafe empty import root is not allowed")
    path = Path(entry).resolve()
    if (
        path.name in {"site-packages", "dist-packages"}
        and path not in site_roots
    ):
        raise RuntimeError("active base site-packages root is not allowed")
    allowed = (
        path in site_roots
        or path in stdlib_zips
        or any(path == root or root in path.parents for root in stdlib_roots)
        or any(path == root or root in path.parents for root in owned_import_roots)
    )
    if not allowed:
        raise RuntimeError("unowned effective import root is not allowed")
    effective_import_paths.append(str(path))
effective_import_paths.sort()
import_path_payload = json.dumps(
    effective_import_paths, ensure_ascii=True, separators=(",", ":")
).encode()

meta_path = [
    [
        getattr(finder, "__module__", type(finder).__module__),
        getattr(finder, "__qualname__", type(finder).__qualname__),
    ]
    for finder in sys.meta_path
]
allowed_meta_path = [
    ["_distutils_hack", "DistutilsMetaFinder"],
    ["_virtualenv", "_Finder"],
    ["_frozen_importlib", "BuiltinImporter"],
    ["_frozen_importlib", "FrozenImporter"],
    ["_frozen_importlib_external", "PathFinder"],
]
if (
    any(entry not in allowed_meta_path for entry in meta_path)
    or meta_path[-3:] != allowed_meta_path[-3:]
):
    raise RuntimeError("unsupported meta-path finder is not allowed")
meta_path_payload = json.dumps(meta_path, ensure_ascii=True, separators=(",", ":")).encode()

path_hooks = [
    [
        getattr(hook, "__module__", type(hook).__module__),
        getattr(hook, "__qualname__", type(hook).__qualname__),
    ]
    for hook in sys.path_hooks
]
allowed_path_hooks = [
    ["zipimport", "zipimporter"],
    ["_frozen_importlib_external", "FileFinder.path_hook.<locals>.path_hook_for_FileFinder"],
]
if path_hooks != allowed_path_hooks:
    raise RuntimeError("unsupported path hook is not allowed")
path_hooks_payload = json.dumps(path_hooks, ensure_ascii=True, separators=(",", ":")).encode()

try:
    from huggingface_hub.constants import HF_HUB_CACHE
    model_root = Path(HF_HUB_CACHE) / "models--sentence-transformers--all-MiniLM-L6-v2"
except Exception:
    model_root = Path("/__missing_huggingface_cache__")

model_entries = []
if model_root.is_dir():
    for path in sorted(model_root.rglob("*")):
        if path.is_symlink() and path.is_dir():
            raise RuntimeError("symlinked model-cache directory is not allowed")
        if not path.is_file():
            continue
        resolved = path.resolve()
        cache_key = str(resolved)
        if cache_key not in resolved_digests:
            resolved_digests[cache_key] = digest_file(resolved)
        model_entries.append([
            path.relative_to(model_root).as_posix(),
            resolved_digests[cache_key],
            resolved.stat().st_size,
        ])
model_payload = json.dumps(model_entries, ensure_ascii=True, separators=(",", ":")).encode()
derived_cache_entries = []
derived_cache_root = Path.home() / ".folio" / "cache" / "embeddings"
if os.environ.get("FOLIO_PROBE_REQUIRE_MAPPER_CACHE") == "1":
    if derived_cache_root.is_symlink():
        raise RuntimeError("symlinked mapper embedding-cache root is not allowed")
    for path in sorted(derived_cache_root.glob("all-MiniLM-L6-v2_cpu_*.pkl")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("symlinked mapper embedding-cache entry is not allowed")
        derived_cache_entries.append([
            path.name,
            digest_file(path),
            path.stat().st_size,
        ])
derived_cache_payload = json.dumps(
    derived_cache_entries, ensure_ascii=True, separators=(",", ":")
).encode()
model_assets_complete = False
model_embedding_dimension = 0
model_device = ""
model_snapshot_revision = ""
if os.environ.get("FOLIO_PROBE_REQUIRE_MODEL_ASSETS") == "1":
    ref_path = model_root / "refs" / "main"
    if not ref_path.is_file():
        raise RuntimeError("embedding model cache has no pinned main revision")
    model_snapshot_revision = ref_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", model_snapshot_revision):
        raise RuntimeError("embedding model cache revision is malformed")
    snapshot_root = model_root / "snapshots" / model_snapshot_revision
    if not snapshot_root.is_dir():
        raise RuntimeError("embedding model cache snapshot is missing")
    if any(path.name.endswith(".incomplete") for path in model_root.rglob("*")):
        raise RuntimeError("embedding model cache contains an incomplete download")
    if any(path.is_symlink() and not path.exists() for path in snapshot_root.rglob("*")):
        raise RuntimeError("embedding model cache snapshot contains a broken link")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2",
        device="cpu",
        revision=model_snapshot_revision,
        local_files_only=True,
    )
    model_device = str(model.device)
    if model_device != "cpu":
        raise RuntimeError(
            f"embedding model loaded on {model_device!r}, expected 'cpu'"
        )
    dimension = model.get_sentence_embedding_dimension()
    if not isinstance(dimension, int) or dimension < 1:
        raise RuntimeError("embedding model cache did not load a valid dimension")
    verified_entries = []
    for path in sorted(model_root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        verified_entries.append([
            path.relative_to(model_root).as_posix(),
            digest_file(resolved),
            resolved.stat().st_size,
        ])
    verified_payload = json.dumps(
        verified_entries, ensure_ascii=True, separators=(",", ":")
    ).encode()
    if verified_payload != model_payload or ref_path.read_text(encoding="utf-8").strip() != model_snapshot_revision:
        raise RuntimeError("embedding model cache mutated during offline verification")
    model_embedding_dimension = dimension
    model_assets_complete = True

cpuinfo_keys = {
    "cpu architecture",
    "cpu implementer",
    "cpu part",
    "cpu revision",
    "cpu variant",
    "features",
    "flags",
    "model",
    "model name",
    "vendor_id",
}
cpuinfo_records = set()
cpuinfo_path = Path("/proc/cpuinfo")
if cpuinfo_path.is_file():
    record = {}
    for line in (*cpuinfo_path.read_text(encoding="utf-8").splitlines(), ""):
        if not line.strip():
            if record:
                cpuinfo_records.add(tuple(sorted(record.items())))
                record = {}
            continue
        key, separator, value = line.partition(":")
        normalized_key = key.strip().lower()
        if separator and normalized_key in cpuinfo_keys:
            record[normalized_key] = " ".join(value.split())

cpu_backend = {
    "cpuinfo_records": [list(record) for record in sorted(cpuinfo_records)],
    "logical_cpu_count": os.cpu_count() or 0,
    "machine": platform.machine(),
    "processor": platform.processor(),
    "system": platform.system(),
}
native_runtime_paths = set()
if importlib.util.find_spec("numpy") is not None:
    import numpy

    numpy_core = getattr(numpy, "_core", getattr(numpy, "core", None))
    numpy_multiarray = getattr(numpy_core, "_multiarray_umath", None)
    numpy_features = getattr(numpy_multiarray, "__cpu_features__", {})
    cpu_backend["numpy_cpu_features"] = sorted(
        str(name) for name, enabled in numpy_features.items() if enabled
    )
if importlib.util.find_spec("torch") is not None:
    import torch

    get_cpu_capability = getattr(torch.backends.cpu, "get_cpu_capability", None)
    cpu_backend["torch_cpu_capability"] = (
        str(get_cpu_capability()) if callable(get_cpu_capability) else ""
    )
    cpu_backend["torch_backends"] = {
        "mkl": bool(torch.backends.mkl.is_available()),
        "mkldnn": bool(torch.backends.mkldnn.is_available()),
        "openmp": bool(torch.backends.openmp.is_available()),
    }
if importlib.util.find_spec("threadpoolctl") is not None:
    import threadpoolctl

    threadpool_info = threadpoolctl.threadpool_info()
    native_runtime_paths.update(
        Path(entry["filepath"])
        for entry in threadpool_info
        if isinstance(entry.get("filepath"), str) and entry["filepath"]
    )
    cpu_backend["threadpools"] = sorted(
        (
            {
                key: value
                for key, value in entry.items()
                if key
                in {
                    "architecture",
                    "internal_api",
                    "num_threads",
                    "prefix",
                    "threading_layer",
                    "user_api",
                    "version",
                }
            }
            for entry in threadpool_info
        ),
        key=lambda entry: json.dumps(entry, sort_keys=True, separators=(",", ":")),
    )
maps_path = Path("/proc/self/maps")
if not maps_path.is_file():
    raise RuntimeError("complete loaded-image enumeration requires /proc/self/maps")
for line in maps_path.read_text(encoding="utf-8").splitlines():
    fields = line.split(maxsplit=5)
    if len(fields) != 6 or not fields[5].startswith("/"):
        continue
    mapped_path = fields[5]
    if mapped_path.endswith(" (deleted)"):
        raise RuntimeError("deleted native runtime mapping is not allowed")
    native_runtime_paths.add(Path(mapped_path))
native_runtime_entries = []
for path in sorted({path.resolve() for path in native_runtime_paths}):
    if not path.is_file():
        raise RuntimeError("loaded native runtime file is unavailable")
    cache_key = str(path)
    if cache_key not in resolved_digests:
        resolved_digests[cache_key] = digest_file(path)
    native_runtime_entries.append([
        str(path),
        resolved_digests[cache_key],
        path.stat().st_size,
    ])
native_runtime_payload = json.dumps(
    native_runtime_entries, ensure_ascii=True, separators=(",", ":")
).encode()
cpu_backend_payload = json.dumps(
    cpu_backend, ensure_ascii=True, sort_keys=True, separators=(",", ":")
).encode()
interpreter = Path(sys.executable).resolve()
print(json.dumps({
    "schema_version": 1,
    "interpreter_path_sha256": digest_bytes(str(interpreter).encode()),
    "interpreter_sha256": digest_file(interpreter),
    "python_version": sys.version,
    "python_optimize": sys.flags.optimize,
    "cpu_backend_sha256": digest_bytes(cpu_backend_payload),
    "native_runtime_file_count": len(native_runtime_entries),
    "native_runtime_bytes": sum(entry[2] for entry in native_runtime_entries),
    "native_runtime_sha256": digest_bytes(native_runtime_payload),
    "distribution_count": len(distributions),
    "distributions_sha256": digest_bytes(distribution_payload),
    "installed_file_count": len(installed_entries),
    "installed_file_bytes": sum(entry[3] for entry in installed_entries),
    "installed_files_sha256": digest_bytes(installed_payload),
    "site_file_count": len(site_entries),
    "site_file_bytes": sum(entry[3] for entry in site_entries),
    "site_files_sha256": digest_bytes(site_payload),
    "stdlib_file_count": len(stdlib_entries),
    "stdlib_file_bytes": sum(entry[3] for entry in stdlib_entries),
    "stdlib_files_sha256": digest_bytes(stdlib_payload),
    "editable_source_files": len(editable_entries),
    "editable_source_bytes": sum(entry[3] for entry in editable_entries),
    "editable_sources_sha256": digest_bytes(editable_payload),
    "derived_cache_bytes": sum(entry[2] for entry in derived_cache_entries),
    "derived_cache_complete": bool(derived_cache_entries),
    "derived_cache_files": len(derived_cache_entries),
    "derived_cache_sha256": digest_bytes(derived_cache_payload),
    "import_path_entries": len(effective_import_paths),
    "import_path_sha256": digest_bytes(import_path_payload),
    "meta_path_entries": len(meta_path),
    "meta_path_sha256": digest_bytes(meta_path_payload),
    "path_hook_entries": len(path_hooks),
    "path_hooks_sha256": digest_bytes(path_hooks_payload),
    "model_asset_files": len(model_entries),
    "model_asset_bytes": sum(entry[2] for entry in model_entries),
    "model_assets_present": bool(model_entries),
    "model_assets_complete": model_assets_complete,
    "model_assets_sha256": digest_bytes(model_payload),
    "model_embedding_dimension": model_embedding_dimension,
    "model_device": model_device,
    "model_snapshot_revision_sha256": digest_bytes(model_snapshot_revision.encode()),
}, sort_keys=True, separators=(",", ":")))
"""


class PilotCheckpointError(RuntimeError):
    """A pilot shard or its fingerprint is missing, corrupt, or incompatible."""


def _runtime_environment() -> dict[str, str]:
    """Return the inherited environment without mutable runtime overrides."""
    environment = dict(os.environ)
    for key in _MUTABLE_RUNTIME_OVERRIDE_KEYS:
        environment.pop(key, None)
    return environment


def _offline_runtime_environment() -> dict[str, str]:
    """Return a deterministic child environment that cannot mutate model or bytecode caches."""
    environment = _runtime_environment()
    environment.update(_OFFLINE_RUNTIME_OVERRIDES)
    return environment


def _assert_clean_runtime_environment() -> None:
    overrides = [key for key in _MUTABLE_RUNTIME_OVERRIDE_KEYS if os.environ.get(key)]
    if overrides:
        raise PilotCheckpointError(
            "comparison pilot refuses mutable runtime overrides: " + ", ".join(overrides)
        )


def _assert_unoptimized_runtime() -> None:
    if sys.flags.optimize != 0:
        raise PilotCheckpointError("comparison pilot requires Python optimization level 0")


def _resolve_path_arguments(args: argparse.Namespace) -> None:
    """Freeze caller-relative CLI paths before child processes change working directory."""
    for name in (
        "corpus_manifest",
        "config",
        "out",
        "checkpoint_dir",
        "leak_manifest",
        "salt_file",
        "public_metadata",
        "mapper_root",
        "enrich_root",
    ):
        setattr(args, name, getattr(args, name).resolve())


def _assert_write_paths_are_safe(
    args: argparse.Namespace, corpus: LoadedCorpus | None = None
) -> None:
    """Keep mutable checkpoints ignored while allowing the planned final artifact."""
    try:
        args.out.relative_to(args.checkpoint_dir)
    except ValueError:
        try:
            args.checkpoint_dir.relative_to(args.out)
        except ValueError:
            pass
        else:
            raise PilotCheckpointError("checkpoint_dir and out must not overlap")
    else:
        raise PilotCheckpointError("checkpoint_dir and out must not overlap")
    input_paths = [
        (input_name, getattr(args, input_name, None))
        for input_name in (
            "corpus_manifest",
            "config",
            "leak_manifest",
            "salt_file",
            "public_metadata",
        )
    ]
    if corpus is not None:
        input_paths.extend(
            (
                ("corpus_scoreable_jsonl", corpus.manifest.corpus_path.resolve()),
                ("corpus_nomatch_jsonl", corpus.manifest.nomatch_path.resolve()),
            )
        )
    for input_name, input_path in input_paths:
        if input_path is None:
            continue
        if (
            args.out == input_path
            or args.checkpoint_dir == input_path
            or args.checkpoint_dir in input_path.parents
        ):
            raise PilotCheckpointError(
                f"writable pilot path must not overlap {input_name}: {input_path}"
            )
    repositories = {
        "candidate": FOLIO_RESOLVE_ROOT.resolve(),
        "enrich": args.enrich_root,
        "mapper": args.mapper_root,
    }
    for output_name in ("checkpoint_dir", "out"):
        output_path = getattr(args, output_name)
        for repository_name, repository_root in repositories.items():
            try:
                relative = output_path.relative_to(repository_root)
            except ValueError:
                continue
            protected_roots = tuple(
                repository_root / name for name in (".venv", "backend", "src")
            )
            if any(
                output_path == protected
                or protected in output_path.parents
                for protected in protected_roots
            ):
                raise PilotCheckpointError(
                    f"{output_name} must be outside fingerprinted source roots in the "
                    f"{repository_name} repository: {output_path}"
                )
            if (
                output_name == "out"
                and repository_name == "candidate"
                and relative == PUBLISHED_COMPARISON_REPORT
            ):
                continue
            completed = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
                cwd=repository_root,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 1:
                raise PilotCheckpointError(
                    f"{output_name} must be Git-ignored inside the "
                    f"{repository_name} repository: {output_path}"
                )
            if completed.returncode != 0:
                raise PilotCheckpointError(
                    f"could not verify {output_name} ignore status in the "
                    f"{repository_name} repository"
                )


def _assert_existing_canonical_report_is_recoverable(
    args: argparse.Namespace, item_ids: Sequence[str]
) -> None:
    """Allow an untracked final report only for a fully completed checkpoint recovery."""
    canonical = FOLIO_RESOLVE_ROOT / PUBLISHED_COMPARISON_REPORT
    if args.out != canonical or not args.out.is_file():
        return
    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            PUBLISHED_COMPARISON_REPORT.as_posix(),
        ],
        cwd=FOLIO_RESOLVE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PilotCheckpointError("could not inspect the existing canonical pilot report")
    if completed.stdout.strip() != f"?? {PUBLISHED_COMPARISON_REPORT.as_posix()}":
        return
    manifest_exists = (args.checkpoint_dir / "manifest.json").is_file()
    all_shards_complete = manifest_exists and all(
        _shard_completion_path(args.checkpoint_dir, item_id).is_file()
        for item_id in item_ids
    )
    if not all_shards_complete:
        raise PilotCheckpointError(
            "untracked canonical pilot report is not recoverable from this checkpoint"
        )


def _command_path(path: Path) -> str:
    """Render repository inputs stably while retaining absolute external targets."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(FOLIO_RESOLVE_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item_key(item_id: str) -> str:
    return hashlib.sha256(item_id.encode()).hexdigest()


def _assert_no_ignored_importables(repo_root: Path, import_root: str) -> dict[str, object]:
    """Reject sourceless imports and hash source-associated ignored bytecode."""
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            import_root,
            f":(exclude){import_root}/.venv/**",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PilotCheckpointError(
            f"could not inspect ignored importables under {repo_root / import_root}"
        )
    bytecode_entries: list[tuple[str, str, int]] = []
    rejected = []
    for raw_path in completed.stdout.splitlines():
        relative = Path(raw_path)
        if ".venv" in relative.parts:
            continue
        suffix = relative.suffix.lower()
        if suffix in {".pyc", ".pyo"} and "__pycache__" in relative.parts:
            path = repo_root / relative
            if not path.is_file():
                raise PilotCheckpointError(f"ignored bytecode is unavailable: {path}")
            bytecode_entries.append(
                (relative.as_posix(), _sha256_file(path), path.stat().st_size)
            )
            continue
        if suffix in {".py", ".pyc", ".pyo", ".pyd", ".so"}:
            rejected.append(relative.as_posix())
    if rejected:
        raise PilotCheckpointError(
            f"ignored executable import files are not allowed under {repo_root / import_root}: "
            + ", ".join(sorted(rejected))
        )
    bytecode_entries.sort()
    return {
        "bytes": sum(entry[2] for entry in bytecode_entries),
        "files": len(bytecode_entries),
        "sha256": sha256_bytes(
            json.dumps(bytecode_entries, ensure_ascii=True, separators=(",", ":")).encode()
        ),
    }


def _consumer_environment_fingerprint(
    venv_python: Path,
    *,
    require_mapper_cache: bool = False,
    require_model_assets: bool = False,
) -> dict[str, object]:
    """Hash the actual consumer interpreter, distributions, and cached embedding model."""
    environment = _offline_runtime_environment()
    try:
        startup_probe = subprocess.run(
            [
                str(venv_python),
                "-I",
                "-S",
                "-B",
                "-c",
                _STARTUP_CUSTOMIZATION_PROBE,
                str(venv_python.parent.parent),
            ],
            capture_output=True,
            env=environment,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PilotCheckpointError("startup customization probe could not run") from exc
    if startup_probe.returncode:
        raise PilotCheckpointError(
            f"startup customization probe failed (rc={startup_probe.returncode})"
        )
    try:
        startup_payload = json.loads(startup_probe.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("startup customization probe was malformed") from exc
    if (
        not isinstance(startup_payload, dict)
        or not isinstance(startup_payload.get("found"), list)
        or any(
            not isinstance(entry, dict)
            or entry.get("name") not in {"sitecustomize", "usercustomize"}
            or not isinstance(entry.get("origin"), str)
            for entry in startup_payload["found"]
        )
    ):
        raise PilotCheckpointError("startup customization probe was malformed")
    if startup_payload["found"]:
        raise PilotCheckpointError("startup customization module is not allowed")
    if require_model_assets:
        environment["FOLIO_PROBE_REQUIRE_MODEL_ASSETS"] = "1"
    if require_mapper_cache:
        environment["FOLIO_PROBE_REQUIRE_MAPPER_CACHE"] = "1"
    try:
        completed = subprocess.run(
            [str(venv_python), "-B", "-P", "-c", _CONSUMER_ENVIRONMENT_PROBE],
            capture_output=True,
            env=environment,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PilotCheckpointError("consumer environment probe could not run") from exc
    if completed.returncode:
        raise PilotCheckpointError(f"consumer environment probe failed (rc={completed.returncode})")
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("consumer environment probe was malformed") from exc
    required_digests = {
        "cpu_backend_sha256",
        "interpreter_path_sha256",
        "interpreter_sha256",
        "distributions_sha256",
        "derived_cache_sha256",
        "installed_files_sha256",
        "site_files_sha256",
        "stdlib_files_sha256",
        "editable_sources_sha256",
        "import_path_sha256",
        "meta_path_sha256",
        "path_hooks_sha256",
        "model_assets_sha256",
        "model_snapshot_revision_sha256",
        "native_runtime_sha256",
    }
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or any(
            not isinstance(payload.get(key), str)
            or len(payload[key]) != 64
            or any(char not in "0123456789abcdef" for char in payload[key])
            for key in required_digests
        )
    ):
        raise PilotCheckpointError("consumer environment probe was malformed")
    for key in (
        "distribution_count",
        "derived_cache_bytes",
        "derived_cache_files",
        "installed_file_count",
        "installed_file_bytes",
        "site_file_count",
        "site_file_bytes",
        "stdlib_file_count",
        "stdlib_file_bytes",
        "editable_source_files",
        "editable_source_bytes",
        "import_path_entries",
        "meta_path_entries",
        "path_hook_entries",
        "model_asset_files",
        "model_asset_bytes",
        "model_embedding_dimension",
        "native_runtime_bytes",
        "native_runtime_file_count",
    ):
        if not isinstance(payload.get(key), int) or payload[key] < 0:
            raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("python_version"), str) or not payload["python_version"]:
        raise PilotCheckpointError("consumer environment probe was malformed")
    if payload.get("python_optimize") != 0:
        raise PilotCheckpointError("consumer environment probe must use optimization level 0")
    if not isinstance(payload.get("model_device"), str):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("model_assets_present"), bool):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("model_assets_complete"), bool):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if not isinstance(payload.get("derived_cache_complete"), bool):
        raise PilotCheckpointError("consumer environment probe was malformed")
    if require_model_assets and (
        payload.get("model_assets_present") is not True
        or payload.get("model_assets_complete") is not True
        or payload.get("model_device") != "cpu"
    ):
        raise PilotCheckpointError(
            "consumer embedding model cache must load completely offline before pilot initialization"
        )
    if require_mapper_cache and payload.get("derived_cache_complete") is not True:
        raise PilotCheckpointError(
            "mapper derived CPU embedding cache must be complete before pilot initialization"
        )
    payload["venv_path_sha256"] = sha256_bytes(str(venv_python.absolute()).encode())
    return payload


def _durably_sync_file(path: Path) -> None:
    """Make an already atomically replaced checkpoint file survive sudden power loss."""
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except OSError as exc:
        raise PilotCheckpointError(f"could not durably publish pilot shard: {path}") from exc


def _durably_create_directory(path: Path) -> None:
    """Create a directory chain and persist every new ancestor entry."""
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    if cursor.exists() and not cursor.is_dir():
        raise PilotCheckpointError(f"pilot checkpoint ancestor is not a directory: {cursor}")
    try:
        for directory in reversed(missing):
            directory.mkdir()
            fsync_directory(directory.parent)
    except OSError as exc:
        raise PilotCheckpointError(
            f"could not durably create pilot checkpoint directory: {path}"
        ) from exc


def _pilot_ids(corpus: LoadedCorpus, limit: int) -> tuple[str, ...]:
    if limit < 1 or limit > len(corpus.scoreable_items):
        raise PilotCheckpointError("pilot scoreable limit is outside the corpus")
    return tuple(item.item_id for item in (*corpus.scoreable_items[:limit], *corpus.nomatch_items))


def _prepare_incumbents(mapper_root: Path, enrich_root: Path) -> None:
    """Perform the one permitted consumer-environment mutation before fingerprinting."""
    mapper = mapper_spec(mapper_root)
    for consumer in (mapper, enrich_spec(enrich_root)):
        prepare_incumbent(consumer, INCUMBENT_VERSION)
    _prepare_mapper_index(mapper)


def _prepare_mapper_index(mapper: Any) -> None:
    """Build the exact CPU-derived mapper cache before sealing the checkpoint."""
    cache_root = _mapper_cpu_cache_root()
    if cache_root.is_symlink():
        raise PilotCheckpointError("symlinked mapper embedding-cache root is not allowed")
    _durably_create_directory(cache_root)
    code = """
from app.models.embedding_models import EmbeddingConfig
from app.services.embedding.service import build_embedding_index, get_embedding_index
build_embedding_index(EmbeddingConfig(device="cpu"))
if get_embedding_index() is None:
    raise RuntimeError("mapper CPU embedding index is unavailable after preparation")
"""
    completed = subprocess.run(
        [str(mapper.venv_python), "-B", "-c", code],
        cwd=str(mapper.repo_root / "backend"),
        capture_output=True,
        env=_offline_runtime_environment(),
        text=True,
        timeout=1800,
    )
    if completed.returncode:
        raise PilotCheckpointError(
            f"mapper CPU embedding index preparation failed (rc={completed.returncode}): "
            f"{completed.stderr.strip()[-4000:]}"
        )
    for cache_path in _mapper_cpu_cache_paths():
        _durably_sync_file(cache_path)


def _mapper_cpu_cache_paths() -> tuple[Path, ...]:
    root = _mapper_cpu_cache_root()
    if root.is_symlink():
        raise PilotCheckpointError("symlinked mapper embedding-cache root is not allowed")
    paths = tuple(sorted(root.glob("all-MiniLM-L6-v2_cpu_*.pkl")))
    if not paths:
        raise PilotCheckpointError("mapper CPU embedding index preparation produced no cache")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise PilotCheckpointError("symlinked mapper embedding-cache entry is not allowed")
    return paths


def _mapper_cpu_cache_root() -> Path:
    return Path.home() / ".folio" / "cache" / "embeddings"


def _prepare_incumbents_for_new_checkpoint(args: argparse.Namespace) -> None:
    """Mutate consumer environments only before a checkpoint manifest exists."""
    if (args.checkpoint_dir / "manifest.json").exists():
        return
    if _incumbent_wheels_are_prepared(args.mapper_root, args.enrich_root):
        _prepare_mapper_index(mapper_spec(args.mapper_root))
        return
    _prepare_incumbents(args.mapper_root, args.enrich_root)


def _incumbent_wheels_are_prepared(mapper_root: Path, enrich_root: Path) -> bool:
    """Verify exact installed wheels before any registry-backed reinstall."""
    mapper = mapper_spec(mapper_root)
    enrich = enrich_spec(enrich_root)
    try:
        for consumer in (mapper, enrich):
            assert_incumbent_probe(_probe_environment(consumer), INCUMBENT_VERSION)
    except (IncumbentInstallMismatch, OSError):
        return False
    return True


def _fingerprint(
    *,
    corpus: LoadedCorpus,
    config_path: Path,
    leak_manifest_path: Path,
    salt_file_path: Path,
    public_metadata_path: Path,
    mapper_root: Path,
    enrich_root: Path,
    output_path: Path,
    limit: int,
) -> dict[str, object]:
    config = load_config(config_path)
    ontology_pin = assert_ontology_pin(corpus.manifest.ontology_cache_sha256)
    mapper = mapper_spec(mapper_root)
    enrich = enrich_spec(enrich_root)
    candidate_ignored_bytecode = {
        "eval": _assert_no_ignored_importables(FOLIO_RESOLVE_ROOT, "eval"),
        "src": _assert_no_ignored_importables(FOLIO_RESOLVE_ROOT, "src"),
    }
    mapper_ignored_bytecode = _assert_no_ignored_importables(mapper.repo_root, "backend")
    enrich_ignored_bytecode = _assert_no_ignored_importables(enrich.repo_root, "backend")
    candidate_environment = _consumer_environment_fingerprint(Path(sys.executable))
    mapper_environment = _consumer_environment_fingerprint(
        mapper.venv_python,
        require_mapper_cache=True,
        require_model_assets=True,
    )
    enrich_environment = _consumer_environment_fingerprint(enrich.venv_python)
    try:
        output_relative = output_path.resolve().relative_to(FOLIO_RESOLVE_ROOT.resolve())
    except ValueError:
        allowed_candidate_outputs: tuple[Path, ...] = ()
    else:
        allowed_candidate_outputs = (
            (output_path,) if output_relative == PUBLISHED_COMPARISON_REPORT else ()
        )
    return {
        "answer_rule_config_sha256": config.content_sha256(),
        "candidate_environment": candidate_environment,
        "candidate_ignored_bytecode": candidate_ignored_bytecode,
        "candidate_repository": _git_repository_state(
            FOLIO_RESOLVE_ROOT, allowed_untracked_paths=allowed_candidate_outputs
        ),
        "corpus_content_sha256": corpus.manifest.content_sha256,
        "corpus_manifest_sha256": _sha256_file(corpus.manifest.manifest_path),
        "corpus_version": corpus.manifest.version,
        "enrich_environment": enrich_environment,
        "enrich_ignored_bytecode": enrich_ignored_bytecode,
        "enrich_lock_sha256": _sha256_file(enrich.repo_root / "backend" / "uv.lock"),
        "enrich_repository": _git_repository_state(enrich_root),
        "folio_python_lock_sha256": _sha256_file(FOLIO_RESOLVE_ROOT / "uv.lock"),
        "folio_python_version": importlib.metadata.version("folio-python"),
        "folio_resolve_version": importlib.metadata.version("folio-resolve"),
        "incumbent_version": INCUMBENT_VERSION,
        "leak_manifest_sha256": _sha256_file(leak_manifest_path),
        "mapper_environment": mapper_environment,
        "mapper_ignored_bytecode": mapper_ignored_bytecode,
        "mapper_lock_sha256": _sha256_file(mapper.repo_root / "backend" / "uv.lock"),
        "mapper_repository": _git_repository_state(mapper_root),
        "nomatch_content_sha256": corpus.manifest.nomatch_content_sha256,
        "ontology_cache_sha256": ontology_pin.sha256,
        "public_metadata_sha256": _sha256_file(public_metadata_path),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED", ""),
        "python_optimize": sys.flags.optimize,
        "python_version": platform.python_version(),
        "runtime_environment": {
            **_OFFLINE_RUNTIME_OVERRIDES,
            "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", ""),
        },
        "salt_file_sha256": _sha256_file(salt_file_path),
        "scoreable_limit": limit,
    }


def _fingerprint_for_args(args: argparse.Namespace, corpus: LoadedCorpus) -> dict[str, object]:
    return _fingerprint(
        corpus=corpus,
        config_path=args.config,
        leak_manifest_path=args.leak_manifest,
        salt_file_path=args.salt_file,
        public_metadata_path=args.public_metadata,
        mapper_root=args.mapper_root,
        enrich_root=args.enrich_root,
        output_path=args.out,
        limit=args.limit,
    )


def _require_current_fingerprint(
    args: argparse.Namespace,
    corpus: LoadedCorpus,
    expected: Mapping[str, object],
    *,
    boundary: str,
) -> LoadedCorpus:
    current_corpus = load_corpus(args.corpus_manifest)
    if _fingerprint_for_args(args, current_corpus) != expected:
        raise PilotCheckpointError(f"pilot inputs drifted {boundary}")
    return current_corpus


def _checkpoint_manifest(
    *, fingerprint: Mapping[str, object], item_ids: Sequence[str]
) -> dict[str, object]:
    return {
        "expected_item_count": len(item_ids),
        "fingerprint": dict(fingerprint),
        "fingerprint_sha256": sha256_bytes(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
        ),
        "item_ids": list(item_ids),
        "kind": PILOT_CHECKPOINT_KIND,
        "schema_version": PILOT_CHECKPOINT_VERSION,
    }


def _create_or_validate_manifest(path: Path, expected: Mapping[str, object]) -> None:
    _atomic_create(path, expected)
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("pilot checkpoint manifest is corrupt") from exc
    if observed != expected:
        raise PilotCheckpointError("pilot checkpoint fingerprint does not match this run")


def _load_shard(
    path: Path,
    item_id: str,
    fingerprint: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError(f"pilot shard is corrupt: {path}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "synthetic_comparison":
        raise PilotCheckpointError(f"pilot shard has the wrong contract: {path}")
    if payload.get("run_kind") != "shard":
        raise PilotCheckpointError(f"pilot shard has the wrong run_kind: {path}")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PilotCheckpointError(f"pilot shard provenance is malformed: {path}")
    cohort = provenance.get("cohort_selection")
    if not isinstance(cohort, dict):
        raise PilotCheckpointError(f"pilot shard cohort is malformed: {path}")
    observed_ids = [
        *cohort.get("scoreable_item_ids", []),
        *cohort.get("nomatch_item_ids", []),
    ]
    if observed_ids != [item_id]:
        raise PilotCheckpointError(f"pilot shard item mismatch: {path}")
    stacks = payload.get("stacks")
    if not isinstance(stacks, dict) or set(stacks) != {
        "folio-enrich:incumbent",
        "folio-mapper:incumbent",
        "folio-resolve:candidate",
    }:
        raise PilotCheckpointError(f"pilot shard stack set is incomplete: {path}")
    for key, stack in stacks.items():
        if not isinstance(stack, dict) or set(stack.get("items", {})) != {item_id}:
            raise PilotCheckpointError(f"pilot shard {key} item rows are incomplete: {path}")
        snapshot = stack.get("stage_snapshot", {})
        if not isinstance(snapshot, dict) or set(snapshot.get("by_item", {})) != {item_id}:
            raise PilotCheckpointError(f"pilot shard {key} stages are incomplete: {path}")
    if fingerprint is not None:
        corpus = payload.get("corpus", {})
        if not isinstance(corpus, dict):
            raise PilotCheckpointError(f"pilot shard corpus is malformed: {path}")
        config_selection = provenance.get("config_selection", {})
        if not isinstance(config_selection, dict):
            raise PilotCheckpointError(f"pilot shard config selection is malformed: {path}")
        candidate_versions = stacks["folio-resolve:candidate"].get("versions", {})
        if not isinstance(candidate_versions, dict):
            raise PilotCheckpointError(f"pilot shard versions are malformed: {path}")
        checks = {
            "corpus version": (corpus.get("version"), fingerprint["corpus_version"]),
            "corpus content": (corpus.get("content_sha256"), fingerprint["corpus_content_sha256"]),
            "nomatch content": (
                corpus.get("nomatch_content_sha256"),
                fingerprint["nomatch_content_sha256"],
            ),
            "answer-rule config": (
                config_selection.get("answer_rule_config_sha256"),
                fingerprint["answer_rule_config_sha256"],
            ),
            "folio-python version": (
                payload.get("folio_python_version"),
                fingerprint["folio_python_version"],
            ),
            "folio-resolve version": (
                candidate_versions.get("folio-resolve"),
                fingerprint["folio_resolve_version"],
            ),
            "candidate repository": (
                stacks["folio-resolve:candidate"].get("repository"),
                fingerprint["candidate_repository"],
            ),
            "enrich repository": (
                stacks["folio-enrich:incumbent"].get("repository"),
                fingerprint["enrich_repository"],
            ),
            "mapper repository": (
                stacks["folio-mapper:incumbent"].get("repository"),
                fingerprint["mapper_repository"],
            ),
        }
        drifted = [name for name, (observed, expected) in checks.items() if observed != expected]
        if drifted:
            raise PilotCheckpointError(
                f"pilot shard fingerprint drifted ({', '.join(drifted)}): {path}"
            )
    return payload


def _shard_paths(root: Path, item_id: str) -> tuple[Path, Path, Path]:
    shard_root = root / "items" / _item_key(item_id)
    return (
        shard_root / "report.json",
        shard_root / "items.jsonl",
        shard_root / "stages",
    )


def _shard_completion_path(root: Path, item_id: str) -> Path:
    return root / "items" / _item_key(item_id) / "complete.json"


def _shard_completion_receipt(
    report: Path, item_id: str, fingerprint: Mapping[str, object]
) -> dict[str, object]:
    try:
        report_sha256 = _sha256_file(report)
    except OSError as exc:
        raise PilotCheckpointError(f"pilot shard report is missing: {report}") from exc
    return {
        "fingerprint_sha256": sha256_bytes(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
        ),
        "item_key": _item_key(item_id),
        "kind": SHARD_COMPLETION_KIND,
        "report_sha256": report_sha256,
        "schema_version": SHARD_COMPLETION_VERSION,
    }


def _publish_shard_completion(root: Path, item_id: str, fingerprint: Mapping[str, object]) -> None:
    report = _shard_paths(root, item_id)[0]
    path = _shard_completion_path(root, item_id)
    expected = _shard_completion_receipt(report, item_id, fingerprint)
    _atomic_create(path, expected)
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError(f"pilot shard completion is corrupt: {path}") from exc
    if observed != expected:
        raise PilotCheckpointError(f"pilot shard completion does not match its report: {path}")


def _load_completed_shard(
    root: Path, item_id: str, fingerprint: Mapping[str, object]
) -> dict[str, Any]:
    report = _shard_paths(root, item_id)[0]
    path = _shard_completion_path(root, item_id)
    if not path.is_file():
        raise PilotCheckpointError(f"pilot shard has no verified completion receipt: {path}")
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError(f"pilot shard completion is corrupt: {path}") from exc
    if observed != _shard_completion_receipt(report, item_id, fingerprint):
        raise PilotCheckpointError(f"pilot shard completion does not match its report: {path}")
    return _load_shard(report, item_id, fingerprint)


def _final_completion_path(root: Path) -> Path:
    return root / "final-complete.json"


def _final_completion_receipt(
    report: Path, fingerprint: Mapping[str, object]
) -> dict[str, object]:
    try:
        report_sha256 = _sha256_file(report)
    except OSError as exc:
        raise PilotCheckpointError(f"final pilot report is missing: {report}") from exc
    return {
        "fingerprint_sha256": sha256_bytes(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
        ),
        "kind": FINAL_COMPLETION_KIND,
        "report_sha256": report_sha256,
        "schema_version": FINAL_COMPLETION_VERSION,
    }


def _publish_final_completion(
    root: Path, report: Path, fingerprint: Mapping[str, object]
) -> None:
    path = _final_completion_path(root)
    expected = _final_completion_receipt(report, fingerprint)
    _atomic_create(path, expected)
    _load_final_completion(root, report, fingerprint)


def _load_final_completion(
    root: Path, report: Path, fingerprint: Mapping[str, object]
) -> None:
    path = _final_completion_path(root)
    if not path.is_file():
        raise PilotCheckpointError(f"pilot has no verified final completion receipt: {path}")
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError(f"pilot final completion is corrupt: {path}") from exc
    if observed != _final_completion_receipt(report, fingerprint):
        raise PilotCheckpointError("pilot final completion does not match its report")


def _run_shard(args: argparse.Namespace, item_id: str, fingerprint: Mapping[str, object]) -> None:
    report, items, stages = _shard_paths(args.checkpoint_dir, item_id)
    completion = _shard_completion_path(args.checkpoint_dir, item_id)
    if completion.exists():
        _load_completed_shard(args.checkpoint_dir, item_id, fingerprint)
        return
    if report.exists():
        report.unlink()
        fsync_directory(report.parent)
    for stack, lane in PILOT_STACK_LANES:
        _durably_create_directory(stages / stack / lane)
    command = [
        sys.executable,
        "eval/run_downstream.py",
        "run_synthetic_comparison",
        "--corpus-manifest",
        _command_path(args.corpus_manifest),
        "--config",
        _command_path(args.config),
        "--out",
        _command_path(report),
        "--items",
        _command_path(items),
        "--row-snapshot-dir",
        _command_path(stages),
        "--leak-manifest",
        _command_path(args.leak_manifest),
        "--salt-file",
        _command_path(args.salt_file),
        "--public-metadata",
        _command_path(args.public_metadata),
        "--mapper-root",
        _command_path(args.mapper_root),
        "--enrich-root",
        _command_path(args.enrich_root),
        "--incumbent-version",
        INCUMBENT_VERSION,
        "--skip-incumbent-prepare",
        "--item-id",
        item_id,
    ]
    completed = subprocess.run(
        command,
        cwd=FOLIO_RESOLVE_ROOT,
        check=False,
        env=_offline_runtime_environment(),
        stdout=subprocess.DEVNULL,
    )
    if completed.returncode:
        raise PilotCheckpointError(
            f"comparison shard failed for {item_id!r} (rc={completed.returncode})"
        )
    _durably_sync_file(report)
    _load_shard(report, item_id, fingerprint)


def _merge_stack_runs(shards: Sequence[Mapping[str, Any]]) -> list[StackRun]:
    runs: list[StackRun] = []
    for key in sorted(shards[0]["stacks"]):
        first = shards[0]["stacks"][key]
        rows: dict[str, frozenset[str]] = {}
        stages: dict[str, Mapping[str, object]] = {}
        invocation_receipts: list[dict[str, object]] = []
        for shard in shards:
            stack = shard["stacks"][key]
            for field in ("stack", "lane", "versions", "config", "repository"):
                if stack[field] != first[field]:
                    raise PilotCheckpointError(f"pilot shard {key} {field} drifted")
            for item_id, iris in stack["items"].items():
                if item_id in rows:
                    raise PilotCheckpointError(f"duplicate pilot item in {key}: {item_id}")
                rows[item_id] = frozenset(iris)
                stages[item_id] = stack["stage_snapshot"]["by_item"][item_id]
            invocation = stack.get("invocation")
            if (
                not isinstance(invocation, dict)
                or not isinstance(invocation.get("kind"), str)
                or not isinstance(invocation.get("working_directory"), str)
                or not isinstance(invocation.get("argv"), list)
                or not all(isinstance(value, str) for value in invocation["argv"])
            ):
                raise PilotCheckpointError(f"pilot shard {key} invocation is malformed")
            invocation_receipts.append(
                {
                    "argv": invocation["argv"],
                    "kind": invocation["kind"],
                    "working_directory": invocation["working_directory"],
                }
            )
        invocation_sha256 = sha256_bytes(
            json.dumps(
                invocation_receipts,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        first_invocation = invocation_receipts[0]
        first_argv = first_invocation["argv"]
        if not isinstance(first_argv, list) or not first_argv:
            raise PilotCheckpointError(f"pilot shard {key} invocation argv is empty")
        aggregate_invocation: tuple[str, ...]
        if first["lane"] == "incumbent":
            if len(first_argv) < 2 or not isinstance(first_argv[1], str):
                raise PilotCheckpointError(f"pilot shard {key} runner path is missing")
            aggregate_invocation = (
                "folio_eval.comparison_pilot.aggregate_consumer_stack",
                first_argv[1],
                "--source-shard-count",
                str(len(shards)),
                "--source-invocations-sha256",
                invocation_sha256,
            )
        else:
            aggregate_invocation = (
                "folio_eval.comparison_pilot.aggregate_local_stack",
                "--source-shard-count",
                str(len(shards)),
                "--source-invocations-sha256",
                invocation_sha256,
            )
        runs.append(
            StackRun(
                stack=first["stack"],
                lane=first["lane"],
                folio_resolve_version=first["versions"]["folio-resolve"],
                folio_python_version=first["versions"]["folio-python"],
                config=first["config"],
                rows=rows,
                stages=stages,
                invocation=aggregate_invocation,
                invocation_working_directory=str(first_invocation["working_directory"]),
                invocation_kind="equivalent_checkpoint_aggregate",
                repository=first["repository"],
            )
        )
    return runs


def _finalization_invocation(args: argparse.Namespace, combined_items: Path) -> dict[str, object]:
    """Record the exact supplied inputs used by equivalent checkpoint finalization."""
    environment = _offline_runtime_environment()
    return {
        "kind": "equivalent_checkpoint_finalization",
        "argv": [
            sys.executable,
            "eval/run_downstream.py",
            "run_synthetic_comparison",
            "--corpus-manifest",
            _command_path(args.corpus_manifest),
            "--config",
            _command_path(args.config),
            "--out",
            _command_path(args.out),
            "--items",
            _command_path(combined_items),
            "--row-snapshot-dir",
            _command_path(args.checkpoint_dir / "final-stages"),
            "--leak-manifest",
            _command_path(args.leak_manifest),
            "--salt-file",
            _command_path(args.salt_file),
            "--public-metadata",
            _command_path(args.public_metadata),
            "--mapper-root",
            _command_path(args.mapper_root),
            "--enrich-root",
            _command_path(args.enrich_root),
            "--incumbent-version",
            INCUMBENT_VERSION,
            "--skip-incumbent-prepare",
            "--limit",
            str(args.limit),
        ],
        "working_directory": str(FOLIO_RESOLVE_ROOT),
        "environment": {
            key: environment.get(key, "")
            for key in (*sorted(_OFFLINE_RUNTIME_OVERRIDES), "PYTHONHASHSEED")
        },
    }


def _finalize(
    args: argparse.Namespace,
    corpus: LoadedCorpus,
    item_ids: Sequence[str],
    manifest: Mapping[str, object],
) -> None:
    fingerprint = manifest.get("fingerprint")
    if not isinstance(fingerprint, Mapping):
        raise PilotCheckpointError("pilot checkpoint fingerprint is malformed")
    shard_payloads = [
        _load_completed_shard(
            args.checkpoint_dir,
            item_id,
            fingerprint,
        )
        for item_id in item_ids
    ]
    runs = _merge_stack_runs(shard_payloads)
    leak_manifest = load_manifest(args.leak_manifest)
    salt = args.salt_file.read_bytes()
    combined_items = args.checkpoint_dir / "final-items.jsonl"
    emit_items_file(
        corpus,
        combined_items,
        limit=args.limit,
        include_nomatch=True,
        leak_manifest=leak_manifest,
        salt=salt,
    )
    final_stages = args.checkpoint_dir / "final-stages"
    for run in runs:
        _durably_create_directory(final_stages / run.stack / run.lane)
    snapshot_files = write_stage_snapshots(
        runs,
        final_stages,
        leak_manifest=leak_manifest,
        salt=salt,
    )
    comparison_invocation = _finalization_invocation(args, combined_items)
    payload = build_comparison(
        corpus,
        runs,
        load_config(args.config),
        limit=args.limit,
        include_nomatch=True,
        comparison_invocation=comparison_invocation,
        stage_snapshot_files=snapshot_files,
        items_file=_file_fingerprint(combined_items, root=FOLIO_RESOLVE_ROOT),
    )
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PilotCheckpointError("final comparison provenance is malformed")
    provenance["checkpoint"] = {
        "completed_shards": len(shard_payloads),
        "manifest_sha256": _sha256_file(args.checkpoint_dir / "manifest.json"),
        "shard_report_sha256": {
            _item_key(item_id): _sha256_file(_shard_paths(args.checkpoint_dir, item_id)[0])
            for item_id in item_ids
        },
    }
    public_metadata = _load_bound_public_metadata(args.public_metadata, fingerprint)
    _require_current_fingerprint(
        args, corpus, fingerprint, boundary="before final report publication"
    )
    _durably_create_directory(args.out.parent)
    write_comparison(
        args.out,
        payload,
        leak_manifest,
        salt,
        public_metadata=public_metadata,
        temporary_dir=args.out.parent,
        temporary_prefix=f".{args.out.name}.",
    )
    _publish_final_completion(args.checkpoint_dir, args.out, fingerprint)


def _load_bound_public_metadata(
    path: Path, fingerprint: Mapping[str, object]
) -> PublicComparisonMetadata:
    """Load one metadata snapshot and prove those exact bytes belong to the run."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise PilotCheckpointError(f"comparison public metadata is unavailable: {path}") from exc
    if sha256_bytes(content) != fingerprint.get("public_metadata_sha256"):
        raise PilotCheckpointError("comparison public metadata drifted before finalization")
    return load_public_comparison_metadata(path, content=content)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--public-metadata", type=Path, required=True)
    parser.add_argument("--mapper-root", type=Path, required=True)
    parser.add_argument("--enrich-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--max-new-items", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise PilotCheckpointError("comparison pilot requires PYTHONHASHSEED=0")
    _assert_unoptimized_runtime()
    _assert_clean_runtime_environment()
    _resolve_path_arguments(args)
    corpus = load_corpus(args.corpus_manifest)
    _assert_write_paths_are_safe(args, corpus)
    item_ids = _pilot_ids(corpus, args.limit)
    _assert_existing_canonical_report_is_recoverable(args, item_ids)
    _prepare_incumbents_for_new_checkpoint(args)
    fingerprint = _fingerprint_for_args(args, corpus)
    manifest = _checkpoint_manifest(fingerprint=fingerprint, item_ids=item_ids)
    _durably_create_directory(args.checkpoint_dir)
    _create_or_validate_manifest(args.checkpoint_dir / "manifest.json", manifest)
    final_completion = _final_completion_path(args.checkpoint_dir)
    if final_completion.exists():
        _load_final_completion(args.checkpoint_dir, args.out, fingerprint)
        print(f"pilot checkpoint: {len(item_ids)}/{len(item_ids)} complete")
        print(f"pilot report: {args.out}")
        return 0
    if not args.finalize_only:
        completed_before = sum(
            _shard_completion_path(args.checkpoint_dir, item_id).exists() for item_id in item_ids
        )
        allowance = args.max_new_items
        for item_id in item_ids:
            completion = _shard_completion_path(args.checkpoint_dir, item_id)
            if completion.exists():
                _load_completed_shard(args.checkpoint_dir, item_id, fingerprint)
                continue
            if allowance is not None and allowance <= 0:
                break
            ordinal = (
                sum(
                    _shard_completion_path(args.checkpoint_dir, candidate_id).exists()
                    for candidate_id in item_ids
                )
                + 1
            )
            _require_current_fingerprint(
                args, corpus, fingerprint, boundary=f"before shard {ordinal}"
            )
            print(f"pilot shard {ordinal}/{len(item_ids)}: starting", flush=True)
            _run_shard(args, item_id, fingerprint)
            _require_current_fingerprint(
                args, corpus, fingerprint, boundary=f"after shard {ordinal}"
            )
            _publish_shard_completion(args.checkpoint_dir, item_id, fingerprint)
            print(f"pilot shard {ordinal}/{len(item_ids)}: complete", flush=True)
            if allowance is not None:
                allowance -= 1
        completed_after = sum(
            _shard_completion_path(args.checkpoint_dir, item_id).exists() for item_id in item_ids
        )
        print(f"pilot checkpoint: {completed_after}/{len(item_ids)} complete")
        if completed_after < len(item_ids):
            if args.max_new_items == 0:
                return 0
            if completed_after == completed_before:
                raise PilotCheckpointError("pilot checkpoint made no progress")
            return 0
    corpus = _require_current_fingerprint(
        args, corpus, fingerprint, boundary="before finalization"
    )
    _finalize(args, corpus, item_ids, manifest)
    print(f"pilot report: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
