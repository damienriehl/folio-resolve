"""Local ceiling behavior without model dependencies or network access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from folio_eval import recall_embedding_ceiling as ceiling
from folio_eval.leakcheck import build_manifest

from folio_resolve.embedding import HashingEmbeddingProvider
from folio_resolve.ontology import Concept


def token_count(text: str) -> int:
    return len(text.split()) + 2


def attribution(*rows: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "relations": [{"item_id": item, "iri": iri, "stage": stage} for item, iri, stage in rows],
    }


def four_concepts() -> list[Concept]:
    return [
        Concept("gold", "Unrelated label", "zebra sanctuary conservation"),
        Concept("known", "boats"),
        Concept("other", "apples"),
        Concept("last", "houses"),
    ]


def test_definition_recovery_counts_all_gold_and_numeric_output() -> None:
    result = ceiling.measure_ceiling(
        four_concepts(),
        {"p": "zebra sanctuary conservation"},
        attribution(("p", "gold", "never_produced"), ("p", "known", "top_100")),
        HashingEmbeddingProvider(),
        token_count,
    )
    assert result["ontology_class_count"] == 4
    assert result["never_produced_count"] == 1
    for arm in ("whole_passage", "sentence_windows"):
        curve = result["passages"]["p"][arm]
        assert curve["10"] == {"recovered_count": 1, "non_gold_count": 2, "suggestion_count": 4}
        assert result["rankings"][arm]["50"]["recovered_count"] == 1
    assert result["r9"]["qualifies"] is True
    serialized = json.dumps(result)
    assert "zebra" not in serialized and "Unrelated label" not in serialized


def test_last_sentence_recovers_when_whole_passage_misses_and_depths_monotonic() -> None:
    concepts = [Concept(f"d{i:03}", "boats harbor", "boats " * (i + 1)) for i in range(110)]
    concepts.append(Concept("gold", "zebra sanctuary conservation"))
    text = "boats " * 300 + ". zebra sanctuary conservation."
    result = ceiling.measure_ceiling(
        concepts,
        {"p": text},
        attribution(("p", "gold", "never_produced")),
        HashingEmbeddingProvider(),
        token_count,
    )
    assert result["rankings"]["whole_passage"]["50"]["recovered_count"] == 0
    assert result["rankings"]["sentence_windows"]["10"]["recovered_count"] == 1
    assert result["r9"]["qualifies"] is True
    assert result["r9"]["best_recovered_at_50"] == 1
    for arm in ("whole_passage", "sentence_windows"):
        for key in ("recovered_count", "non_gold_count", "suggestion_count"):
            counts = [result["rankings"][arm][str(k)][key] for k in (10, 25, 50, 100)]
            assert counts == sorted(counts)


def test_windows_use_wordpieces_include_tail_and_split_unbroken_text() -> None:
    # Simulate a tokenizer that splits every character; whitespace count is unsafe.
    def count(text: str) -> int:
        return len(text) + 2

    text = "a" * 600 + ". The final sentence."
    windows = ceiling.sentence_windows(text, count)
    assert all(count(window) < 256 for window in windows)
    assert "".join(windows).replace(" ", "") == text.replace(" ", "")
    assert windows[-1].endswith("The final sentence.")


@pytest.mark.parametrize("variable", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
def test_offline_missing_fails_before_loading(
    variable: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.delenv(variable)
    monkeypatch.setattr(ceiling, "LocalEmbeddingProvider", lambda *_: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="OFFLINE"):
        ceiling.load_local_provider(tmp_path)


def test_bad_model_hash_fails_before_loading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    pin = {"repo_id": "fake", "revision": "fake", "files": {"weights": "0" * 64}}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(pin))
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights").write_bytes(b"fake weights")
    monkeypatch.setattr(ceiling.baseline, "MODEL_FILES_PATH", manifest)
    monkeypatch.setattr(ceiling, "LocalEmbeddingProvider", lambda *_: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="SHA-256"):
        ceiling.load_local_provider(model)


def test_attribution_digest_binding(tmp_path: Path) -> None:
    path = tmp_path / "attribution.json"
    payload = json.dumps(attribution(("p", "gold", "never_produced"))).encode()
    path.write_bytes(payload)
    assert ceiling.load_attribution(path, hashlib.sha256(payload).hexdigest())["relations"]
    with pytest.raises(ValueError, match="SHA-256"):
        ceiling.load_attribution(path, "0" * 64)


def test_empty_denominator_does_not_qualify() -> None:
    result = ceiling.measure_ceiling(
        four_concepts(), {}, attribution(), HashingEmbeddingProvider(), token_count
    )
    assert result["passages"] == {}
    assert result["r9"]["qualifies"] is False
    assert result["r9"]["best_recovery_fraction_at_50"] == 0


@pytest.mark.parametrize(
    "rows,passages",
    [
        ([("p", "missing", "never_produced")], {"p": "passage"}),
        ([("p", "gold", "never_produced")], {}),
        ([("p", "gold", "never_produced"), ("p", "gold", "never_produced")], {"p": "passage"}),
    ],
)
def test_invalid_relations_fail(rows: list[tuple[str, str, str]], passages: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ceiling.measure_ceiling(
            four_concepts(), passages, attribution(*rows), HashingEmbeddingProvider(), token_count
        )


def test_leak_scan_before_write_preserves_existing_output(tmp_path: Path) -> None:
    salt = b"fake-test-salt"
    manifest = build_manifest(
        ["forbidden private phrase"], salt, gold_version="fake", gold_content_sha256="a" * 64
    )
    path = tmp_path / "result.json"
    path.write_text("prior")
    with pytest.raises(ValueError, match="leak"):
        ceiling.write_report(path, {"passages": {"forbidden private phrase": 1}}, manifest, salt)
    assert path.read_text() == "prior"
    ceiling.write_report(path, {"count": 1}, manifest, salt)
    assert json.loads(path.read_text()) == {"count": 1}


@pytest.mark.parametrize("gold_count,qualifies", [(4, True), (5, False)])
def test_r9_exact_quarter_boundary(gold_count: int, qualifies: bool) -> None:
    concepts = [Concept(f"d{i:03}", "boats harbor") for i in range(110)]
    concepts += [
        Concept("gold", "boats"),
        *[Concept(f"z{i}", "unrelated") for i in range(gold_count - 1)],
    ]
    rows = [
        ("p", "gold", "never_produced"),
        *[("p", f"z{i}", "never_produced") for i in range(gold_count - 1)],
    ]
    result = ceiling.measure_ceiling(
        concepts, {"p": "boats"}, attribution(*rows), HashingEmbeddingProvider(), token_count
    )
    assert result["r9"]["best_recovered_at_50"] == 1
    assert result["r9"]["qualifies"] is qualifies


@pytest.mark.parametrize("ontology_case", ["exact", "extra", "missing", "pin_mismatch"])
def test_main_binds_inputs_and_writes_numeric_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ontology_case: str
) -> None:
    from types import SimpleNamespace

    corpus = SimpleNamespace(
        manifest=SimpleNamespace(
            content_sha256="a" * 64, ontology_cache_sha256="b" * 64, scoreable=True
        ),
        scoreable_items=[SimpleNamespace(item_id="p", text="zebra sanctuary conservation")],
    )
    source = attribution(("p", "gold", "never_produced"))
    source["fingerprint"] = {"corpus_content_sha256": "a" * 64, "ontology_cache_sha256": "b" * 64}
    path = tmp_path / "attribution.json"
    path.write_text(json.dumps(source))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    salt = tmp_path / "salt"
    salt.write_bytes(b"fake-salt")
    manifest = build_manifest(
        ["forbidden private phrase"],
        b"fake-salt",
        gold_version="fake",
        gold_content_sha256="c" * 64,
    )
    monkeypatch.setattr(ceiling, "load_corpus", lambda _: corpus)
    monkeypatch.setattr(
        ceiling, "assert_ontology_pin", lambda _: SimpleNamespace(path=tmp_path, sha256="b" * 64)
    )
    monkeypatch.setattr(
        ceiling,
        "load_folio_index",
        lambda: (
            SimpleNamespace(iris={c.iri for c in four_concepts()}),
            ("e" if ontology_case == "pin_mismatch" else "b") * 64,
            "fake",
        ),
    )
    concepts = four_concepts()
    if ontology_case == "extra":
        concepts.append(Concept("https://example.org/ontology#extra", "Excluded concept"))
    elif ontology_case == "missing":
        # Missing even a non-gold dictionary IRI must prevent evaluation.
        concepts.pop()
    monkeypatch.setattr(ceiling.baseline, "load_corpus", lambda *_: concepts)
    monkeypatch.setattr(ceiling, "load_manifest", lambda _: manifest)
    monkeypatch.setattr(
        ceiling, "load_local_provider", lambda _: (HashingEmbeddingProvider(), token_count)
    )
    output = tmp_path / "output.json"
    args = [
        "--attribution",
        str(path),
        "--attribution-sha256",
        digest,
        "--corpus-manifest",
        str(tmp_path / "corpus.json"),
        "--model-path",
        str(tmp_path / "model"),
        "--leak-manifest",
        str(tmp_path / "leaks.json"),
        "--salt-file",
        str(salt),
        "--output",
        str(output),
    ]
    if ontology_case in {"missing", "pin_mismatch"}:
        output.write_text("prior output")
        monkeypatch.setattr(ceiling, "load_local_provider", lambda _: pytest.fail("model loaded"))
        with pytest.raises(ValueError, match="embedding ontology differs from eval ontology"):
            ceiling.main(args)
        assert output.read_text() == "prior output"
        return
    assert ceiling.main(args) == 0
    report = json.loads(output.read_text())
    assert report["attribution_sha256"] == digest
    assert report["never_produced_count"] == 1
    assert report["ontology_sha256"] == "b" * 64
    assert report["ontology_class_count"] == 4
    assert report["excluded_concept_count"] == (1 if ontology_case == "extra" else 0)
    for arm in ceiling.ARMS:
        assert report["rankings"][arm]["100"]["suggestion_count"] == 4
    assert "https://example.org/ontology#extra" not in output.read_text()
    assert "Excluded concept" not in output.read_text()
    assert "zebra sanctuary conservation" not in output.read_text()
    output.unlink()
    corpus.manifest.content_sha256 = "d" * 64
    monkeypatch.setattr(ceiling, "load_local_provider", lambda _: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="fingerprint"):
        ceiling.main(args)
    assert not output.exists()


def test_review_heading_collision_before_model_or_corpus(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("compute started before fixed prose check")

    monkeypatch.setattr(ceiling, "load_corpus", forbidden)
    monkeypatch.setattr(ceiling, "load_local_provider", forbidden)
    monkeypatch.setattr(
        ceiling,
        "load_manifest",
        lambda _: build_manifest(
            ["Distance past rank 200"],
            b"fake-salt",
            gold_version="fake",
            gold_content_sha256="a" * 64,
        ),
    )
    salt = tmp_path / "salt"
    salt.write_bytes(b"fake-salt")
    with pytest.raises(ValueError, match="leak check"):
        ceiling.main(
            [
                "--attribution",
                "unused",
                "--attribution-sha256",
                "unused",
                "--corpus-manifest",
                "unused",
                "--model-path",
                "unused",
                "--leak-manifest",
                "unused",
                "--salt-file",
                str(salt),
                "--output",
                "unused",
            ]
        )
