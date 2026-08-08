"""Regression tests for the Ch02 precision-fix resolution policy (LabelResolver + seed blocklist).

Each test pins a measured v6 proving-run failure. A tiny fake ``search_by_label`` reproduces the
FOLIO 0-100 scores measured on the live catalogue (mis-maps at 90.0; genuine recoveries >= 96.97).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from folio_resolve import (
    WHOLE_STRING_THRESHOLD,
    AliasBlocklist,
    LabelResolver,
    load_seed_blocklist,
)


@dataclass
class _Result:
    iri: str
    preferred_label: str
    branch: str


# Measured FOLIO search_by_label output (label -> [(concept, score_0_100)]), best first.
_CATALOGUE: dict[str, list[tuple[_Result, float]]] = {
    # Place/agency mis-maps — rapidfuzz over-scores short proper-noun labels to exactly 90.0.
    "law": [(_Result("R-delaware", "Delaware", "Location"), 90.0)],
    "Presumptions": [(_Result("R-nmi", "Northern Mariana Islands", "Location"), 90.0)],
    "effect of answers": [(_Result("R-fec", "Federal Election Commission", "Governmental Body"), 90.0)],
    "Arbitration and Mediation": [(_Result("R-trat", "Trat", "Location"), 90.0)],
    # Genuine recoveries — full matches score >= 96.97.
    "Burden of Proof": [(_Result("R-lbop", "Litigation Burdens of Proof", "Objectives"), 100.0)],
    "Hearing": [(_Result("R-hearing", "Hearing", "Event"), 100.0)],
    "Lawyer": [(_Result("R-lawyer", "Lawyer", "Engagement Terms"), 100.0)],
    "Closing Arguments": [(_Result("R-closing", "Closing Arguments / Summation", "Event"), 96.97)],
    # Conjuncts of the 12b5e434 compound heading both resolve cleanly.
    "Proposed Findings of Fact": [(_Result("R-pfof", "Proposed Findings of Fact", "Document / Artifact"), 100.0)],
    "Proposed Conclusions of Law": [(_Result("R-pcol", "Proposed Conclusions of Law", "Document / Artifact"), 100.0)],
    "Arbitration": [(_Result("R-arb", "Arbitration Practice", "Service"), 100.0)],
    "Mediation": [(_Result("R-med", "Mediation Practice", "Service"), 100.0)],
}


def _search(label: str) -> list[tuple[object, float]]:
    return list(_CATALOGUE.get(label, []))


def test_whole_string_bar_rejects_place_mismap_band() -> None:
    # "law" -> Delaware (90.0) is below the 92.0 bar: the entire 90.0 mis-map band is rejected.
    resolver = LabelResolver(_search)
    assert resolver.resolve("law") == []
    assert resolver.resolve("Presumptions") == []
    assert resolver.resolve("effect of answers") == []


def test_whole_string_bar_keeps_named_recoveries() -> None:
    resolver = LabelResolver(_search)
    for label, iri in [
        ("Burden of Proof", "R-lbop"),
        ("Hearing", "R-hearing"),
        ("Lawyer", "R-lawyer"),
        ("Closing Arguments", "R-closing"),
    ]:
        resolved = resolver.resolve(label)
        assert len(resolved) == 1
        assert resolved[0].iri == iri
        # Every resolved concept carries its branch (fix a) so gates can evaluate it.
        assert resolved[0].branch != ""


def test_decompose_first_splits_compound_into_two_siblings() -> None:
    # 12b5e434: whole-string "...Fact and Conclusions of Law" would fuzzy-match one wrong partial;
    # decompose-first resolves both siblings instead.
    resolver = LabelResolver(_search)
    resolved = resolver.resolve("Proposed Findings of Fact and Conclusions of Law")
    assert {r.iri for r in resolved} == {"R-pfof", "R-pcol"}


def test_decompose_first_beats_whole_string_place_mismap() -> None:
    # "Arbitration and Mediation" whole-string mis-maps to "Trat" (Location, 90). Decompose-first
    # resolves the two Service conjuncts and never returns the place.
    resolver = LabelResolver(_search)
    resolved = resolver.resolve("Arbitration and Mediation")
    assert {r.iri for r in resolved} == {"R-arb", "R-med"}
    assert all(r.branch == "Service" for r in resolved)


def test_threshold_constant_between_mismap_band_and_named_recoveries() -> None:
    assert 90.0 < WHOLE_STRING_THRESHOLD <= 96.97


def test_seed_blocklist_fires_on_real_iris() -> None:
    bl = load_seed_blocklist()
    assert not bl.is_empty()
    # Action != Auction (real Auction IRI, not the old synthetic EXAMPLE-Auction).
    assert bl.is_blocked("Action", "https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO")
    # Agency homonyms recorded from the Ch01/Ch02 packs.
    assert bl.is_blocked("justice", "https://folio.openlegalstandard.org/R0DB70442Cf8b73D9275F14a")
    assert bl.is_blocked("state", "https://folio.openlegalstandard.org/R0480ee8B57a1441863FE362")
    assert bl.is_blocked("tax", "https://folio.openlegalstandard.org/R1B9b8f8D5c8164D9da3B238")
    # The old synthetic placeholder is gone (it could never fire on real data).
    assert not bl.is_blocked("Action", "https://folio.openlegalstandard.org/EXAMPLE-Auction")


def test_seed_blocklist_is_a_real_alias_blocklist() -> None:
    assert isinstance(load_seed_blocklist(), AliasBlocklist)


# -- resolution policy details -------------------------------------------


def test_a_label_the_catalogue_does_not_know_resolves_to_nothing() -> None:
    assert LabelResolver(_search).resolve("no such heading anywhere") == []


def test_the_surface_records_what_actually_resolved() -> None:
    """Gates compare `surface` against `label` to tell a real place mention from a mis-map."""
    resolver = LabelResolver(_search)
    assert resolver.resolve("Hearing")[0].surface == "Hearing"
    conjuncts = resolver.resolve("Proposed Findings of Fact and Conclusions of Law")
    assert {r.surface for r in conjuncts} == {
        "Proposed Findings of Fact",
        "Proposed Conclusions of Law",
    }


def test_the_whole_string_is_the_fallback_when_no_conjunct_clears_the_bar() -> None:
    # "Presumptions" alone is a 90.0 mis-map, but a compound whose conjuncts resolve to nothing
    # must still get its whole-string chance rather than silently returning [].
    catalogue = {"Alpha and Beta": [(_Result("R-ab", "Alpha and Beta", "Objectives"), 100.0)]}
    resolver = LabelResolver(lambda label: list(catalogue.get(label, [])))
    resolved = resolver.resolve("Alpha and Beta")
    assert [r.iri for r in resolved] == ["R-ab"]
    assert resolved[0].surface == "Alpha and Beta"


def test_conjuncts_resolving_to_the_same_concept_are_deduplicated() -> None:
    same = [(_Result("R-same", "Same", "Objectives"), 100.0)]
    resolver = LabelResolver(lambda label: list(same))
    assert [r.iri for r in resolver.resolve("Alpha and Beta")] == ["R-same"]


def test_the_thresholds_are_per_instance_overridable() -> None:
    # A consumer with a differently-calibrated backend tunes the bar without forking the policy.
    assert LabelResolver(_search).resolve("law") == []
    lenient = LabelResolver(_search, whole_string_threshold=89.0, conjunct_threshold=89.0)
    assert [r.iri for r in lenient.resolve("law")] == ["R-delaware"]
    assert [r.iri for r in lenient.resolve("Arbitration and Mediation")] == ["R-arb", "R-med"]


def test_a_row_without_an_iri_is_not_a_resolution() -> None:
    unusable = [(_Result("", "Nameless", "Objectives"), 100.0)]
    assert LabelResolver(lambda _label: list(unusable)).resolve("anything") == []


def test_the_label_falls_back_from_preferred_label_to_label() -> None:
    @dataclass
    class _LabelOnly:
        iri: str
        label: str
        branch: str

    rows = [(_LabelOnly("R1", "Hearing", "Event"), 100.0)]
    assert LabelResolver(lambda _l: list(rows)).resolve("Hearing")[0].label == "Hearing"


def test_a_search_backend_that_raises_degrades_to_no_resolution() -> None:
    def _boom(_label: str) -> list[tuple[object, float]]:
        raise RuntimeError("catalogue unavailable")

    assert LabelResolver(_boom).resolve("Hearing") == []
    assert LabelResolver(_boom).resolve("Arbitration and Mediation") == []


@pytest.mark.parametrize(
    "row",
    [
        ("not-a-pair-just-a-string"),
        (object(),),
        (_Result("R1", "X", "Y"), "high"),
        (_Result("R1", "X", "Y"), None),
        (_Result("R1", "X", "Y"),),
    ],
)
def test_a_malformed_search_row_degrades_instead_of_raising(row: object) -> None:
    """Regression: the unpack and float() sat OUTSIDE the guard.

    `search_by_label` is a duck-typed consumer callable, so its top row is untrusted input —
    but a non-numeric score or a row that is not a (concept, score) pair raised straight
    through a resolver that documents itself as returning [] when nothing clears the bar.
    Same defensive contract as parse_judge_json and compute_relevance_score.
    """
    assert LabelResolver(lambda _label: [row]).resolve("Hearing") == []  # type: ignore[list-item]


def test_a_numeric_string_score_is_still_accepted() -> None:
    # float("96.97") is a real number; only genuinely unusable rows are dropped.
    rows = [(_Result("R1", "Hearing", "Event"), "96.97")]
    assert [r.iri for r in LabelResolver(lambda _l: list(rows)).resolve("Hearing")] == ["R1"]  # type: ignore[list-item]


def test_resolved_concepts_are_frozen_value_objects() -> None:
    resolved = LabelResolver(_search).resolve("Hearing")[0]
    assert hash(resolved)
    with pytest.raises(AttributeError):
        resolved.score = 0.0  # type: ignore[misc]
