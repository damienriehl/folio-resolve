"""Proposed gold improvements (pilot): decompose a molecule cell into its atom tags.

Damien's first six adjudications all corrected the same shape of mistake. A curator wrote one
compound practice label — a *molecule* — and mapped it to one or two Area-of-Law concepts. His
corrections did two things every time:

1. **Per-cell atomic mapping.** The cell means what the cell says, and nothing more. A child cell
   never inherits its parent's meaning ("Borrower" is *Borrower*, not *Finance and Lending Law*).
2. **Decompose the molecule into atoms.** The implicit industry / asset / player / practice tag
   that the molecule names but the curator left off gets added. A vessel-financing label is not
   only *Finance and Lending Law*, it is also *Transportation and Logistics Industry* and *Ship*;
   a bank-side counsel role is *Bank* + *Lawyer*; a public-issuer label gains *Public Company*.

Those atoms are not scattered at random through FOLIO — they sit in a handful of the ontology's
26 top-level branches (``Actor / Player``, ``Asset Type``, ``Industry and Market``, ``Service``,
``Legal Entity``, ``Area of Law``). This module proposes, for an un-reviewed input cell, the atom
tags its own words name, by two routes:

* **direct search** — the cell's noun phrases looked up against FOLIO labels, keeping only hits
  that sit in an atom branch and whose label is plausibly what the phrase says;
* **few-shot anchors** — the trigger→atom pairs read off Damien's own six rulings, so the pattern
  he demonstrated generalizes to the rest of the same practice family.

Every proposal is *machine-proposed* and enters the sheet as a question, never as gold. Nothing
here touches the pipeline under test (R2): proposals resolve through the same offline
:class:`~folio_eval.resolve_labels.LabelIndex` gold itself resolves through.

The anchor table names FOLIO concepts and ordinary English trigger words — no firm surface string
(KTD1) — so it is safe to commit. The *cells* it runs over are firm surfaces and stay in the
gitignored packet.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .normalize import FOLIO_IRI_PREFIX, label_key, normalize_label
from .resolve_labels import LabelIndex, resolve_label

#: FOLIO top-level branches an *atom* can live in. ``Area of Law`` is included because a molecule
#: often names a second area the curator did not write, but it is the branch curators already
#: reach for, so it ranks last.
ATOM_BRANCHES: tuple[str, ...] = (
    "Actor / Player",
    "Asset Type",
    "Industry and Market",
    "Service",
    "Legal Entity",
    "Objectives",
    "Area of Law",
)

#: Rank order for display and for the per-item cap: the branches Damien *added* come first, the
#: branch curators already use comes last.
_BRANCH_RANK: Mapping[str, int] = {name: position for position, name in enumerate(ATOM_BRANCHES)}

#: Words that carry no atom on their own; searching them returns noise (place names, generic
#: nouns that match hundreds of labels).
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "the", "of", "or", "for", "to", "in", "on", "by", "with", "non",
        "other", "others", "general", "generic", "misc", "miscellaneous", "various", "type",
        "types", "matter", "matters", "work", "issue", "issues", "related", "advice", "advisory",
        "services", "service", "law", "legal", "practice", "group", "team", "level", "new",
        "all", "any", "including", "include", "etc", "e.g.", "i.e.", "structured", "securitized",
        "based", "out", "court", "special", "up", "down", "re", "sub",
        # Taxonomy scaffolding: words a curator uses to organize a column, not to name a concept.
        # Searched, they head hundreds of unrelated labels ("Asset" -> Automobile Asset).
        "asset", "assets", "category", "categories", "class", "classes", "kind", "kinds",
        "item", "items", "name", "names", "code", "codes", "attribute", "attributes",
    }
)

#: Damien's six rulings, read as trigger → atom concepts. The right-hand side is a FOLIO
#: *preferred or alternative label*; anything that does not resolve against the loaded ontology is
#: dropped, so a FOLIO version bump degrades this table quietly instead of proposing ghosts.
ANCHOR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ship", ("Ship", "Transportation and Logistics Industry")),
    ("vessel", ("Ship", "Transportation and Logistics Industry")),
    ("maritime", ("Ship", "Transportation and Logistics Industry")),
    ("shipping", ("Ship", "Transportation and Logistics Industry")),
    ("aviation", ("Aircraft", "Transportation and Logistics Industry")),
    ("aircraft", ("Aircraft", "Transportation and Logistics Industry")),
    ("bank", ("Bank", "Finance and Insurance Services Industry")),
    ("banking", ("Bank", "Finance and Insurance Services Industry")),
    ("counsel", ("Lawyer",)),
    ("lawyer", ("Lawyer",)),
    ("attorney", ("Lawyer",)),
    ("borrower", ("Borrower",)),
    ("lender", ("Lender",)),
    ("guarantor", ("Guarantor",)),
    ("trustee", ("Trustee",)),
    ("receiver", ("Receiver",)),
    ("receivership", ("Receiver",)),
    ("workout", ("Out-of-Court Restructuring",)),
    ("restructuring", ("Out-of-Court Restructuring",)),
    ("insolvency", ("Insolvency",)),
    ("public", ("Public Company",)),
    ("municipal", ("Municipality",)),
    ("sovereign", ("Government",)),
    ("insurance", ("Insurance Law", "Finance and Insurance Services Industry")),
    ("insurer", ("Insurance Law", "Finance and Insurance Services Industry")),
    ("reinsurance", ("Insurance Law", "Finance and Insurance Services Industry")),
    ("finance", ("Finance and Lending Law", "Financing Practice")),
    ("financing", ("Finance and Lending Law", "Financing Practice")),
    ("lending", ("Finance and Lending Law", "Financing Practice")),
    ("loan", ("Finance and Lending Law", "Financing Practice")),
    ("loans", ("Finance and Lending Law", "Financing Practice")),
    ("credit", ("Finance and Lending Law", "Financing Practice")),
    ("debt", ("Finance and Lending Law", "Financing Practice")),
    ("mortgage", ("Real Estate Industry", "Real Estate Asset")),
    ("estate", ("Real Estate Industry", "Real Estate Asset")),
    ("property", ("Real Estate Industry", "Real Estate Asset")),
    ("real", ("Real Estate Industry", "Real Estate Asset")),
    ("commercial", ("Commercial Transactions Law",)),
    ("energy", ("Energy Industry",)),
    ("project", ("Financing Practice",)),
    ("fund", ("Investment Fund",)),
    ("equity", ("Equity Security",)),
    ("derivative", ("Derivative",)),
    ("derivatives", ("Derivative",)),
    ("tax", ("Tax Law",)),
    ("islamic", ("Islamic Law System",)),
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'&.\-]*")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"[/,;:|]+")


@dataclass(frozen=True, slots=True)
class BranchIndex:
    """Every concept's FOLIO top-level branch, walked once from the class hierarchy."""

    branch_by_iri: Mapping[str, str]

    def branch_of(self, iri: str) -> str:
        return self.branch_by_iri.get(iri, "")

    def is_atom(self, iri: str) -> bool:
        return self.branch_of(iri) in _BRANCH_RANK


def build_branch_index(
    parents: Mapping[str, Sequence[str]], labels: Mapping[str, str]
) -> BranchIndex:
    """Resolve each concept to the root of its ``parent_iris`` chain.

    Cycles and multi-parent classes are handled the way the rest of this harness handles
    non-determinism: the lexicographically smallest parent wins, and a cycle stops the walk.
    """
    branch: dict[str, str] = {}
    for iri in parents:
        seen: set[str] = set()
        current = iri
        while True:
            if current in seen:
                break
            seen.add(current)
            up = sorted(parent for parent in parents.get(current, ()) if parent in parents)
            if not up:
                break
            current = up[0]
        branch[iri] = labels.get(current, "")
    return BranchIndex(branch_by_iri=branch)


def branch_index_from_folio(folio: object) -> BranchIndex:
    """Build the branch index from a constructed ``folio.FOLIO`` graph.

    Restricted to the FOLIO namespace, exactly as
    :func:`~folio_eval.resolve_labels.index_from_folio` is: the graph also carries unlabelled
    OWL scaffolding, and walking into it strands every concept on a nameless root.
    """
    parents: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for owl in getattr(folio, "classes", []):
        iri = str(getattr(owl, "iri", "") or "")
        if FOLIO_IRI_PREFIX not in iri:
            continue
        labels[iri] = str(getattr(owl, "label", "") or getattr(owl, "preferred_label", "") or "")
        raw = getattr(owl, "parent_iris", None) or getattr(owl, "sub_class_of", None) or []
        parents[iri] = [
            str(parent)
            for parent in raw
            if isinstance(parent, str) and FOLIO_IRI_PREFIX in parent
        ]
    return build_branch_index(parents, labels)


def noun_phrases(text: str) -> list[str]:
    """The cell's own content phrases: parentheticals dropped, 1- and 2-grams, stopwords out.

    Order is longest-first so a two-word phrase (*real estate*) is searched before either word on
    its own, which is what keeps *Real Estate Industry* ahead of *Estate Planning*.
    """
    cleaned = _PARENTHETICAL_RE.sub(" ", normalize_label(text))
    phrases: list[str] = []

    def add(candidate: str) -> None:
        value = candidate.strip()
        if value and value not in phrases:
            phrases.append(value)

    for chunk in _SPLIT_RE.split(cleaned):
        words = [word for word in _WORD_RE.findall(chunk)]
        content = [word for word in words if word.casefold() not in STOPWORDS and len(word) > 2]
        if len(words) > 1:
            add(" ".join(words))
        for position in range(len(words) - 1):
            pair = words[position : position + 2]
            if any(word.casefold() not in STOPWORDS for word in pair):
                add(" ".join(pair))
        for word in content:
            add(word)
    return phrases


@dataclass(frozen=True, slots=True)
class AtomProposal:
    """One machine-proposed atom tag for one input cell."""

    iri: str
    label: str
    branch: str
    method: str
    query: str
    score: float
    definition: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "iri": self.iri,
            "label": self.label,
            "branch": self.branch,
            "method": self.method,
            "query": self.query,
            "score": round(self.score, 4),
            "definition": self.definition,
            "machine_proposed": True,
        }


#: ``(query, limit) -> [(iri, label, score_0_100)]`` — the ontology's own label search, injected
#: so tests never need the live graph.
SearchFn = Callable[[str, int], Sequence[tuple[str, str, float]]]

#: A search hit whose label shares no content word with the query is a place-name-style accident
#: (``Finance`` → *Lao People's Democratic Republic* at 90 in the real baseline). Reuse the same
#: shape of guard the resolution batch uses.
MIN_SEARCH_SCORE = 90.0

#: The atom Damien named was always the *head* of a short concept name — *Ship*, *Bank*,
#: *Public Company*, *Real Estate Industry*. The search route is held to that: the phrase has to
#: open or close the label, not merely appear somewhere inside it, or the pilot fills up with
#: clause-shaped concepts (*Waiver of Bond Provision* for "Bond").
MAX_SEARCH_LABEL_WORDS = 4

#: ``Objectives`` reaches the sheet only through an anchor. Searched, it is where FOLIO keeps
#: clause and provision names, and it swamps everything else.
SEARCH_BRANCHES: frozenset[str] = frozenset(ATOM_BRANCHES) - {"Objectives"}


def _shares_a_word(query: str, label: str) -> bool:
    left = {word.casefold() for word in _WORD_RE.findall(query)} - STOPWORDS
    right = {word.casefold() for word in _WORD_RE.findall(label)} - STOPWORDS
    return bool(left & right)


def _heads_or_tails(query: str, label: str) -> bool:
    """True when the query phrase opens or closes the label, on whole words."""
    left = [word.casefold() for word in _WORD_RE.findall(query)]
    right = [word.casefold() for word in _WORD_RE.findall(label)]
    if not left or not right or len(right) > MAX_SEARCH_LABEL_WORDS:
        return False
    return right[: len(left)] == left or right[-len(left) :] == left


def anchor_atoms(text: str, index: LabelIndex) -> list[tuple[str, str, str]]:
    """``(iri, label, trigger)`` for every anchor rule the cell's words fire, in table order."""
    words = {word.casefold() for word in _WORD_RE.findall(normalize_label(text))}
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for trigger, concepts in ANCHOR_RULES:
        if trigger not in words:
            continue
        for name in concepts:
            resolution = resolve_label(name, index)
            if resolution.iri is None or resolution.iri in seen:
                continue
            seen.add(resolution.iri)
            out.append((resolution.iri, name, trigger))
    return out


def propose_atoms(
    text: str,
    *,
    index: LabelIndex,
    branches: BranchIndex,
    search: SearchFn | None = None,
    gold_iris: Iterable[str] = (),
    definitions: Mapping[str, str] | None = None,
    limit: int = 6,
) -> tuple[AtomProposal, ...]:
    """Propose the atom tags one input cell names but its gold does not yet carry.

    Anchors first (Damien's demonstrated pattern), then direct label search over the cell's noun
    phrases. Concepts already in the cell's gold are never re-proposed, and only concepts sitting
    in an atom branch survive — a proposal has to be an industry, an asset, a player, a practice,
    an entity, an objective, or an area of law.
    """
    already = {str(iri) for iri in gold_iris}
    definitions = definitions or {}
    found: dict[str, AtomProposal] = {}

    for iri, label, trigger in anchor_atoms(text, index):
        if iri in already or iri in found or not branches.is_atom(iri):
            continue
        found[iri] = AtomProposal(
            iri=iri,
            label=index.label_for(iri) or label,
            branch=branches.branch_of(iri),
            method="anchor",
            query=trigger,
            score=100.0,
            definition=definitions.get(iri, ""),
        )

    if search is not None:
        seen_labels = {label_key(proposal.label) for proposal in found.values()}
        for phrase in noun_phrases(text):
            for iri, label, score in search(phrase, 10):
                if iri in already or iri in found:
                    continue
                if branches.branch_of(iri) not in SEARCH_BRANCHES:
                    continue
                if score < MIN_SEARCH_SCORE or not _shares_a_word(phrase, label):
                    continue
                if not _heads_or_tails(phrase, label):
                    continue
                # Two concepts can share a label; showing the name twice reads as a duplicate.
                if label_key(label) in seen_labels:
                    continue
                seen_labels.add(label_key(label))
                found[iri] = AtomProposal(
                    iri=iri,
                    label=index.label_for(iri) or label,
                    branch=branches.branch_of(iri),
                    method="search",
                    query=phrase,
                    score=float(score),
                    definition=definitions.get(iri, ""),
                )

    ordered = sorted(
        found.values(),
        key=lambda proposal: (
            0 if proposal.method == "anchor" else 1,
            _BRANCH_RANK.get(proposal.branch, len(_BRANCH_RANK)),
            -proposal.score,
            label_key(proposal.label),
        ),
    )
    return tuple(ordered[:limit])
