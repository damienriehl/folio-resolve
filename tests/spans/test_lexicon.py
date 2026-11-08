import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from folio_resolve.spans.lexicon import (
    PINNED_SHA256,
    build_lexicon,
    load_lexicon,
    verify_lexicon,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEADWORDS = (
    "good faith",
    "good",
    "faith",
    "breach",
    "breach of contract",
    "contract",
    "tenant",
    "property",
    "real property",
    "tax",
    "appeal",
    "the",
    "of",
    "fully indexed",
    "rate",
    "this",
    "and",
    "with",
    "they",
    "three",
    "will",
)


@pytest.fixture
def tiny_parquet(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "tiny.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    sense_id=f"good faith:noun:{i}",
                    headword="good faith",
                    pos="noun",
                    sense_index=i,
                    domain="law",
                    tier="tier1",
                    source="opengloss",
                    gloss=f"sense {i}",
                    heavy_nested=[{"omit": i}],
                )
                for i in (1, 0)
            ]
        ),
        path,
    )
    return path, {"data/tiny.parquet": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_tiny_builder_reproducible_sorted_and_projected(tiny_parquet, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    path, pins = tiny_parquet
    original = pq.ParquetFile
    columns_read = []

    class ProjectedFile:
        def __init__(self, *args, **kwargs):
            self.file = original(*args, **kwargs)

        def iter_batches(self, *args, **kwargs):
            columns_read.append(kwargs["columns"])
            return self.file.iter_batches(*args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", ProjectedFile)
    outputs = []
    for suffix in ("a", "b"):
        artifact, manifest = tmp_path / f"{suffix}.gz", tmp_path / f"{suffix}.json"
        build_lexicon([path], artifact, manifest, expected_sha256=pins, headwords=["good faith"])
        outputs.append((artifact.read_bytes(), manifest.read_bytes()))
    assert outputs[0] == outputs[1]
    assert all(
        set(cols)
        == {"sense_id", "headword", "pos", "sense_index", "domain", "tier", "source", "gloss"}
        for cols in columns_read
    )
    assert [
        row["sense_index"] for row in map(json.loads, gzip.decompress(outputs[0][0]).splitlines())
    ] == [0, 1]
    assert outputs[0][0][4:8] == b"\0\0\0\0"


def test_wrong_source_hash_fails_before_any_row_is_read(tiny_parquet, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    path, _ = tiny_parquet
    monkeypatch.setattr(pq, "ParquetFile", lambda *args, **kwargs: pytest.fail("read parquet"))
    with pytest.raises(ValueError, match=r"tiny\.parquet"):
        build_lexicon(
            [path],
            tmp_path / "x.gz",
            tmp_path / "x.json",
            expected_sha256={"data/tiny.parquet": "0" * 64},
        )


def test_fixture_content_hash_and_recompression(tmp_path):
    artifact = FIXTURES / "opengloss-mini.jsonl.gz"
    manifest = FIXTURES / "opengloss-mini.manifest.json"
    expected = json.loads(manifest.read_text())["content_sha256"]
    content = gzip.decompress(artifact.read_bytes())
    assert hashlib.sha256(content).hexdigest() == expected
    recompressed = tmp_path / "again.gz"
    recompressed.write_bytes(gzip.compress(content, compresslevel=1, mtime=0))
    assert verify_lexicon(recompressed, manifest).content_sha256 == expected
    modified = content.replace(b"good faith", b"bad faith!")
    recompressed.write_bytes(gzip.compress(modified, mtime=0))
    actual = hashlib.sha256(modified).hexdigest()
    with pytest.raises(ValueError) as exc:
        load_lexicon(recompressed, manifest)
    assert expected in str(exc.value) and actual in str(exc.value)


def test_fixture_contains_all_senses_and_required_headwords():
    lexicon = load_lexicon(
        FIXTURES / "opengloss-mini.jsonl.gz", FIXTURES / "opengloss-mini.manifest.json"
    )
    senses = lexicon.senses_for("Good faith")
    assert len(senses) > 1
    assert [s.sense_index for s in senses] == sorted(s.sense_index for s in senses)
    assert all(s.source and s.tier for s in senses)
    for word in HEADWORDS[:12]:
        assert lexicon.senses_for(word), word
    assert lexicon.manifest.source_sha256 == PINNED_SHA256


def test_no_pyarrow_runtime_subprocess():
    script = """
import sys
sys.modules['pyarrow'] = None
import folio_resolve
import folio_resolve.spans
from folio_resolve.spans.lexicon import load_lexicon, build_lexicon
from pathlib import Path
p = Path(sys.argv[1])
lexicon = load_lexicon(p / 'opengloss-mini.jsonl.gz', p / 'opengloss-mini.manifest.json')
assert lexicon.senses_for('good faith')
try:
    build_lexicon([], p / 'unused.gz', p / 'unused.json')
except ModuleNotFoundError as exc:
    assert 'folio-resolve[opengloss]' in str(exc)
else:
    raise AssertionError('builder did not require pyarrow')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(FIXTURES)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_cli_build_and_verify(tiny_parquet, tmp_path):
    path, pins = tiny_parquet
    pins_file = tmp_path / "pins.json"
    pins_file.write_text(json.dumps(pins))
    artifact, manifest = tmp_path / "cli.gz", tmp_path / "cli.json"
    for args in (
        [
            "build",
            str(path),
            "--artifact",
            str(artifact),
            "--manifest",
            str(manifest),
            "--pins",
            str(pins_file),
            "--headword",
            "good faith",
        ],
        ["verify", str(artifact), str(manifest)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "folio_resolve.spans.lexicon", *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    assert load_lexicon(artifact, manifest).senses_for("good faith")


def test_real_parquet_fixture_build_reproducible(tmp_path):
    root = Path(
        os.environ.get(
            "OPENGLOSS_PARQUET_DIR", "~/.local/share/lexplicit/artifacts/opengloss/v2.4-c64e480"
        )
    ).expanduser()
    paths = [root / Path(name).name for name in PINNED_SHA256]
    if not all(path.is_file() and os.access(path, os.R_OK) for path in paths):
        pytest.skip("pinned OpenGloss parquet files unavailable")
    builds = []
    for suffix in ("a", "b"):
        artifact, manifest = tmp_path / f"{suffix}.gz", tmp_path / f"{suffix}.json"
        build_lexicon(paths, artifact, manifest, headwords=HEADWORDS)
        builds.append((artifact.read_bytes(), manifest.read_bytes()))
    assert builds[0] == builds[1]
    assert builds[0] == (
        (FIXTURES / "opengloss-mini.jsonl.gz").read_bytes(),
        (FIXTURES / "opengloss-mini.manifest.json").read_bytes(),
    )
