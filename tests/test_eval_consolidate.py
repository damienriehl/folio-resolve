"""The consolidated review sheet: what counts as a decision, and what counts as a collision."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from folio_eval.consolidate import (
    authored,
    consolidate,
    label_index,
    load_sittings,
    render_consolidated,
)

PREFIX = "folio-eval-draft:"
V5_310 = f"{PREFIX}v5-abc|ontology-unknown|310|fingerprint310"
V5_236 = f"{PREFIX}v5-abc|ontology-unknown|236|fingerprint236"
V3_310 = f"{PREFIX}v3-old|ontology-unknown|310|fingerprintv3"

GOLD_IRI = "https://folio.openlegalstandard.org/RGOLD"
PIPE_IRI = "https://folio.openlegalstandard.org/RPIPE"


def bundle(drafts: dict[str, Any]) -> dict[str, Any]:
    return {"exported_at": "2026-08-16T00:00:00Z", "drafts": drafts}


def packet(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "rows": rows
        if rows is not None
        else [
            {
                "decision_id": "suspect:alive",
                "section": "suspect",
                "surface_label": "Borrower",
                "ancestor_path": ["Banking", "Insurance Finance"],
                "gold": [{"iri": GOLD_IRI, "label": "Banking Law"}],
                "pipeline": [{"iri": PIPE_IRI, "label": "Structured Finance Law"}],
            }
        ]
    }


def test_machine_state_is_not_a_decision() -> None:
    """``collect()`` emits defaults for every row without a baseline; those are not the reviewer's.

    One of Damien's real stranded sittings held 104 entries and not one of them was his -- ranking
    or reporting on the raw count reads that sitting as the fullest body of work in the browser.
    """
    default_only = {"gold": {GOLD_IRI: "keep"}, "pipeline": {PIPE_IRI: "not_gold"}}
    assert authored(default_only) == {}
    # a level mapping alone is the system's atomization, not a judgement
    assert authored({"level_mappings": {"L1": [GOLD_IRI]}}) == {}
    # but a non-default verdict, a note, or an added concept is
    assert authored({"gold": {GOLD_IRI: "remove"}}) == {"gold": {GOLD_IRI: "remove"}}
    assert authored({"pipeline": {PIPE_IRI: "elevate"}}) == {"pipeline": {PIPE_IRI: "elevate"}}
    assert authored({"note": "keep an eye on this"})["note"] == "keep an eye on this"
    assert authored({"pairing": "heuristic"})["pairing"] == "heuristic"


def test_a_sitting_with_nothing_authored_is_dropped() -> None:
    sittings = load_sittings(
        bundle(
            {
                V5_236: {"version": 3, "decisions": {"suspect:alive": {"gold": {GOLD_IRI: "keep"}}}},
                V5_310: {"version": 4, "decisions": {"suspect:alive": {"note": "mine"}}},
            }
        )
    )
    assert [s.packet_key for s in sittings] == ["v5-abc|ontology-unknown|310|fingerprint310"]
    assert sittings[0].baseline == "v5-abc"
    assert sittings[0].rows == "310"


def test_same_field_different_answers_is_the_only_collision() -> None:
    """Complementary fields merge; identical values agree. Neither is work to redo."""
    sittings = load_sittings(
        bundle(
            {
                V3_310: {"decisions": {"pairing:x": {"pairing": "heuristic", "note": "context"}}},
                V5_310: {"decisions": {"pairing:x": {"pairing": "heuristic"}}},
                V5_236: {"decisions": {"pairing:x": {"gold": {GOLD_IRI: "remove"}}}},
            }
        )
    )
    merged = consolidate(sittings)
    # pairing appears twice with the SAME value -> agreement, not collision
    assert ("pairing:x", "pairing") in merged.agreements
    assert merged.collisions == []
    # every field is retained with its provenance
    assert set(merged.by_row["pairing:x"]) == {"pairing", "note", "gold"}
    assert len(merged.by_row["pairing:x"]["pairing"]) == 2


def test_a_real_disagreement_is_reported() -> None:
    merged = consolidate(
        load_sittings(
            bundle(
                {
                    V3_310: {"decisions": {"pairing:x": {"pairing": "heuristic"}}},
                    V5_310: {"decisions": {"pairing:x": {"pairing": "alternative"}}},
                }
            )
        )
    )
    assert merged.collisions == [("pairing:x", "pairing")]
    assert merged.agreements == []


def test_rows_split_by_whether_the_current_packet_still_has_them() -> None:
    """Orphaned work is the only category that silently evaporates on the next fold."""
    merged = consolidate(
        load_sittings(
            bundle(
                {
                    V5_310: {
                        "decisions": {
                            "suspect:alive": {"gold": {GOLD_IRI: "remove"}},
                            "suspect:gone": {"pipeline": {PIPE_IRI: "elevate"}},
                        }
                    }
                }
            )
        )
    )
    present = {"suspect:alive"}
    assert merged.rows_for(present, inside=True) == ["suspect:alive"]
    assert merged.rows_for(present, inside=False) == ["suspect:gone"]


def test_the_sheet_shows_concept_labels_not_bare_iris() -> None:
    """The simplified rendering: an rdfs:label a reviewer can read, IRI on hover."""
    merged = consolidate(
        load_sittings(
            bundle({V5_310: {"decisions": {"suspect:alive": {"gold": {GOLD_IRI: "remove"}}}}})
        )
    )
    page = render_consolidated(merged, packet(), generated="2026-08-16 00:00 UTC")

    assert label_index(packet())[GOLD_IRI] == "Banking Law"
    assert ">Banking Law<" in page
    assert f'title="{GOLD_IRI}"' in page
    # the row is named by its input label and path, not by an opaque decision id
    assert "Borrower" in page and "Banking &gt; Insurance Finance" in page
    assert ">remove<" in page
    # Self-contained: the page carries an inline filter script, but must never REACH for anything.
    # It is opened from a Cloudflare-Access-gated URL and from file://, where any external load
    # silently fails and takes the styling or behaviour with it.
    for forbidden in ("src=", "href=", "@import", "fetch(", "XMLHttpRequest", "//cdn"):
        assert forbidden not in page


def test_the_sheet_states_plainly_when_there_is_nothing_to_reconcile() -> None:
    merged = consolidate(
        load_sittings(bundle({V5_310: {"decisions": {"suspect:alive": {"note": "mine"}}}}))
    )
    page = render_consolidated(merged, packet(), generated="2026-08-16 00:00 UTC")
    assert "nothing to reconcile" in page
    assert "true collisions" in page


def test_an_unresolvable_concept_still_renders() -> None:
    """A concept from a retired packet has no label here; show the short IRI rather than nothing."""
    merged = consolidate(
        load_sittings(
            bundle(
                {
                    V5_236: {
                        "decisions": {
                            "suspect:gone": {
                                "added_mappings": [
                                    {"iri": "https://folio.openlegalstandard.org/RUNKNOWN",
                                     "label": "Secured Transaction"}
                                ]
                            }
                        }
                    }
                }
            )
        )
    )
    page = render_consolidated(merged, packet(), generated="2026-08-16 00:00 UTC")
    assert "RUNKNOWN" in page
    assert "Secured Transaction" in page
    assert "not present in the current packet" in page
