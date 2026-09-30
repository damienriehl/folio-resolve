"""In-memory resolution and the known provider-overconfidence guard."""
from folio_resolve import Concept, InMemoryOntology, LabelResolver

ontology = InMemoryOntology([
    Concept(iri="R-delaware", label="Delaware", branch="Location"),
    Concept(iri="R-antitrust", label="Antitrust Law", branch="Area of Law"),
    Concept(iri="R-securities", label="Securities Law", branch="Area of Law"),
])
resolver = LabelResolver(search_by_label=ontology.search_by_label)
assert resolver.resolve("Delaware")[0].branch == "Location"
assert {r.iri for r in resolver.resolve("Antitrust and Securities Law")} == {
    "R-antitrust", "R-securities",
}
assert resolver.resolve("law") == []
# Reproduce the documented upstream fuzzy score; exact in-memory scoring alone
# does not exercise the resolver's 92-point acceptance bar.
def fuzzy_search(label):
    if label == "law":
        return [(ontology.get_concept("R-delaware"), 90.0)]
    return ontology.search_by_label(label)

assert LabelResolver(search_by_label=fuzzy_search).resolve("law") == [], "law accepted Delaware@90"
print("compound IRIs, branch preservation, and law/Delaware@90 guard passed")
