"""Synthetic-corpus contracts (U5); no private evaluation data is read here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from folio_eval.answer_rule import AnswerRuleConfig, RankedCandidate
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.resolve_labels import IndexedConcept, LabelIndex
from folio_eval.score import score_items
from folio_eval.synthesize import (
    SynthesisError,
    SyntheticItem,
    build_corpus,
    extend_corpus,
    load_corpus,
    resolve_gold,
    to_gold_item_record,
)

ONTOLOGY_SHA = "a" * 64
CREATED = "2026-08-16T00:00:00Z"
SALT = b"synthetic-test-salt"
CONFIG = AnswerRuleConfig(threshold=0.5, top_k=2, calibrated=True, rationale="test")


@pytest.fixture
def index() -> LabelIndex:
    return LabelIndex.from_concepts(
        [
            IndexedConcept("R-arbitration", ("Arbitration Rules",)),
            IndexedConcept("R-defense", ("Litigation Defense",)),
            IndexedConcept("R-canonical", ("Canonical Surface",), ("Alias Surface",)),
            IndexedConcept("R-shared-a", ("Shared Label",)),
            IndexedConcept("R-shared-b", ("Shared Label",)),
        ]
    )


@pytest.fixture
def empty_manifest():
    return build_manifest(
        ["invented forbidden surface"],
        SALT,
        gold_version="test",
        gold_content_sha256="b" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
    )


def item(
    item_id: str,
    text: str,
    *,
    labels: tuple[str, ...] = ("Arbitration Rules",),
    iris: frozenset[str] = frozenset({"R-arbitration"}),
    verification: str = "deterministic",
    provenance: dict[str, object] | None = None,
) -> SyntheticItem:
    return SyntheticItem(
        item_id=item_id,
        doc_type="brief",
        jurisdiction="test",
        text=text,
        gold_labels=labels,
        gold_iris=iris,
        verification=verification,
        provenance=provenance or {"generator": "unit-test"},
    )


def build(tmp_path: Path, rows: list[SyntheticItem], empty_manifest, **kwargs):
    return build_corpus(
        rows,
        version=kwargs.pop("version", 1),
        answer_rule_config=kwargs.pop("answer_rule_config", CONFIG),
        leak_manifest=empty_manifest,
        salt=SALT,
        out_dir=tmp_path,
        seed=17,
        ontology_cache_sha256=ONTOLOGY_SHA,
        created=CREATED,
        **kwargs,
    )


def test_empty_gold_scoreable_rejected_and_explicit_nomatch_is_routed(
    tmp_path: Path, empty_manifest
) -> None:
    accidental = item("empty", "There is no mapped concept.", labels=(), iris=frozenset())
    with pytest.raises(SynthesisError, match="scoreable row has empty gold_iris: empty"):
        build(tmp_path / "bad", [accidental], empty_manifest)

    intentional = item(
        "nomatch",
        "There is intentionally no mapped concept.",
        labels=(),
        iris=frozenset(),
        provenance={"generator": "unit-test", "no_match": True},
    )
    loaded = load_corpus(build(tmp_path / "good", [intentional], empty_manifest).manifest_path)
    assert loaded.corpus_items == ()
    assert [row.item_id for row in loaded.nomatch_items] == ["nomatch"]


def test_leakcheck_scans_provenance_mapping_keys(tmp_path: Path) -> None:
    surface = "invented firm key"
    leak_manifest = build_manifest(
        [surface],
        SALT,
        gold_version="test",
        gold_content_sha256="b" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
    )
    row = item(
        "key-leak",
        "Clean passage.",
        labels=("Label",),
        iris=frozenset({"urn:test"}),
        provenance={surface: "clean"},
    )
    with pytest.raises(SynthesisError, match="leak check failed"):
        build(tmp_path, [row], leak_manifest)


def test_ambiguous_resolution_needs_review_and_is_not_scored(
    tmp_path: Path, empty_manifest, index: LabelIndex
) -> None:
    unresolved = item("ambiguous", "A paraphrase.", labels=("Shared Label",), iris=frozenset())
    resolved = resolve_gold([unresolved], index)[0]
    assert resolved.verification == "needs_review"
    assert resolved.gold_iris == frozenset()
    assert resolved.provenance["resolution_issues"]
    loaded = load_corpus(build(tmp_path, [resolved], empty_manifest).manifest_path)
    assert loaded.corpus_items[0].verification == "needs_review"
    assert loaded.gold_item_records() == ()


def test_build_is_byte_deterministic(tmp_path: Path, empty_manifest) -> None:
    rows = [
        item("b", "Paraphrased second."),
        item("a", "Paraphrased first."),
        item(
            "none",
            "An unmatched passage.",
            labels=(),
            iris=frozenset(),
            provenance={"z": 1, "no_match": True, "a": 2},
        ),
    ]
    one = build(tmp_path / "one", rows, empty_manifest)
    two = build(tmp_path / "two", list(reversed(rows)), empty_manifest)
    assert one.corpus_path.read_bytes() == two.corpus_path.read_bytes()
    assert one.nomatch_path.read_bytes() == two.nomatch_path.read_bytes()
    assert one.manifest_path.read_bytes() == two.manifest_path.read_bytes()


def test_leak_collision_reports_counts_and_ids_only(tmp_path: Path) -> None:
    manifest = build_manifest(
        ["planted private phrase"],
        SALT,
        gold_version="test",
        gold_content_sha256="b" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
    )
    with pytest.raises(SynthesisError) as caught:
        build(tmp_path, [item("leaky-id", "Contains planted private phrase here.")], manifest)
    message = str(caught.value)
    assert message == "leak check failed: collisions=1 item_ids=leaky-id"
    assert "planted private phrase" not in message


def test_gold_set_larger_than_top_k_fails(tmp_path: Path, empty_manifest) -> None:
    oversized = item(
        "wide",
        "A broad synthetic passage.",
        labels=("A", "B", "C"),
        iris=frozenset({"R-a", "R-b", "R-c"}),
    )
    with pytest.raises(SynthesisError, match="gold_iris exceeds top_k: wide count=3 top_k=2"):
        build(tmp_path, [oversized], empty_manifest)


def test_non_lexical_fraction_and_floor(tmp_path: Path, empty_manifest) -> None:
    lexical = item("lexical", "The Arbitration Rules govern this dispute.")
    nonlexical = item(
        "nonlexical",
        "The pleading rebuts the claim.",
        labels=("Litigation Defense",),
        iris=frozenset({"R-defense"}),
    )
    manifest = build(
        tmp_path,
        [lexical, nonlexical],
        empty_manifest,
        non_lexical_floor=0.75,
    )
    assert manifest.non_lexical_fraction == 0.5
    assert manifest.scoreable is False


def test_non_lexical_fraction_uses_resolved_preferred_surface(
    tmp_path: Path, empty_manifest, index: LabelIndex
) -> None:
    alias_item = item(
        "alias",
        "The Alias Surface appears verbatim.",
        labels=("Alias Surface",),
        iris=frozenset(),
    )
    resolved = resolve_gold([alias_item], index)[0]
    manifest = build(tmp_path, [resolved], empty_manifest)
    assert resolved.provenance["resolved_labels_by_iri"] == {"R-canonical": "Canonical Surface"}
    assert manifest.non_lexical_fraction == 1.0


def test_extend_preserves_v1_and_adds_ratified_rows(tmp_path: Path, empty_manifest) -> None:
    v1 = build(tmp_path, [item("first", "First paraphrase.")], empty_manifest)
    before = {path.name: path.read_bytes() for path in (v1.corpus_path, v1.manifest_path)}
    v2 = extend_corpus(
        v1.manifest_path,
        [item("second", "Second paraphrase.")],
        answer_rule_config=CONFIG,
        leak_manifest=empty_manifest,
        salt=SALT,
        out_dir=tmp_path,
        seed=17,
        ontology_cache_sha256=ONTOLOGY_SHA,
        created=CREATED,
    )
    assert v2.version == 2
    assert {row.item_id for row in load_corpus(v2.manifest_path).corpus_items} == {
        "first",
        "second",
    }
    assert {path.name: path.read_bytes() for path in (v1.corpus_path, v1.manifest_path)} == before
    assert [row.item_id for row in load_corpus(v1.manifest_path).corpus_items] == ["first"]
    with pytest.raises(SynthesisError, match="cannot extend corpus across ontology pins"):
        extend_corpus(
            v2.manifest_path,
            [],
            answer_rule_config=CONFIG,
            leak_manifest=empty_manifest,
            salt=SALT,
            out_dir=tmp_path,
            seed=17,
            ontology_cache_sha256="c" * 64,
            created=CREATED,
        )


def test_gold_record_satisfies_score_items_contract() -> None:
    record = to_gold_item_record(item("score-me", "A paraphrased rule applies."))

    def predict(_record):
        return [
            RankedCandidate(
                iri="R-arbitration",
                label="Arbitration Rules",
                score=100.0,
                probability=1.0,
                rank=1,
            )
        ]

    run = score_items([record], predict, config=CONFIG, slice_name="synthetic")
    assert run.overall.tp == 1
    assert run.overall.f1 == 1.0
    assert json.loads(json.dumps(record.item_id)) == "score-me"
