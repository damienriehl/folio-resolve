"""Ontology-neutral spec layer.

Lifted from folio-enrich ``ontology/spec.py``. Matching is not hardwired to FOLIO: an
``OntologySpec`` carries where the OWL comes from (``OntologyCoords``) and the per-ontology
behavior (``OntologyBehavior``: prefix stripping, lemma denylist, concept/property exclusions,
IRI roots). Ships FOLIO_SPEC and the CatholicOS CANON_SPEC as proof the layer is real.
"""

from __future__ import annotations

from dataclasses import dataclass

# Legal pluralia-tantum whose singular/plural forms cause bad entity-ruler hits.
_FOLIO_LEMMA_DENYLIST: frozenset[str] = frozenset(
    {
        "damages", "damage", "costs", "cost", "proceedings", "proceeding", "goods", "good",
        "arms", "arm", "premises", "savings", "saving", "findings", "finding", "securities",
        "minutes", "minute", "holdings", "holding", "pleadings", "pleading", "articles",
        "article", "data", "datum", "leaves", "leave", "wills", "will", "means",
    }
)

# Branches excluded from FOLIO matching (non-substantive / structural).
_FOLIO_EXCLUDED_BRANCHES: frozenset[str] = frozenset({"Standards Compatibility"})


@dataclass(frozen=True)
class OntologyCoords:
    source_type: str  # "github" | "http"
    repo_branch: str = "main"
    owl_url: str = ""
    owl_sha256: str = ""


@dataclass(frozen=True)
class OntologyBehavior:
    prefix_strip: tuple[str, ...] = ()
    lemma_denylist: frozenset[str] = frozenset()
    concept_exclude_substrings: tuple[str, ...] = ()
    concept_exclude_prefixes: tuple[str, ...] = ()
    property_exclude_substrings: tuple[str, ...] = ()
    property_exclude_prefixes: tuple[str, ...] = ()
    excluded_branches: frozenset[str] = frozenset()
    iri_roots: tuple[str, ...] = ()

    def excludes_concept_label(self, label: str) -> bool:
        """Whether this ontology's rules exclude ``label``. Case-insensitive on both sides.

        The label was already upper-cased for comparison, but the *patterns* were not — so a
        consumer-authored spec (the point of this layer) written as
        ``concept_exclude_substrings=("dupe",)`` silently excluded nothing.
        """
        upper = label.upper()
        if any(upper.startswith(p.upper()) for p in self.concept_exclude_prefixes):
            return True
        return any(sub.upper() in upper for sub in self.concept_exclude_substrings)


@dataclass(frozen=True)
class OntologySpec:
    id: str
    display_name: str
    base_iri: str
    coords: OntologyCoords
    behavior: OntologyBehavior
    min_label_coverage: float | None = None


FOLIO_SPEC = OntologySpec(
    id="folio",
    display_name="FOLIO",
    base_iri="https://folio.openlegalstandard.org/",
    coords=OntologyCoords(source_type="github", repo_branch="main"),
    behavior=OntologyBehavior(
        prefix_strip=("folio:", "utbms:", "oasis:"),
        lemma_denylist=_FOLIO_LEMMA_DENYLIST,
        concept_exclude_substrings=("DUPE",),
        concept_exclude_prefixes=("ZZZ:",),
        property_exclude_substrings=("DEPRECATED",),
        property_exclude_prefixes=("ZZZ:",),
        excluded_branches=_FOLIO_EXCLUDED_BRANCHES,
        iri_roots=("https://folio.openlegalstandard.org/",),
    ),
)

CANON_SPEC = OntologySpec(
    id="canon",
    display_name="Catholic Semantic Canon",
    base_iri="https://ontology.catholicos.catholic/",
    coords=OntologyCoords(
        source_type="http",
        owl_url="https://raw.githubusercontent.com/CatholicOS/ontology-semantic-canon/main/sources/ontology-semantic-canon.owl",
        owl_sha256="add8b2b140273b197b759f8945b4f5aa66ecb1ec801fcc69431f1b4baaf59f24",
    ),
    behavior=OntologyBehavior(
        concept_exclude_substrings=("DUPE",),
        concept_exclude_prefixes=("ZZZ",),
        property_exclude_substrings=("DEPRECATED",),
        property_exclude_prefixes=("ZZZ",),
        iri_roots=(
            "https://ontology.catholicos.catholic/",
            "http://webprotege.stanford.edu/",
        ),
    ),
    min_label_coverage=99.0,
)

BUILTIN_SPECS: dict[str, OntologySpec] = {FOLIO_SPEC.id: FOLIO_SPEC, CANON_SPEC.id: CANON_SPEC}


def get_spec(spec_id: str) -> OntologySpec:
    spec = BUILTIN_SPECS.get(spec_id)
    if spec is None:
        raise KeyError(f"Unknown ontology spec: {spec_id!r}. Known: {sorted(BUILTIN_SPECS)}")
    return spec
