"""U7 grader ensemble contracts; fixtures are synthetic and contain no firm data."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from folio_eval.audit import assemble_sittings
from folio_eval.grade import (
    GradeError,
    GraderVote,
    audit_report,
    audit_sample,
    fold_votes,
    load_vote_file,
)
from folio_eval.leakcheck import ScryptParams, build_manifest
from folio_eval.packet_render import write_sitting_v2
from folio_eval.resolve_labels import IndexedConcept, LabelIndex
from folio_eval.synthesize import SyntheticItem

SALT = b"u7-test-salt"


@pytest.fixture
def dictionary() -> LabelIndex:
    return LabelIndex.from_concepts(
        [
            IndexedConcept("R-a", ("Alpha",)),
            IndexedConcept("R-b", ("Beta",)),
            IndexedConcept("R-c", ("Gamma",)),
        ]
    )


def item(item_id: str = "i1", *, verification: str = "needs_review", text: str = "A passage."):
    return SyntheticItem(
        item_id=item_id,
        doc_type="brief",
        jurisdiction="test",
        text=text,
        verification=verification,
        gold_labels=("Alpha",) if verification == "human" else (),
        gold_iris=frozenset({"R-a"}) if verification == "human" else frozenset(),
        provenance={"generator_id": "gen-1", "model_family": "generator-family"},
    )


def vote(grader: int, concepts: dict[str, float], item_id: str = "i1") -> GraderVote:
    return GraderVote(item_id, f"grader-{grader}", f"family-{grader}", concepts, "gen-1")


def test_two_to_one_split_folds_agreed_core_and_queues_singleton(dictionary: LabelIndex) -> None:
    outcome = fold_votes(
        [item()],
        [vote(1, {"Alpha": 0.9}), vote(2, {"Alpha": 0.8}), vote(3, {"Beta": 0.9})],
        floor=0.6,
        dictionary=dictionary,
    )
    assert outcome.items[0].verification == "deterministic"
    assert outcome.items[0].gold_iris == frozenset({"R-a"})
    assert len(outcome.close_calls) == 1
    assert outcome.close_calls[0].disagreement_class == "singleton_concept"
    assert outcome.close_calls[0].proposed_iri == "R-b"


def test_partial_sets_fold_each_concept_with_two_above_floor_votes(
    dictionary: LabelIndex,
) -> None:
    outcome = fold_votes(
        [item()],
        [
            vote(1, {"Alpha": 0.9, "Beta": 0.8}),
            vote(2, {"Alpha": 0.7}),
            vote(3, {"Beta": 0.95}),
        ],
        floor=0.6,
        dictionary=dictionary,
    )

    assert outcome.items[0].verification == "deterministic"
    assert outcome.items[0].gold_iris == frozenset({"R-a", "R-b"})
    assert not outcome.close_calls


def test_ratified_row_is_never_regraded(dictionary: LabelIndex) -> None:
    ratified = item(verification="human")
    outcome = fold_votes(
        [ratified], [vote(1, {"Beta": 0.99}), vote(2, {"Beta": 0.99}), vote(3, {"Beta": 0.99})],
        floor=0.6, dictionary=dictionary,
    )
    assert outcome.items[0] is ratified
    assert outcome.skipped_ratified == ("i1",)
    assert not outcome.close_calls


def _vote_file(tmp_path: Path, *, concepts: dict[str, object], grader="grader-1", family="family-1"):
    path = tmp_path / "votes.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "grader_id": grader,
                "model_family": family,
                "votes": [
                    {"item_id": "i1", "generator_id_claimed": "gen-1", "concepts": concepts}
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_vote_loader_rejects_off_dictionary_label(tmp_path: Path, dictionary: LabelIndex) -> None:
    with pytest.raises(ValueError, match="off-dictionary"):
        load_vote_file(_vote_file(tmp_path, concepts={"Delta": 0.8}), [item()], dictionary=dictionary)


def test_vote_loader_rejects_matching_generator_id(
    tmp_path: Path, dictionary: LabelIndex
) -> None:
    with pytest.raises(ValueError, match="own item"):
        load_vote_file(
            _vote_file(tmp_path, concepts={"Alpha": 0.8}, grader="gen-1"),
            [item()],
            dictionary=dictionary,
        )


def test_vote_loader_allows_different_grader_in_generator_family(
    tmp_path: Path, dictionary: LabelIndex
) -> None:
    loaded = load_vote_file(
        _vote_file(
            tmp_path,
            concepts={"Alpha": 0.8},
            grader="codex-grader",
            family="generator-family",
        ),
        [item()],
        dictionary=dictionary,
    )

    assert loaded[0].grader_id == "codex-grader"


def test_sub_floor_agreement_routes_queue(dictionary: LabelIndex) -> None:
    outcome = fold_votes(
        [item()],
        [vote(1, {"Alpha": 0.5}), vote(2, {"Alpha": 0.55}), vote(3, {"Alpha": 0.59})],
        floor=0.6,
        dictionary=dictionary,
    )
    assert outcome.close_calls[0].disagreement_class == "sub_floor_confidence"


def test_missing_votes_routes_before_other_disagreements(dictionary: LabelIndex) -> None:
    outcome = fold_votes(
        [item()], [vote(1, {}), vote(2, {"Beta": 0.9})], floor=0.6, dictionary=dictionary
    )
    assert outcome.close_calls[0].disagreement_class == "missing_votes"


def test_fold_rejects_three_votes_from_same_grader(dictionary: LabelIndex) -> None:
    votes = [vote(1, {"Alpha": 0.9}), vote(2, {"Alpha": 0.9}), vote(3, {"Alpha": 0.9})]
    votes = [replace(entry, grader_id="same-grader") for entry in votes]
    with pytest.raises(GradeError, match="duplicate grader"):
        fold_votes([item()], votes, floor=0.6, dictionary=dictionary)


def test_fold_accepts_two_codex_and_one_claude_for_codex_generator(
    dictionary: LabelIndex,
) -> None:
    generated = replace(
        item(), provenance={"generator_id": "codex-generator", "model_family": "codex"}
    )
    votes = [
        GraderVote("i1", "codex-grader-1", "codex", {"Alpha": 0.9}, "codex-generator"),
        GraderVote("i1", "codex-grader-2", "codex", {"Alpha": 0.8}, "codex-generator"),
        GraderVote("i1", "claude-grader", "claude", {"Alpha": 0.7}, "codex-generator"),
    ]

    row = fold_votes([generated], votes, floor=0.6, dictionary=dictionary).items[0]

    assert row.verification == "deterministic"
    assert row.gold_iris == frozenset({"R-a"})


def test_three_empty_votes_route_empty_proposal(dictionary: LabelIndex) -> None:
    outcome = fold_votes(
        [item()], [vote(1, {}), vote(2, {}), vote(3, {})], floor=0.6, dictionary=dictionary
    )
    assert outcome.items[0].verification == "needs_review"
    assert outcome.close_calls[0].disagreement_class == "empty_proposal"


def test_three_empty_votes_confirm_no_match(dictionary: LabelIndex) -> None:
    no_match = replace(item(), provenance={**item().provenance, "no_match": True})

    outcome = fold_votes(
        [no_match], [vote(1, {}), vote(2, {}), vote(3, {})], floor=0.6, dictionary=dictionary
    )

    assert outcome.items[0].verification == "deterministic"
    assert outcome.items[0].gold_iris == frozenset()
    assert not outcome.close_calls


def test_disjoint_nonempty_proposals_remain_full_item_set_mismatch(
    dictionary: LabelIndex,
) -> None:
    outcome = fold_votes(
        [item()],
        [vote(1, {"Alpha": 0.9}), vote(2, {"Beta": 0.8}), vote(3, {"Gamma": 0.9})],
        floor=0.6,
        dictionary=dictionary,
    )

    assert outcome.items[0].verification == "needs_review"
    assert outcome.items[0].gold_iris == frozenset()
    assert len(outcome.close_calls) == 1
    assert outcome.close_calls[0].disagreement_class == "set_mismatch"
    assert outcome.close_calls[0].proposed_iri is None


def test_ambiguous_resolution_quarantines_item_despite_other_agreement() -> None:
    ambiguous_dictionary = LabelIndex.from_concepts(
        [IndexedConcept("R-a", ("Shared",)), IndexedConcept("R-b", ("Shared",))]
    )

    outcome = fold_votes(
        [item()],
        [vote(1, {"Shared": 0.9}), vote(2, {"Shared": 0.8}), vote(3, {})],
        floor=0.6,
        dictionary=ambiguous_dictionary,
    )

    assert outcome.items[0].verification == "needs_review"
    assert outcome.items[0].gold_iris == frozenset()
    assert outcome.close_calls[0].disagreement_class == "ambiguous_label"


def test_provisional_gold_provenance_carries_all_votes(dictionary: LabelIndex) -> None:
    votes = [vote(1, {"Alpha": 0.9}), vote(2, {"Alpha": 0.8}), vote(3, {"Alpha": 0.7})]
    row = fold_votes([item()], votes, floor=0.6, dictionary=dictionary).items[0]
    assert row.verification == "deterministic"
    assert row.gold_iris == frozenset({"R-a"})
    assert [entry["grader_id"] for entry in row.provenance["grader_votes"]] == [
        "grader-1", "grader-2", "grader-3"
    ]


def test_audit_sample_is_deterministic_and_report_math(dictionary: LabelIndex) -> None:
    rows = [item(f"i{number}") for number in range(8)]
    votes = [
        vote(grader, {"Alpha": 0.9}, row.item_id)
        for row in rows
        for grader in (1, 2, 3)
    ]
    outcome = fold_votes(rows, votes, floor=0.6, dictionary=dictionary)
    assert audit_sample(outcome, size=4, seed=17) == audit_sample(outcome, size=4, seed=17)
    assert audit_sample(outcome, size=4, seed=17) != audit_sample(outcome, size=4, seed=18)
    assert audit_report([False, True, False, True]) == 0.5


def _manifest(surfaces: list[str]):
    return build_manifest(
        surfaces or ["invented non-colliding surface"],
        SALT,
        gold_version="test",
        gold_content_sha256="a" * 64,
        scrypt_params=ScryptParams(n=2, r=1, p=1, dklen=8, test_params=True),
    )


def test_synthetic_sitting_leakcheck_and_gate_precedence(
    tmp_path: Path, dictionary: LabelIndex
) -> None:
    outcome = fold_votes(
        [item(text="Contains planted collision phrase.")],
        [vote(1, {"Alpha": 0.9}), vote(2, {"Beta": 0.9}), vote(3, {"Alpha": 0.9})],
        floor=0.6,
        dictionary=dictionary,
    )
    batch = assemble_sittings(outcome.close_call_packet()).batches[0]
    kwargs = {"lane": "synthetic", "leak_manifest": _manifest([]), "salt": SALT}
    with pytest.raises(ValueError, match="firm sheet"):
        write_sitting_v2(batch.packet, batch.manifest, tmp_path, firm_sheet_empty=False, **kwargs)
    with pytest.raises(ValueError, match="leak check failed"):
        write_sitting_v2(
            batch.packet,
            batch.manifest,
            tmp_path,
            lane="synthetic",
            leak_manifest=_manifest(["planted"]),
            salt=SALT,
            firm_sheet_empty=True,
        )
    assert not list(tmp_path.iterdir())


def test_synthetic_queue_batch_cap_is_25(tmp_path: Path) -> None:
    from folio_eval.audit import Packet, PacketRow

    rows = tuple(
        PacketRow(
            decision_id=f"d{i}", section="suspect", item_id=f"i{i}", firm="synthetic",
            stratum="brief", stratum_id="brief", ancestor_path=(), surface_label=f"i{i}",
            input_text="passage", slice_name="synthetic", reason_class="set_mismatch",
            suggested_action="review",
        )
        for i in range(26)
    )
    packet = Packet(rows, (), {}, None, {}, {}, {})
    manifest = replace(assemble_sittings(packet).batches[0].manifest, batch_size=26)
    with pytest.raises(ValueError, match="capped at 25"):
        write_sitting_v2(
            packet, manifest, tmp_path, lane="synthetic", leak_manifest=_manifest([]), salt=SALT,
            firm_sheet_empty=True,
        )


def test_synthetic_sitting_derives_firm_sheet_nonempty_from_rows(tmp_path: Path) -> None:
    from folio_eval.audit import Packet, PacketRow

    row = PacketRow(
        decision_id="d1", section="suspect", item_id="i1", firm="firm1", stratum="brief",
        stratum_id="brief", ancestor_path=(), surface_label="i1", input_text="passage",
        slice_name="synthetic", reason_class="set_mismatch", suggested_action="review",
    )
    packet = Packet((row,), (), {}, None, {}, {}, {})
    manifest = assemble_sittings(packet).batches[0].manifest
    with pytest.raises(ValueError, match="only synthetic rows"):
        write_sitting_v2(
            packet, manifest, tmp_path, lane="synthetic", leak_manifest=_manifest([]), salt=SALT,
            firm_sheet_empty=True,
        )
