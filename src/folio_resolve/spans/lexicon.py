"""Reproducible OpenGloss compiler and stdlib-only verified artifact loader.

Build with ``python -m folio_resolve.spans.lexicon build <parquet paths>
--artifact lexicon.jsonl.gz --manifest lexicon.manifest.json``. Only this build
command needs the ``opengloss`` extra. No parquet or network access occurs at tag time.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from .function_words import FUNCTION_WORDS
from .tokenize import normalize

DATASET_ID = "mjbommar/opengloss-v2.4-senses"
REVISION = "c64e480015b8c84f9d08469d4dfd64a7a8fb89bd"
PINNED_SHA256 = {
    "data/train-00000.parquet": "32f6c69d143082cc23f8a98e7c875a20600cf9ec4de4ca6fd25cb4807c27f719",
    "data/train-00001.parquet": "bad7ae8f582bec44e97710f94df1dc81869613f6649e376b9f5feefbac2501e1",
    "data/train-00002.parquet": "ddfd0bd844634b605e52189ae8972c2972266772fd9fd1a306e1115f21ab5dec",
}
COLUMNS = ["sense_id", "headword", "pos", "sense_index", "domain", "tier", "source", "gloss"]
FUNCTION_POS = frozenset(
    {
        "determiner",
        "pronoun",
        "preposition",
        "conjunction",
        "interjection",
        "numeral",
    }
)


@dataclass(frozen=True)
class Sense:
    sense_id: str
    headword: str
    pos: str
    sense_index: int
    domain: str | None
    tier: str
    source: str
    gloss: str


class LexiconManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str
    revision: str
    source_sha256: dict[str, str]
    row_count: int
    content_sha256: str
    headwords: list[str] | None = None


@dataclass(frozen=True)
class OpenGlossLexicon:
    manifest: LexiconManifest
    by_headword: Mapping[tuple[str, ...], tuple[Sense, ...]]

    def senses_for(self, headword: str) -> tuple[Sense, ...]:
        return self.by_headword.get(normalize(headword), ())

    def is_content_word(self, word: str) -> bool:
        senses = self.by_headword.get((word,), ())
        if not senses:
            return word not in FUNCTION_WORDS
        return any(sense.pos.casefold() not in FUNCTION_POS for sense in senses)


def _read_verified(artifact: str | Path, manifest: str | Path) -> tuple[bytes, LexiconManifest]:
    metadata = LexiconManifest.model_validate_json(Path(manifest).read_bytes())
    content = gzip.decompress(Path(artifact).read_bytes())
    actual = hashlib.sha256(content).hexdigest()
    if actual != metadata.content_sha256:
        raise ValueError(
            f"Lexicon digest mismatch for {Path(artifact).name}: "
            f"expected {metadata.content_sha256}, actual {actual}"
        )
    return content, metadata


def verify_lexicon(artifact: str | Path, manifest: str | Path) -> LexiconManifest:
    """Verify the uncompressed content, independent of gzip encoding."""
    return _read_verified(artifact, manifest)[1]


def load_lexicon(artifact: str | Path, manifest: str | Path) -> OpenGlossLexicon:
    content, metadata = _read_verified(artifact, manifest)
    grouped: dict[tuple[str, ...], list[Sense]] = {}
    count = 0
    for line in content.splitlines():
        sense = Sense(**json.loads(line))
        grouped.setdefault(normalize(sense.headword), []).append(sense)
        count += 1
    if count != metadata.row_count:
        raise ValueError(
            f"Lexicon row count mismatch: expected {metadata.row_count}, actual {count}"
        )
    return OpenGlossLexicon(
        metadata,
        MappingProxyType(
            {
                key: tuple(sorted(senses, key=lambda s: (s.sense_index, s.pos, s.sense_id)))
                for key, senses in grouped.items()
            }
        ),
    )


def build_lexicon(
    parquet_paths: Iterable[str | Path],
    artifact: str | Path,
    manifest: str | Path,
    *,
    expected_sha256: Mapping[str, str] | None = None,
    headwords: Iterable[str] | None = None,
    dataset_id: str = DATASET_ID,
    revision: str = REVISION,
) -> LexiconManifest:
    """Verify every source before reading any rows, project, sort, and compile.

    ``expected_sha256`` uses portable ``data/<basename>`` keys. The default is
    the pinned release; overrides allow a caller to compile another verified release.
    """
    try:
        parquet = importlib.import_module("pyarrow.parquet")
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Building an OpenGloss lexicon requires pyarrow; "
            'install the optional extra: pip install "folio-resolve[opengloss]"'
        ) from exc
    pins = PINNED_SHA256 if expected_sha256 is None else expected_sha256
    sources = {f"data/{Path(path).name}": Path(path) for path in parquet_paths}
    if set(sources) != set(pins):
        raise ValueError(f"Expected parquet files {sorted(pins)}; got {sorted(sources)}")
    hashes: dict[str, str] = {}
    for name, path in sorted(sources.items()):
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != pins[name]:
            raise ValueError(
                f"Parquet digest mismatch for {name}: expected {pins[name]}, actual {actual}"
            )
        hashes[name] = actual
    selected = None if headwords is None else sorted(set(headwords))
    allowed = None if selected is None else {normalize(word) for word in selected}
    rows: list[Sense] = []
    for path in sources.values():
        for batch in parquet.ParquetFile(path).iter_batches(columns=COLUMNS, batch_size=8192):
            rows.extend(
                Sense(**row)
                for row in batch.to_pylist()
                if allowed is None or normalize(row["headword"]) in allowed
            )
    rows.sort(key=lambda row: (row.headword.lower(), row.pos, row.sense_index, row.sense_id))
    content = "".join(
        json.dumps(asdict(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    metadata = LexiconManifest(
        dataset_id=dataset_id,
        revision=revision,
        source_sha256=hashes,
        row_count=len(rows),
        content_sha256=hashlib.sha256(content).hexdigest(),
        headwords=selected,
    )
    with (
        Path(artifact).open("wb") as output,
        gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed,
    ):
        compressed.write(content)
    Path(manifest).write_text(
        json.dumps(metadata.model_dump(), sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("parquet", nargs="+", type=Path)
    build.add_argument("--artifact", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--pins", type=Path, help="JSON mapping of data/<basename> to sha256")
    build.add_argument("--headword", action="append", dest="headwords")
    verify = commands.add_parser("verify")
    verify.add_argument("artifact", type=Path)
    verify.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        pins = None if args.pins is None else json.loads(args.pins.read_text(encoding="utf-8"))
        metadata = build_lexicon(
            args.parquet,
            args.artifact,
            args.manifest,
            expected_sha256=pins,
            headwords=args.headwords,
        )
    else:
        metadata = verify_lexicon(args.artifact, args.manifest)
    print(json.dumps(metadata.model_dump(), sort_keys=True))


if __name__ == "__main__":
    main()
