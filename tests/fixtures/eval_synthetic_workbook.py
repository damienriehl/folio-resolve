"""Synthetic taxonomy sheets + a tiny label index for the gold-builder tests (U2).

Every string here is invented (widgets, gadgets, sprockets). No real firm taxonomy content and
no real FOLIO IRIs appear in this file — the IRIs are FOLIO-shaped but fabricated, so the tests
exercise the real code paths without carrying either firm data or an ontology snapshot (KTD1).

The sheet shapes mirror the *structure* the parsers detect (a cascade sheet with ``Level 2`` /
``Level 3`` heading columns and ``SALI 0..6`` value columns; SharePoint-style term-set exports
with ``Level N Term`` columns and ``SALI Mapping`` / ``SALI IRI`` columns) without reproducing
any workbook's literal header text.
"""

from __future__ import annotations

from folio_eval.resolve_labels import IndexedConcept, LabelIndex

FOLIO = "https://folio.openlegalstandard.org/"

# --- Synthetic ontology ---------------------------------------------------------------------

W_MANUFACTURING = f"{FOLIO}RSYNwidget00000000000001"
W_ADVISORY = f"{FOLIO}RSYNgadget00000000000002"
W_LITIGATION = f"{FOLIO}RSYNsprocket000000000003"
W_ENFORCEMENT = f"{FOLIO}RSYNdoohickey00000000004"
W_ARBITRATION = f"{FOLIO}RSYNthingamajig000000005"
W_ASSEMBLY = f"{FOLIO}RSYNgizmo000000000000006"
W_INDUSTRY = f"{FOLIO}RSYNtrinket0000000000007"
W_AGREEMENTS = f"{FOLIO}RSYNbauble00000000000008"
W_PURCHASE = f"{FOLIO}RSYNwhatsit0000000000009"
W_FINANCE = f"{FOLIO}RSYNdoodad00000000000010"
W_COLON = f"{FOLIO}RSYNzorb000000000000011"
W_AMBIG_A = f"{FOLIO}RSYNcontraptionAAAAA012"
W_AMBIG_B = f"{FOLIO}RSYNcontraptionBBBBB013"

SYNTHETIC_CONCEPTS: tuple[IndexedConcept, ...] = (
    IndexedConcept(
        iri=W_MANUFACTURING,
        preferred_labels=("Widget Manufacturing Law",),
        alternative_labels=("Widget Law",),
    ),
    IndexedConcept(iri=W_ADVISORY, preferred_labels=("Gadget Advisory Service",)),
    IndexedConcept(iri=W_LITIGATION, preferred_labels=("Sprocket Litigation Practice",)),
    IndexedConcept(iri=W_ENFORCEMENT, preferred_labels=("Doohickey Regulatory Enforcement",)),
    IndexedConcept(iri=W_ARBITRATION, preferred_labels=("Thingamajig Arbitration",)),
    # En-dash in the ontology label; the sheets spell it with a plain hyphen.
    IndexedConcept(iri=W_ASSEMBLY, preferred_labels=("Gizmo\u2013Assembly Law",)),
    IndexedConcept(
        iri=W_INDUSTRY,
        preferred_labels=("Trinket Industry",),
        alternative_labels=("Trinket Sector",),
    ),
    IndexedConcept(iri=W_AGREEMENTS, preferred_labels=("Bauble Agreements",)),
    IndexedConcept(iri=W_PURCHASE, preferred_labels=("Whatsit Purchase and Sale",)),
    IndexedConcept(iri=W_FINANCE, preferred_labels=("Doodad Finance Law",)),
    # A preferred label that itself contains a colon: the compound parse must fall through to
    # the whole-string branch for it.
    IndexedConcept(iri=W_COLON, preferred_labels=("Zorb: Special Regime Law",)),
    # Two concepts sharing one preferred label -> ambiguous resolution.
    IndexedConcept(iri=W_AMBIG_A, preferred_labels=("Contraption Services",)),
    IndexedConcept(iri=W_AMBIG_B, preferred_labels=("Contraption Services",)),
)


def synthetic_index() -> LabelIndex:
    return LabelIndex.from_concepts(SYNTHETIC_CONCEPTS)


# --- Synthetic cascade sheet (firm-1 shape) -------------------------------------------------

FIRM1_HEADER: list[str | None] = [
    "Group Code",
    "Level 2 - Category (select one)",
    "Level 3 - Attributes (select all that apply)",
    "Widget Code",
    "SALI 0 (cascade down)",
    "SALI 1",
    "SALI 2",
    "SALI 3",
    "SALI 4",
    "SALI 5",
    "SALI 6",
    "SALI NOTES",
]


def _f1(
    level1: str | None = None,
    level2: str | None = None,
    level3: str | None = None,
    code: str | None = None,
    sali: dict[int, str] | None = None,
    notes: str | None = None,
) -> list[str | None]:
    """Build one cascade-sheet row; ``sali`` maps SALI column number (0-6) to a cell value."""
    row: list[str | None] = [level1, level2, level3, code] + [None] * 7 + [notes]
    for column, value in (sali or {}).items():
        row[4 + column] = value
    return row


FIRM1_ROWS: list[list[str | None]] = [
    FIRM1_HEADER,
    # L1 header carrying a cascading value.
    _f1(level1="Widget Practice", sali={0: "Widget Manufacturing Law"}),
    # L2-only header carrying two cascading values in non-adjacent columns.
    _f1(level2="Widget Advice", sali={0: "Gadget Advisory Service", 3: "Trinket Industry"}),
    # L3 with its own value -> own + L2 + L1 union.
    _f1(level3="Enforcement matters", code="1.0", sali={2: "Doohickey Regulatory Enforcement"}),
    # L3 with no own values -> inherits L2 + L1 only (not blank).
    _f1(level3="General advice", code="2.0"),
    # Non-referential leaf -> excluded and counted.
    _f1(level3="Other", code="3.0"),
    # Relational expression -> excluded from gold, item keeps its other value.
    _f1(level3="Membership matters", code="4.0", sali={1: "sali:isMemberOf", 2: "Thingamajig Arbitration"}),
    # Pipe-delimited cell -> two gold values from one cell.
    _f1(
        level3="Blended matters",
        code="5.0",
        sali={1: "Whatsit Purchase and Sale | Doodad Finance Law"},
    ),
    # Dash-variant + trailing whitespace -> resolves only after normalization.
    _f1(level3="Assembly matters", code="6.0", sali={4: "Gizmo-Assembly Law "}),
    # SALI NOTES flag words -> low-confidence pre-flag.
    _f1(level3="Unsettled matters", code="7.0", sali={1: "Sprocket Litigation Practice"}, notes="discuss with the team"),
    # L2 header with no values at all, under an L1 with no values either (see next L1).
    _f1(level1="Empty Practice"),
    _f1(level2="Empty Category"),
    # Blank only when own AND inherited are empty.
    _f1(level3="Nothing mapped here", code="8.0"),
    # L2 + L3 on one row: the row's values are the L2's mapping, cascading to all children.
    _f1(level2="Shared Category", level3="First attribute", code="9.0", sali={0: "Widget Law", 1: "Bauble Agreements"}),
    _f1(level3="Second attribute", code="10.0"),
    # Duplicate (input, gold) pair: same ancestors, same leaf, same values as row above.
    _f1(level3="Second attribute", code="11.0"),
]


# --- Synthetic cascade sheet for the per-cell derivation (gold v2, KTD6 v2) ------------------
#
# The v2 reading needs shapes v1 never distinguished: a heading cell that is itself an item, a
# shared row whose input and output counts do not line up, and the same cell text appearing twice
# with different answers. Kept as its own sheet so the v1 fixture's expectations never move.

FIRM1_V2_ROWS: list[list[str | None]] = [
    FIRM1_HEADER,
    # L1 heading cell carrying its own mapping -> its own item, nothing cascades from it.
    _f1(level1="Widget Practice", sali={0: "Widget Manufacturing Law"}),
    # L2 heading cell, one input and two output blocks -> both belong to that one input.
    _f1(level2="Widget Advice", sali={0: "Gadget Advisory Service", 3: "Trinket Industry"}),
    # L3 cell with its own mapping -> exactly its own, no inheritance from the headings above.
    _f1(level3="Enforcement matters", code="1.0", sali={2: "Doohickey Regulatory Enforcement"}),
    # L3 cell with nothing of its own -> blank (coverage, KD7), no longer rescued by a cascade.
    _f1(level3="General advice", code="2.0"),
    # Shared row, two inputs and two output blocks -> positional 1:1 pairing, unambiguous.
    _f1(
        level2="Shared Category",
        level3="First attribute",
        code="3.0",
        sali={0: "Widget Law", 1: "Bauble Agreements"},
    ),
    # Shared row, two inputs and THREE output blocks -> heuristic + pairing_ambiguous flag.
    _f1(
        level2="Uneven Category",
        level3="Odd attribute",
        code="4.0",
        sali={
            0: "Sprocket Litigation Practice",
            1: "Thingamajig Arbitration",
            2: "Gadget Advisory Service",
        },
    ),
    _f1(level1="Second Practice"),
    _f1(level2="Other Category"),
    # Same cell text as above, different answer -> one deduped item flagged gold_inconsistent.
    _f1(level3="Enforcement matters", code="5.0", sali={4: "Whatsit Purchase and Sale"}),
    # Same cell text as above, both unanswered -> deduped, and *not* an inconsistency.
    _f1(level3="General advice", code="6.0"),
    # Answered in one place, unanswered in another -> deduped, and *not* an inconsistency either
    # (KD7: a blank cell is 'not yet mapped', never a contradiction of a cell that is mapped).
    _f1(level3="First attribute", code="7.0"),
    # SALI NOTES flag words -> a low-confidence pre-flag, i.e. a section-C suspect.
    _f1(
        level3="Unsettled matters",
        code="8.0",
        sali={1: "Sprocket Litigation Practice"},
        notes="discuss with the team",
    ),
]


# --- Synthetic term-set sheets (firm-2 shape) -----------------------------------------------

FIRM2_WORKTYPE_HEADER: list[str | None] = [
    "Term Set Name",
    "Term Description",
    "Level 1 Term",
    "Level 2 Term",
    "Term Depreciated",
    "SALI Mapping: Area of Law",
    "SALI Mapping: Service",
    "Additional SALI Mapping",
    "SALI IRI",
]


def _f2wt(
    term_set: str | None,
    description: str,
    level1: str,
    level2: str | None = None,
    area: str | None = None,
    service: str | None = None,
    additional: str | None = None,
    iri: str | None = None,
) -> list[str | None]:
    return [term_set, description, level1, level2, "N", area, service, additional, iri]


FIRM2_WORKTYPE_ROWS: list[list[str | None]] = [
    FIRM2_WORKTYPE_HEADER,
    # Compound value: the right-hand side resolves.
    _f2wt("Widget Work Kinds", "Acquisition support", "Acquisition support", area="Doodad Finance: Whatsit Purchase and Sale"),
    # Whole-string branch: the rhs is not a label but the whole compound string is.
    _f2wt(None, "Special regime work", "Special regime work", area="Zorb: Special Regime Law"),
    # Bucket branch: neither rhs nor whole string resolves, the bucket does.
    _f2wt(None, "Sector work", "Sector work", area="Trinket Industry: something unmappable"),
    # '?'-suffixed value -> gold-suspect queue (still resolved, flagged).
    _f2wt(None, "Unclear work", "Unclear work", area="Sprocket Litigation Practice?"),
    # Non-referential value -> excluded and counted.
    _f2wt(None, "Varying work", "Varying work", area="varies"),
    # 'Additional SALI Mapping' is notes-not-gold.
    _f2wt(
        None,
        "Noted work",
        "Noted work",
        area="Gadget Advisory Service",
        additional="Thingamajig Arbitration",
    ),
    # No mapping at all -> blank (coverage, not scored).
    _f2wt(None, "Unmapped work", "Unmapped work"),
]

FIRM2_TERMSET_HEADER: list[str | None] = [
    "Term Set Name",
    "Term Description",
    "Level 1 Term",
    "Level 2 Term",
    "Term Depreciated",
    "SALI Mapping",
    "SALI IRI",
]


def _f2ts(
    term_set: str | None,
    level1: str,
    level2: str | None = None,
    mapping: str | None = None,
    iri: str | None = None,
) -> list[str | None]:
    leaf = level2 or level1
    return [term_set, leaf, level1, level2, "N", mapping, iri]


FIRM2_SECTOR_ROWS: list[list[str | None]] = [
    FIRM2_TERMSET_HEADER,
    # Legacy IRI with a trailing space, alongside its label (AE3).
    _f2ts(
        "Sprocket Sectors",
        "Trinkets",
        mapping="Trinket Industry",
        iri=f"http://lmss.sali.org/{W_INDUSTRY.rsplit('/', 1)[1]} ",
    ),
    # A lemma/plural-variant label.
    _f2ts(None, "Baubles", mapping="Bauble Agreement"),
    # Unresolvable label -> resolution batch.
    _f2ts(None, "Mystery", mapping="Utterly Unmappable Concept"),
]

# Two rows describing the SAME term (same term key) -> their labels union into one item.
FIRM2_MULTIROW_ROWS: list[list[str | None]] = [
    FIRM2_TERMSET_HEADER,
    _f2ts("Doohickey Topics", "Widgets", "Manufacture", mapping="Widget Manufacturing Law"),
    _f2ts(None, "Widgets", "Manufacture", mapping="Gadget Advisory Service"),
]


# --- Pipe-cell + cross-firm-collision fixture (the sheet-Gold defect, 2026-07-28) -----------
#
# Mirrors the real shape Damien hit: a firm-1 shared row whose cascade-down cell is a pipe cell
# (one cell naming two concepts) with per-attribute cells repeating them, so the input/output
# counts do not line up and the row goes to section A. Firm 2 then names a cell with the SAME
# text and a *different*, smaller mapping. A firm-blind lookup binds the firm-1 pairing row to
# firm 2's item and shows one concept where the pipe cell put two.

FIRM1_PIPE_ROWS: list[list[str | None]] = [
    FIRM1_HEADER,
    _f1(level1="Blended Practice"),
    _f1(
        level2="Blended Finance",
        level3="Blended finance",
        code="1.0",
        sali={
            0: "Widget Manufacturing Law | Bauble Agreements",
            1: "Widget Manufacturing Law",
            2: "Bauble Agreements",
        },
    ),
]

FIRM2_PIPE_ROWS: list[list[str | None]] = [
    FIRM2_WORKTYPE_HEADER,
    # Same cell text as the firm-1 row above, one concept instead of two.
    _f2wt("Blended Kinds", "Blended finance", "Blended finance", area="Bauble Agreements"),
]
