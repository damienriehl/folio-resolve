"""Proposed gold improvements (section F pilot): molecules decomposed into atom tags.

Synthetic only — a hand-built label index and a hand-built class hierarchy stand in for FOLIO, so
the branch walk, the anchor table, the noun-phrase extraction, and every filter run offline.
"""

from __future__ import annotations

from folio_eval.improve import (
    ATOM_BRANCHES,
    AtomProposal,
    anchor_atoms,
    build_branch_index,
    noun_phrases,
    propose_atoms,
)
from folio_eval.resolve_labels import IndexedConcept, LabelIndex

FOLIO = "https://folio.openlegalstandard.org/"

# A miniature ontology shaped like FOLIO's: a handful of roots, one level of children.
ROOT_PLAYER = f"{FOLIO}RSYNrootPlayer000000001"
ROOT_ASSET = f"{FOLIO}RSYNrootAsset0000000002"
ROOT_INDUSTRY = f"{FOLIO}RSYNrootIndustry0000003"
ROOT_LOCATION = f"{FOLIO}RSYNrootLocation0000004"

SHIP = f"{FOLIO}RSYNship00000000000005"
BANK = f"{FOLIO}RSYNbank00000000000006"
LAWYER = f"{FOLIO}RSYNlawyer0000000000007"
SHIPPING_INDUSTRY = f"{FOLIO}RSYNshipind000000000008"
SHIPTON = f"{FOLIO}RSYNshipton000000000009"

PARENTS: dict[str, list[str]] = {
    ROOT_PLAYER: [],
    ROOT_ASSET: [],
    ROOT_INDUSTRY: [],
    ROOT_LOCATION: [],
    SHIP: [ROOT_ASSET],
    BANK: [ROOT_PLAYER],
    LAWYER: [ROOT_PLAYER],
    SHIPPING_INDUSTRY: [ROOT_INDUSTRY],
    SHIPTON: [ROOT_LOCATION],
}

LABELS: dict[str, str] = {
    ROOT_PLAYER: "Actor / Player",
    ROOT_ASSET: "Asset Type",
    ROOT_INDUSTRY: "Industry and Market",
    ROOT_LOCATION: "Location",
    SHIP: "Ship",
    BANK: "Bank",
    LAWYER: "Lawyer",
    SHIPPING_INDUSTRY: "Transportation and Logistics Industry",
    SHIPTON: "Shipton",
}


def index() -> LabelIndex:
    return LabelIndex.from_concepts(
        IndexedConcept(iri=iri, preferred_labels=(label,)) for iri, label in LABELS.items()
    )


def branches() -> object:
    return build_branch_index(PARENTS, LABELS)


# --------------------------------------------------------------------------------------
# Branch walk
# --------------------------------------------------------------------------------------


def test_every_concept_resolves_to_its_folio_top_level_branch() -> None:
    b = build_branch_index(PARENTS, LABELS)
    assert b.branch_of(SHIP) == "Asset Type"
    assert b.branch_of(BANK) == "Actor / Player"
    assert b.branch_of(SHIPPING_INDUSTRY) == "Industry and Market"
    assert b.branch_of(SHIPTON) == "Location"


def test_only_atom_branches_can_carry_a_proposal() -> None:
    b = build_branch_index(PARENTS, LABELS)
    assert b.is_atom(SHIP) and b.is_atom(BANK) and b.is_atom(SHIPPING_INDUSTRY)
    # a place is not an atom of a practice label — this is the Slovenia-shaped failure class
    assert not b.is_atom(SHIPTON)
    assert "Location" not in ATOM_BRANCHES


def test_a_cycle_in_the_hierarchy_stops_the_walk_instead_of_hanging() -> None:
    a, z = f"{FOLIO}Ra", f"{FOLIO}Rz"
    b = build_branch_index({a: [z], z: [a]}, {a: "A", z: "Z"})
    assert b.branch_of(a) in {"A", "Z"}


# --------------------------------------------------------------------------------------
# Noun phrases
# --------------------------------------------------------------------------------------


def test_noun_phrases_drop_parentheticals_and_lead_with_the_longest_phrase() -> None:
    phrases = noun_phrases("Widget assembly finance (non-standard)")
    assert phrases[0] == "Widget assembly finance"
    assert "Widget assembly" in phrases
    assert "assembly" in phrases
    assert not any("standard" in phrase for phrase in phrases)


def test_noun_phrases_split_slash_compounds_into_their_parts() -> None:
    phrases = noun_phrases("Out of Court restructuring/workout/receivership")
    assert "workout" in phrases and "receivership" in phrases


def test_noun_phrases_drop_pure_stopword_singletons() -> None:
    assert "law" not in [phrase.casefold() for phrase in noun_phrases("Practice of Law")]


# --------------------------------------------------------------------------------------
# Anchors — Damien's six corrections, generalized
# --------------------------------------------------------------------------------------


def test_an_anchor_fires_on_the_cells_own_word_and_resolves_through_the_index() -> None:
    fired = anchor_atoms("Ship Lending", index())
    assert (SHIP, "Ship", "ship") in fired


def test_an_anchor_naming_a_concept_the_ontology_lacks_is_dropped_quietly() -> None:
    # 'counsel' -> Lawyer is in the table; an index without Lawyer simply proposes nothing.
    thin = LabelIndex.from_concepts([IndexedConcept(iri=SHIP, preferred_labels=("Ship",))])
    assert anchor_atoms("Bank advocate counsel", thin) == []


# --------------------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------------------


def _search(query: str, limit: int = 10) -> list[tuple[str, str, float]]:
    table = {
        "ship": [(SHIP, "Ship", 100.0), (SHIPTON, "Shipton", 100.0)],
        "shipping": [(SHIPPING_INDUSTRY, "Transportation and Logistics Industry", 95.0)],
        "finance": [(SHIPTON, "Shipton", 90.0)],
    }
    return table.get(query.casefold(), [])


def test_the_pilot_proposes_the_atoms_the_cell_names_and_ranks_anchors_first() -> None:
    proposals = propose_atoms(
        "Ship Lending", index=index(), branches=branches(), search=_search, gold_iris=()
    )
    assert isinstance(proposals[0], AtomProposal)
    assert [proposal.iri for proposal in proposals[:2]] == [SHIP, SHIPPING_INDUSTRY]
    assert proposals[0].method == "anchor"
    assert proposals[0].branch == "Asset Type"


def test_a_concept_already_in_this_cells_gold_is_never_re_proposed() -> None:
    proposals = propose_atoms(
        "Ship Lending", index=index(), branches=branches(), search=_search, gold_iris=(SHIP,)
    )
    assert SHIP not in {proposal.iri for proposal in proposals}


def test_a_search_hit_outside_the_atom_branches_is_dropped() -> None:
    proposals = propose_atoms(
        "Ship", index=index(), branches=branches(), search=_search, gold_iris=()
    )
    assert SHIPTON not in {proposal.iri for proposal in proposals}


def test_a_search_hit_sharing_no_word_with_the_query_is_dropped() -> None:
    """The 'Finance -> Lao People's Democratic Republic @ 90' failure class, as a guard."""

    def wandering(query: str, limit: int = 10) -> list[tuple[str, str, float]]:
        return [(BANK, "Bank", 100.0)] if query.casefold() == "widgets" else []

    proposals = propose_atoms(
        "Widgets", index=index(), branches=branches(), search=wandering, gold_iris=()
    )
    assert proposals == ()


def test_the_per_cell_proposal_count_is_capped() -> None:
    proposals = propose_atoms(
        "Ship Lending",
        index=index(),
        branches=branches(),
        search=_search,
        gold_iris=(),
        limit=1,
    )
    assert len(proposals) == 1


def test_a_proposal_serializes_as_machine_proposed() -> None:
    proposal = propose_atoms(
        "Ship Lending", index=index(), branches=branches(), search=_search
    )[0]
    payload = proposal.to_json()
    assert payload["machine_proposed"] is True
    assert payload["iri"] == SHIP and payload["branch"] == "Asset Type"


def test_a_search_hit_that_only_buries_the_query_mid_label_is_dropped() -> None:
    """Damien's atoms were always the head of a short name, never a clause mentioning it."""

    def buried(query: str, limit: int = 10) -> list[tuple[str, str, float]]:
        return [(SHIP, "Waiver of Ship Provision Clause", 100.0)]

    assert (
        propose_atoms("Widgets", index=index(), branches=branches(), search=buried) == ()
    )


def test_two_concepts_sharing_a_label_are_shown_once() -> None:
    twin = f"{FOLIO}RSYNvessel000000000010"
    other = f"{FOLIO}RSYNvessel000000000011"
    parents = dict(PARENTS, **{twin: [ROOT_ASSET], other: [ROOT_ASSET]})
    labels = dict(LABELS, **{twin: "Vessels", other: "Vessels"})
    b = build_branch_index(parents, labels)
    idx = LabelIndex.from_concepts(
        IndexedConcept(iri=iri, preferred_labels=(label,)) for iri, label in labels.items()
    )

    def twins(query: str, limit: int = 10) -> list[tuple[str, str, float]]:
        return [(twin, "Vessels", 100.0), (other, "Vessels", 100.0)]

    proposals = propose_atoms("Vessels", index=idx, branches=b, search=twins)
    assert [proposal.label for proposal in proposals] == ["Vessels"]
