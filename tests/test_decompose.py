"""Span decomposition — conjunction splitting and shared head/tail restoration.

Ch02 finding 005 / unit ``12b5e434``: *"Proposed Findings of Fact and Conclusions of Law"* names
two sibling concepts with an elided shared head, so whole-string matching returns nothing. The
contract this module owes its callers (``LabelResolver``, ``MatchPipeline._expand``):

* the original string is always element 0, so a whole-string match can still win;
* every conjunct is emitted with the elided head/tail restored;
* the output is deduplicated and order-stable.
"""

from __future__ import annotations

import pytest

from folio_resolve import decompose

# -- the contract --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Cross-Examination",
        "Proposed Findings of Fact and Conclusions of Law",
        "Arbitration and Mediation",
        "Mergers and/or Acquisitions",
        "",
    ],
)
def test_the_original_string_is_always_first(text: str) -> None:
    assert decompose(text)[0] == text.strip()


@pytest.mark.parametrize(
    "text",
    [
        "Arbitration and Mediation",
        "Antitrust and Securities Law",
        "A and B and C",
        "Discovery; Depositions",
    ],
)
def test_output_is_deduplicated(text: str) -> None:
    parts = decompose(text)
    assert len(parts) == len(set(parts))


def test_decompose_is_idempotent_on_its_own_conjuncts() -> None:
    # Every emitted conjunct is single-headed, so re-decomposing it is a no-op.
    for part in decompose("Proposed Findings of Fact and Conclusions of Law")[1:]:
        assert decompose(part) == [part]


# -- conjunction forms ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Arbitration and Mediation", ["Arbitration", "Mediation"]),
        ("Arbitration or Mediation", ["Arbitration", "Mediation"]),
        ("Mergers and/or Acquisitions", ["Mergers", "Acquisitions"]),
        ("Discovery; Depositions", ["Discovery", "Depositions"]),
        ("Contracts, and Torts", ["Contracts", "Torts"]),
        ("Contracts, or Torts", ["Contracts", "Torts"]),
    ],
)
def test_every_supported_conjunction_splits(text: str, expected: list[str]) -> None:
    assert decompose(text) == [text, *expected]


def test_and_or_is_preferred_over_the_shorter_and() -> None:
    # Longest-alternative-first ordering matters: splitting on " and " would leave "/or".
    assert decompose("Mergers and/or Acquisitions")[1:] == ["Mergers", "Acquisitions"]


def test_multiple_conjunctions_all_split() -> None:
    assert decompose("Arbitration and Mediation and Negotiation")[1:] == [
        "Arbitration",
        "Mediation",
        "Negotiation",
    ]


def test_a_bare_comma_is_not_a_conjunction() -> None:
    # "Smith, Jones" is one name, not two concepts — only ", and" / ", or" split.
    assert decompose("Evidence, Testimony") == ["Evidence, Testimony"]


# -- non-compound input --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Cross-Examination", "Litigation Defenses", "Andorra", "Sandbox", ""],
)
def test_single_head_strings_pass_through_unchanged(text: str) -> None:
    assert decompose(text) == [text.strip()]


def test_a_conjunction_needs_its_surrounding_spaces() -> None:
    # "Andorra" / "Bandwidth" contain "and" but not " and ".
    assert decompose("Andorra Bandwidth Sandbox") == ["Andorra Bandwidth Sandbox"]


def test_input_is_stripped() -> None:
    assert decompose("  Arbitration and Mediation  ") == [
        "Arbitration and Mediation",
        "Arbitration",
        "Mediation",
    ]


def test_a_conjunction_with_nothing_on_one_side_is_not_a_split() -> None:
    assert decompose("Arbitration and") == ["Arbitration and"]
    assert decompose("and") == ["and"]


# -- shared head ---------------------------------------------------------


def test_the_12b5e434_compound_heading() -> None:
    assert decompose("Proposed Findings of Fact and Conclusions of Law") == [
        "Proposed Findings of Fact and Conclusions of Law",
        "Proposed Findings of Fact",
        "Proposed Conclusions of Law",
    ]


@pytest.mark.parametrize(
    "head",
    ["Proposed", "Draft", "Amended", "Supplemental", "Joint", "Stipulated", "Preliminary", "Final"],
)
def test_every_shared_head_modifier_is_restored(head: str) -> None:
    parts = decompose(f"{head} Interrogatories and Requests")
    assert parts[1:] == [f"{head} Interrogatories", f"{head} Requests"]


def test_the_shared_head_is_case_insensitively_detected_but_written_as_authored() -> None:
    assert decompose("PROPOSED Findings and Conclusions")[1:] == [
        "PROPOSED Findings",
        "PROPOSED Conclusions",
    ]


def test_a_head_already_present_on_a_conjunct_is_not_doubled() -> None:
    assert decompose("Proposed Findings and Proposed Conclusions")[1:] == [
        "Proposed Findings",
        "Proposed Conclusions",
    ]


def test_a_head_only_counts_when_it_leads_the_first_conjunct() -> None:
    # "Findings" is not a shared-head modifier, so nothing is prepended.
    assert decompose("Findings and Conclusions")[1:] == ["Findings", "Conclusions"]


# -- shared tail ---------------------------------------------------------


def test_a_trailing_shared_tail_is_restored_onto_earlier_conjuncts() -> None:
    assert decompose("Antitrust and Securities Law")[1:] == ["Antitrust Law", "Securities Law"]


@pytest.mark.parametrize(
    "tail",
    ["Agreement", "Law", "Claims", "Claim", "Act", "Clause", "Clauses", "Provisions", "Rights"],
)
def test_every_shared_tail_noun_is_restored(tail: str) -> None:
    assert decompose(f"Alpha and Beta {tail}")[1:] == [f"Alpha {tail}", f"Beta {tail}"]


def test_a_tail_already_present_on_a_conjunct_is_not_doubled() -> None:
    assert decompose("Antitrust Law and Securities Law")[1:] == ["Antitrust Law", "Securities Law"]


def test_head_and_tail_are_mutually_exclusive_readings() -> None:
    """A leading head wins, so "Proposed X and Y Law" never grows a spurious "... Law" tail."""
    parts = decompose("Proposed Findings and Conclusions of Law")
    assert parts[1:] == ["Proposed Findings", "Proposed Conclusions of Law"]
    assert not any(p.endswith("Findings Law") for p in parts)
