"""Hashed-surface manifest and n-gram leak checker (U4; R6, R7; KTD4).

The governing plan is
``docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md`` U4 / KTD4,
enabling R6/R7. Owners generate a salted scrypt manifest from private firm gold; workers scan
public candidate artifacts without loading firm rows. The manifest contains normalized free-text
digests only: neither raw surfaces, the salt, nor FOLIO IRIs are publishable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .clusters import surface_strings
from .normalize import is_iri_like, normalize_label
from .splits import GoldItemRecord, GoldSet, load_gold

MANIFEST_KIND = "firm-surface-manifest"
MANIFEST_VERSION = 1
NORMALIZATION_NAME = "normalize_label"
MAX_SURFACE_TOKENS = 64
MAX_DIGESTS = 100_000
SAFE_ITEM_ID_RE = re.compile(r"[A-Za-z0-9_.:-]+")

# This is intentionally only probed while the check subcommand is running. Worker clones need
# not have this private tree; absence means there is no local version binding to enforce.
_EVAL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_GOLD_MANIFEST_GLOB = _EVAL_ROOT / "data" / "gold" / "gold_v*.manifest.json"


class LeakcheckError(RuntimeError):
    """A usage, salt, or manifest integrity error that makes scanning unsafe."""


@dataclass(frozen=True, slots=True)
class ScryptParams:
    """Pinned scrypt work factors stored in the publishable manifest."""

    n: int = 2**14
    r: int = 8
    p: int = 1
    dklen: int = 32

    def validate(self) -> None:
        if self.n < 2 or self.n & (self.n - 1):
            raise LeakcheckError("scrypt n must be a power of two greater than one")
        if self.r < 1 or self.p < 1 or self.dklen < 1:
            raise LeakcheckError("scrypt r, p, and dklen must be positive")
        if self.n > 2**14 or self.r > 8 or self.p > 1 or self.dklen > 32:
            raise LeakcheckError("scrypt parameters exceed the supported leakcheck limits")

    def to_json(self) -> dict[str, int]:
        return {"n": self.n, "r": self.r, "p": self.p, "dklen": self.dklen}


DEFAULT_SCRYPT_PARAMS = ScryptParams()


@dataclass(frozen=True, slots=True)
class Manifest:
    """Publishable surface digest set and the parameters required to query it."""

    version: int
    scrypt_params: ScryptParams
    normalization: str
    min_tokens: int
    max_tokens: int
    gold_version: str
    gold_content_sha256: str
    digests: tuple[str, ...]
    kind: str = MANIFEST_KIND

    @property
    def digest_count(self) -> int:
        return len(self.digests)

    def validate(self) -> None:
        if self.kind != MANIFEST_KIND or self.version != MANIFEST_VERSION:
            raise LeakcheckError("unsupported leakcheck manifest kind or version")
        if self.normalization != NORMALIZATION_NAME:
            raise LeakcheckError(f"unsupported normalization: {self.normalization!r}")
        self.scrypt_params.validate()
        if self.digest_count:
            if self.min_tokens < 1 or self.max_tokens < self.min_tokens:
                raise LeakcheckError("invalid manifest token bounds")
        elif self.min_tokens != 0 or self.max_tokens != 0:
            raise LeakcheckError("empty manifest must have zero token bounds")
        if self.max_tokens > MAX_SURFACE_TOKENS:
            raise LeakcheckError("manifest token bounds exceed the supported limit")
        if self.digest_count > MAX_DIGESTS:
            raise LeakcheckError("manifest digest count exceeds the supported limit")
        if tuple(sorted(set(self.digests))) != self.digests:
            raise LeakcheckError("manifest digests must be sorted and unique")
        digest_length = self.scrypt_params.dklen * 2
        if any(
            len(digest) != digest_length
            or digest != digest.casefold()
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in self.digests
        ):
            raise LeakcheckError("manifest digests must be lowercase hexadecimal values")

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "scrypt_params": self.scrypt_params.to_json(),
            "normalization": self.normalization,
            "min_tokens": self.min_tokens,
            "max_tokens": self.max_tokens,
            "gold_version": self.gold_version,
            "gold_content_sha256": self.gold_content_sha256,
            "digest_count": self.digest_count,
            "digests": list(self.digests),
        }


@dataclass(frozen=True, slots=True)
class FileReport:
    """Leak result safe to print: counts and JSONL item ids, never matching text."""

    path: Path
    collision_count: int
    item_ids: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "collision_count": self.collision_count,
            "item_ids": list(self.item_ids),
        }


def canonical_json(payload: Mapping[str, object]) -> str:
    """Serialize publishable structures deterministically."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
        if mode is not None:
            os.chmod(path, mode)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
        os.chmod(path, mode)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _as_gold_set(gold: GoldSet | Iterable[GoldItemRecord]) -> GoldSet:
    if isinstance(gold, GoldSet):
        return gold
    return GoldSet(
        items=tuple(gold),
        gold_id="",
        gold_version=0,
        content_sha256="",
        ontology_cache_sha256="",
        manifest={},
    )


def _is_iri_surface(surface: str) -> bool:
    """Reject both known FOLIO forms and any absolute IRI scheme."""
    return is_iri_like(surface) or bool(urlsplit(surface).scheme)


def harvest_surfaces(gold: GoldSet | Iterable[GoldItemRecord]) -> frozenset[str]:
    """Return normalized free-text surfaces from the owner backstop's exact surface source."""
    gold_set = _as_gold_set(gold)
    forbidden_iris = {
        normalize_label(iri) for item in gold_set.items for iri in item.gold_iris if iri
    }
    normalized = (normalize_label(surface) for surface in surface_strings(gold_set))
    return frozenset(
        surface
        for surface in normalized
        if surface and surface not in forbidden_iris and not _is_iri_surface(surface)
    )


def _digest(surface: str, salt: bytes, params: ScryptParams) -> str:
    try:
        return hashlib.scrypt(
            surface.encode("utf-8"),
            salt=salt,
            n=params.n,
            r=params.r,
            p=params.p,
            dklen=params.dklen,
        ).hex()
    except (MemoryError, OverflowError, ValueError) as exc:
        raise LeakcheckError("scrypt parameters cannot be executed safely") from exc


def build_manifest(
    surfaces: Iterable[str],
    salt: bytes,
    *,
    gold_version: str,
    gold_content_sha256: str,
    scrypt_params: ScryptParams = DEFAULT_SCRYPT_PARAMS,
) -> Manifest:
    """Build a deterministic manifest from normalized, deduplicated free-text surfaces."""
    if not salt:
        raise LeakcheckError("salt must not be empty")
    scrypt_params.validate()
    normalized = frozenset(normalize_label(surface) for surface in surfaces if surface.strip())
    if any(_is_iri_surface(surface) for surface in normalized):
        raise LeakcheckError("IRI-like values are forbidden in a surface manifest")
    token_lengths = [len(surface.split()) for surface in normalized]
    manifest = Manifest(
        version=MANIFEST_VERSION,
        scrypt_params=scrypt_params,
        normalization=NORMALIZATION_NAME,
        min_tokens=min(token_lengths, default=0),
        max_tokens=max(token_lengths, default=0),
        gold_version=gold_version,
        gold_content_sha256=gold_content_sha256,
        digests=tuple(sorted(_digest(surface, salt, scrypt_params) for surface in normalized)),
    )
    manifest.validate()
    return manifest


def scan_text(text: str, manifest: Manifest, salt: bytes) -> int:
    """Count distinct matching normalized token n-grams in one text value."""
    manifest.validate()
    if not manifest.digests:
        return 0
    if not salt:
        raise LeakcheckError("salt must not be empty")
    return _scan_text(text, manifest, salt, frozenset(manifest.digests))


def _scan_text(
    text: str, manifest: Manifest, salt: bytes, digest_set: frozenset[str]
) -> int:
    tokens = normalize_label(text).split()
    collisions = 0
    for length in range(manifest.min_tokens, manifest.max_tokens + 1):
        ngrams = {
            " ".join(tokens[start : start + length])
            for start in range(0, len(tokens) - length + 1)
        }
        collisions += sum(
            _digest(ngram, salt, manifest.scrypt_params) in digest_set for ngram in ngrams
        )
    return collisions


def _string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _string_values(nested)


def scan_file(path: Path, manifest: Manifest, salt: bytes) -> FileReport:
    """Scan a JSONL object's string values or an entire plain-text/Markdown file."""
    manifest.validate()
    if not manifest.digests:
        return FileReport(path=path, collision_count=0, item_ids=())
    if not salt:
        raise LeakcheckError("salt must not be empty")
    digest_set = frozenset(manifest.digests)
    collisions = 0
    item_ids: set[str] = set()
    if path.suffix.casefold() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise LeakcheckError(f"JSONL line {line_number} is not an object: {path}")
                item_collisions = sum(
                    _scan_text(value, manifest, salt, digest_set)
                    for value in _string_values(payload)
                )
                collisions += item_collisions
                item_id = payload.get("item_id")
                if item_collisions and isinstance(item_id, str):
                    item_ids.add(
                        item_id
                        if SAFE_ITEM_ID_RE.fullmatch(item_id)
                        else f"sha256:{hashlib.sha256(item_id.encode()).hexdigest()[:12]}"
                    )
    else:
        collisions = _scan_text(path.read_text(encoding="utf-8"), manifest, salt, digest_set)
    return FileReport(path=path, collision_count=collisions, item_ids=tuple(sorted(item_ids)))


def _manifest_from_json(payload: object) -> Manifest:
    if not isinstance(payload, dict):
        raise LeakcheckError("manifest is not a JSON object")
    params_raw = payload.get("scrypt_params")
    digests_raw = payload.get("digests")
    if (
        not isinstance(params_raw, dict)
        or not isinstance(digests_raw, list)
        or not all(isinstance(value, str) for value in digests_raw)
    ):
        raise LeakcheckError("manifest parameters or digests are malformed")
    try:
        params = ScryptParams(
            n=int(params_raw["n"]),
            r=int(params_raw["r"]),
            p=int(params_raw["p"]),
            dklen=int(params_raw["dklen"]),
        )
        manifest = Manifest(
            kind=str(payload["kind"]),
            version=int(payload["version"]),
            scrypt_params=params,
            normalization=str(payload["normalization"]),
            min_tokens=int(payload["min_tokens"]),
            max_tokens=int(payload["max_tokens"]),
            gold_version=str(payload["gold_version"]),
            gold_content_sha256=str(payload["gold_content_sha256"]),
            digests=tuple(digests_raw),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LeakcheckError("manifest fields are malformed") from exc
    if int(payload.get("digest_count", -1)) != manifest.digest_count:
        raise LeakcheckError("manifest digest_count does not match digests")
    manifest.validate()
    return manifest


def load_manifest(path: Path) -> Manifest:
    return _manifest_from_json(json.loads(path.read_text(encoding="utf-8")))


def _read_or_create_salt(path: Path) -> bytes:
    try:
        salt = path.read_bytes()
        if not salt:
            raise LeakcheckError("salt file is empty")
        path.chmod(0o600)
        return salt
    except FileNotFoundError:
        pass
    salt = secrets.token_bytes(32)
    _atomic_write_bytes(path, salt, mode=0o600)
    return salt


def _read_salt(path: Path) -> bytes:
    raw = path.read_bytes()
    if not raw:
        raise LeakcheckError("salt file is empty")
    return raw


def _gold_version_name(version: object) -> str:
    text = str(version)
    return text if text.startswith("gold_v") else f"gold_v{text}"


def _local_gold_versions(paths: Iterable[Path]) -> tuple[str, ...]:
    versions: set[str] = set()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            continue
        payload = json.loads(text)
        if isinstance(payload, dict) and "gold_version" in payload:
            versions.add(_gold_version_name(payload["gold_version"]))
    return tuple(sorted(versions))


def _version_order(version: str) -> tuple[int, str]:
    match = re.fullmatch(r"gold_v(\d+)", version)
    return (int(match.group(1)), version) if match else (-1, version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or query a hashed firm-surface manifest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="owner-only: generate a surface manifest")
    generate.add_argument("--gold", type=Path, required=True)
    generate.add_argument("--salt-file", type=Path, required=True)
    generate.add_argument("--out", type=Path, required=True)
    check = subparsers.add_parser("check", help="scan candidate artifacts without firm rows")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--salt-file", type=Path, required=True)
    check.add_argument("input_file", type=Path, nargs="+")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    local_gold_manifest_paths: Iterable[Path] | None = None,
) -> int:
    """Run the owner ``generate`` or worker-safe ``check`` command."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            gold = load_gold(args.gold)
            salt = _read_or_create_salt(args.salt_file)
            manifest = build_manifest(
                harvest_surfaces(gold),
                salt,
                gold_version=_gold_version_name(gold.gold_version),
                gold_content_sha256=gold.content_sha256,
            )
            _atomic_write_text(args.out, canonical_json(manifest.to_json()))
            print(f"wrote {manifest.digest_count} digests to {args.out}")
            return 0

        manifest = load_manifest(args.manifest)
        salt = _read_salt(args.salt_file)
        if local_gold_manifest_paths is None:
            local_gold_manifest_paths = DEFAULT_LOCAL_GOLD_MANIFEST_GLOB.parent.glob(
                DEFAULT_LOCAL_GOLD_MANIFEST_GLOB.name
            )
        local_versions = _local_gold_versions(local_gold_manifest_paths)
        if local_versions and max(local_versions, key=_version_order) != manifest.gold_version:
            raise LeakcheckError(
                "manifest stale: local gold version does not match surface manifest"
            )
        reports = [scan_file(path, manifest, salt) for path in args.input_file]
        for report in reports:
            ids = ",".join(report.item_ids) if report.item_ids else "-"
            print(f"{report.path}: collisions={report.collision_count} item_ids={ids}")
        return 1 if any(report.collision_count for report in reports) else 0
    except (OSError, json.JSONDecodeError, LeakcheckError) as exc:
        print(f"leakcheck error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - use eval/run_leakcheck.py
    sys.exit(main())
