"""Ontology-neutral spec layer — coords, per-ontology behavior, and the built-in specs.

Matching is not hardwired to FOLIO: an ``OntologySpec`` carries where the OWL comes from and how
that ontology behaves (prefix stripping, lemma denylist, concept/property exclusions, IRI roots).
The consumer-authored spec is the point of the layer, so its rules are tested as a public API.
"""

from __future__ import annotations

import pytest

from folio_resolve import BUILTIN_SPECS, CANON_SPEC, FOLIO_SPEC, OntologySpec
from folio_resolve.spec import OntologyBehavior, OntologyCoords, get_spec

# -- get_spec ------------------------------------------------------------


def test_get_spec_returns_the_builtin_specs() -> None:
    assert get_spec("folio") is FOLIO_SPEC
    assert get_spec("canon") is CANON_SPEC


def test_get_spec_names_the_known_ids_when_it_fails() -> None:
    with pytest.raises(KeyError) as exc:
        get_spec("nope")
    assert "canon" in str(exc.value) and "folio" in str(exc.value)


def test_the_builtin_registry_is_keyed_by_spec_id() -> None:
    assert BUILTIN_SPECS == {"folio": FOLIO_SPEC, "canon": CANON_SPEC}
    assert all(spec_id == spec.id for spec_id, spec in BUILTIN_SPECS.items())


# -- concept-label exclusion ---------------------------------------------


def test_folio_excludes_its_marked_labels() -> None:
    behavior = FOLIO_SPEC.behavior
    assert behavior.excludes_concept_label("ZZZ: Retired Concept")  # prefix rule
    assert behavior.excludes_concept_label("Arbitration DUPE")  # substring rule
    assert not behavior.excludes_concept_label("Arbitration Rules")


def test_exclusion_is_case_insensitive_on_the_label() -> None:
    assert FOLIO_SPEC.behavior.excludes_concept_label("zzz: retired")
    assert FOLIO_SPEC.behavior.excludes_concept_label("arbitration dupe")


def test_exclusion_is_case_insensitive_on_the_authored_pattern() -> None:
    """Regression: only the *label* was upper-cased, not the patterns.

    A consumer-authored spec — the entire point of this layer — written with lowercase rules
    silently excluded nothing at all.
    """
    behavior = OntologyBehavior(
        concept_exclude_prefixes=("zzz:",), concept_exclude_substrings=("dupe",)
    )
    assert behavior.excludes_concept_label("ZZZ: Retired Concept")
    assert behavior.excludes_concept_label("Arbitration DUPE")
    assert behavior.excludes_concept_label("zzz: retired concept")
    assert not behavior.excludes_concept_label("Arbitration Rules")


def test_a_prefix_rule_only_matches_at_the_start() -> None:
    behavior = OntologyBehavior(concept_exclude_prefixes=("ZZZ:",))
    assert behavior.excludes_concept_label("ZZZ: Retired")
    assert not behavior.excludes_concept_label("Retired ZZZ: Concept")


def test_a_behavior_with_no_rules_excludes_nothing() -> None:
    behavior = OntologyBehavior()
    assert not behavior.excludes_concept_label("ZZZ: anything DUPE")
    assert not behavior.excludes_concept_label("")


# -- the FOLIO spec ------------------------------------------------------


def test_folio_spec_shape() -> None:
    assert FOLIO_SPEC.id == "folio"
    assert FOLIO_SPEC.display_name == "FOLIO"
    assert FOLIO_SPEC.base_iri == "https://folio.openlegalstandard.org/"
    assert FOLIO_SPEC.coords.source_type == "github"
    assert FOLIO_SPEC.coords.repo_branch == "main"
    assert FOLIO_SPEC.base_iri in FOLIO_SPEC.behavior.iri_roots
    assert FOLIO_SPEC.min_label_coverage is None


def test_folio_strips_the_vocabulary_prefixes_its_labels_carry() -> None:
    assert FOLIO_SPEC.behavior.prefix_strip == ("folio:", "utbms:", "oasis:")


def test_folio_excludes_the_structural_branch() -> None:
    assert FOLIO_SPEC.behavior.excluded_branches == frozenset({"Standards Compatibility"})


def test_the_lemma_denylist_carries_the_legal_pluralia_tantum() -> None:
    denylist = FOLIO_SPEC.behavior.lemma_denylist
    # Both members of each pair: the plural surface AND the singular a lemmatizer would produce.
    for plural, singular in [
        ("damages", "damage"),
        ("proceedings", "proceeding"),
        ("wills", "will"),
        ("findings", "finding"),
        ("pleadings", "pleading"),
    ]:
        assert plural in denylist and singular in denylist, plural
    assert "securities" in denylist
    assert "arbitration" not in denylist  # ordinary terms stay lemmatizable


def test_the_lemma_denylist_is_all_lowercase() -> None:
    # It is consulted against lowercased label keys in folio_resolve.lemma.
    assert all(w == w.lower() for w in FOLIO_SPEC.behavior.lemma_denylist)


# -- the CatholicOS canon spec (proof the layer is not FOLIO-only) -------


def test_canon_spec_is_an_http_sourced_ontology_with_a_pinned_digest() -> None:
    assert CANON_SPEC.id == "canon"
    assert CANON_SPEC.coords.source_type == "http"
    assert CANON_SPEC.coords.owl_url.startswith("https://")
    assert len(CANON_SPEC.coords.owl_sha256) == 64
    assert CANON_SPEC.min_label_coverage == 99.0


def test_canon_carries_a_second_iri_root_for_its_webprotege_terms() -> None:
    assert CANON_SPEC.base_iri in CANON_SPEC.behavior.iri_roots
    assert len(CANON_SPEC.behavior.iri_roots) == 2


def test_canon_declares_no_lemma_denylist_or_prefix_stripping() -> None:
    # Per-ontology behavior really is per-ontology, not a copy of FOLIO's.
    assert CANON_SPEC.behavior.lemma_denylist == frozenset()
    assert CANON_SPEC.behavior.prefix_strip == ()


# -- immutability --------------------------------------------------------


def test_specs_are_frozen() -> None:
    for obj in (FOLIO_SPEC, FOLIO_SPEC.behavior, FOLIO_SPEC.coords):
        with pytest.raises(AttributeError):
            obj.id = "mutated"  # type: ignore[misc]


def test_a_consumer_can_author_its_own_spec() -> None:
    spec = OntologySpec(
        id="example",
        display_name="Example Ontology",
        base_iri="https://example.test/",
        coords=OntologyCoords(source_type="http", owl_url="https://example.test/o.owl"),
        behavior=OntologyBehavior(
            lemma_denylist=frozenset({"minutes"}),
            concept_exclude_substrings=("obsolete",),
            iri_roots=("https://example.test/",),
        ),
    )
    assert spec.behavior.excludes_concept_label("Obsolete Term")
    assert "minutes" in spec.behavior.lemma_denylist
    assert spec.id not in BUILTIN_SPECS  # authoring one does not register it globally
