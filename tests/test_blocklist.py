"""Alias / homonym blocklist — the deterministic Action != Auction guard (Ch02 unit 4b06a90c).

The shipped seed's real-IRI assertions live in ``test_resolve.py``; this file covers the
container semantics (keying, domain scoping, persistence, degraded loading) with synthetic IRIs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from folio_resolve import AliasBlocklist, BlockedAlias, load_seed_blocklist
from folio_resolve.blocklist import SEED_RESOURCE

# -- BlockedAlias keying -------------------------------------------------


def test_the_key_normalizes_term_and_domain_but_not_iri() -> None:
    # IRIs are case-sensitive identifiers; surface terms and domains are human-entered.
    assert BlockedAlias("Action", "R-Auction", "Litigation").key() == ("action", "R-Auction", "litigation")
    assert BlockedAlias("Action", "R-Auction").key() == ("action", "R-Auction", "")


def test_entries_differing_only_in_case_collapse_to_one() -> None:
    bl = AliasBlocklist(
        [BlockedAlias("Action", "R-auction"), BlockedAlias("ACTION", "R-auction", reason="dupe")]
    )
    assert len(bl) == 1
    assert bl.entries()[0].reason == "dupe"  # last write wins


def test_the_iri_is_matched_exactly() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    assert not bl.is_blocked("Action", "r-auction")


# -- construction and mutation -------------------------------------------


def test_an_empty_blocklist_blocks_nothing() -> None:
    bl = AliasBlocklist()
    assert bl.is_empty()
    assert len(bl) == 0
    assert bl.entries() == []
    assert not bl.is_blocked("anything", "R-anything")


def test_none_entries_is_equivalent_to_empty() -> None:
    assert AliasBlocklist(None).is_empty()


def test_block_is_a_shorthand_for_add() -> None:
    bl = AliasBlocklist()
    bl.block("Action", "R-auction", reason="Action != Auction")
    assert bl.is_blocked("Action", "R-auction")
    assert not bl.is_empty()
    assert bl.entries()[0].reason == "Action != Auction"


def test_a_wrong_verdict_can_be_appended_at_runtime() -> None:
    # The self-improving loop: a reviewer's `wrong` verdict becomes a permanent veto.
    bl = AliasBlocklist()
    assert not bl.is_blocked("charge", "R-encumbrance")
    bl.block("charge", "R-encumbrance", "criminal", reason="reviewer verdict")
    assert bl.is_blocked("charge", "R-encumbrance", domains=["criminal"])


def test_entries_returns_a_copy() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    bl.entries().clear()
    assert len(bl) == 1


# -- domain scoping ------------------------------------------------------


def test_a_global_block_applies_in_every_domain() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    assert bl.is_blocked("Action", "R-auction")
    assert bl.is_blocked("Action", "R-auction", domains=["litigation"])
    assert bl.is_blocked("Action", "R-auction", domains=[])


def test_a_domain_block_only_applies_in_that_domain() -> None:
    bl = AliasBlocklist([BlockedAlias("charge", "R-encumbrance", domain="criminal")])
    assert bl.is_blocked("charge", "R-encumbrance", domains=["criminal"])
    assert bl.is_blocked("charge", "R-encumbrance", domains=["property", "criminal"])
    assert not bl.is_blocked("charge", "R-encumbrance", domains=["property"])
    # No active prior at all: a domain-scoped block must not fire globally.
    assert not bl.is_blocked("charge", "R-encumbrance")


def test_domain_matching_is_case_insensitive() -> None:
    bl = AliasBlocklist([BlockedAlias("charge", "R-x", domain="Criminal")])
    assert bl.is_blocked("charge", "R-x", domains=["CRIMINAL"])


def test_the_same_alias_can_be_wrong_in_one_domain_and_right_in_another() -> None:
    bl = AliasBlocklist([BlockedAlias("charge", "R-encumbrance", domain="criminal")])
    assert bl.filter_candidates("charge", [("R-encumbrance", 90.0)], domains=["criminal"]) == []
    assert bl.filter_candidates("charge", [("R-encumbrance", 90.0)], domains=["property"]) == [
        ("R-encumbrance", 90.0)
    ]


# -- filter_candidates ---------------------------------------------------


def test_filter_preserves_order_and_scores_of_survivors() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    got = bl.filter_candidates(
        "Action", [("R-cause", 85.0), ("R-auction", 92.3), ("R-claim", 70.0)]
    )
    assert got == [("R-cause", 85.0), ("R-claim", 70.0)]


def test_filtering_an_empty_candidate_list_is_empty() -> None:
    assert AliasBlocklist([BlockedAlias("a", "R1")]).filter_candidates("a", []) == []


def test_filter_accepts_any_iterable_of_candidates() -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    got = bl.filter_candidates("Action", iter([("R-auction", 92.3), ("R-cause", 85.0)]))
    assert got == [("R-cause", 85.0)]


def test_filter_consumes_a_domains_iterator_once_for_many_candidates() -> None:
    """`domains` is materialized up front — a generator must not be exhausted by candidate 1."""
    bl = AliasBlocklist([BlockedAlias("charge", "R-b", domain="criminal")])
    got = bl.filter_candidates(
        "charge",
        [("R-a", 90.0), ("R-b", 90.0), ("R-c", 90.0)],
        domains=(d for d in ["criminal"]),
    )
    assert got == [("R-a", 90.0), ("R-c", 90.0)]


# -- persistence ---------------------------------------------------------


def test_save_load_roundtrip_preserves_every_field(tmp_path: Path) -> None:
    original = AliasBlocklist(
        [
            BlockedAlias("Action", "R-auction", reason="Action != Auction"),
            BlockedAlias("charge", "R-encumbrance", domain="criminal", reason="sense split"),
        ]
    )
    path = tmp_path / "bl.json"
    original.save(path)
    loaded = AliasBlocklist.load(path)
    assert {e.key(): (e.domain, e.reason) for e in loaded.entries()} == {
        e.key(): (e.domain, e.reason) for e in original.entries()
    }


def test_the_saved_file_is_versioned_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "bl.json"
    AliasBlocklist([BlockedAlias("Action", "R-auction")]).save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["blocked_aliases"] == [
        {"surface_term": "Action", "blocked_iri": "R-auction", "domain": None, "reason": ""}
    ]


def test_load_tolerates_a_file_with_no_entries(tmp_path: Path) -> None:
    path = tmp_path / "bl.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert AliasBlocklist.load(path).is_empty()


def test_load_accepts_a_string_path(tmp_path: Path) -> None:
    path = tmp_path / "bl.json"
    AliasBlocklist([BlockedAlias("Action", "R-auction")]).save(str(path))
    assert AliasBlocklist.load(str(path)).is_blocked("Action", "R-auction")


def test_save_roundtrips_through_load_without_growing(tmp_path: Path) -> None:
    bl = AliasBlocklist([BlockedAlias("Action", "R-auction")])
    for i in range(3):
        path = tmp_path / f"bl{i}.json"
        bl.save(path)
        bl = AliasBlocklist.load(path)
    assert len(bl) == 1


# -- the shipped seed ----------------------------------------------------


def test_the_seed_loads_from_package_data() -> None:
    seed = load_seed_blocklist()
    assert isinstance(seed, AliasBlocklist)
    assert not seed.is_empty()
    assert AliasBlocklist.from_seed().entries()


def test_the_seed_resource_coordinates_are_inside_this_package() -> None:
    assert SEED_RESOURCE == ("folio_resolve.data", "alias_blocklist.json")


def test_a_missing_seed_degrades_to_no_vetoes_rather_than_crashing_the_tagger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_pkg: str) -> object:
        raise ModuleNotFoundError("folio_resolve.data")

    monkeypatch.setattr("folio_resolve.blocklist.resources.files", _boom)
    assert AliasBlocklist.from_seed().is_empty()


def test_an_unreadable_seed_also_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Unreadable:
        def __truediv__(self, _name: str) -> _Unreadable:
            return self

        def read_text(self, encoding: str = "utf-8") -> str:
            raise OSError("permission denied")

    monkeypatch.setattr("folio_resolve.blocklist.resources.files", lambda _pkg: _Unreadable())
    assert load_seed_blocklist().is_empty()
