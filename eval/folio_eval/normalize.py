"""Label and IRI normalization helpers for gold construction (U2, KTD6, R2).

Gold labels are curator-typed spreadsheet cells: they carry NBSPs, en/em dashes where the
ontology has a hyphen, doubled interior spaces, trailing spaces, ``?`` uncertainty markers,
pipe-delimited multi-values, relational expressions, and legacy ``lmss.sali.org`` IRIs. This
module holds every one of those textual rules in one place so the resolver (``resolve_labels``)
and the derivation (``gold``) share exactly one definition of "the same string".

Nothing here touches the ontology or the pipeline under test — R2 requires gold resolution to
be independent of the system being scored.

Legacy-IRI normalization does not exist elsewhere in this repo (plan Assumptions); folio-python's
``folio/graph.py`` behavior is the reference: the opaque suffix after the last ``/`` (or after
the ``lmss:`` prefix) is the concept key, and only the namespace changes.
"""

from __future__ import annotations

import re
import unicodedata

#: The current FOLIO namespace every gold IRI is normalized into.
FOLIO_IRI_PREFIX = "https://folio.openlegalstandard.org/"

#: Namespaces seen in the workbooks that mean the same concept key as ``FOLIO_IRI_PREFIX``.
LEGACY_IRI_PREFIXES: tuple[str, ...] = (
    "http://lmss.sali.org/",
    "https://lmss.sali.org/",
    "http://www.lmss.sali.org/",
    "https://www.lmss.sali.org/",
    "lmss:",
    "sali:",
)

#: Unicode dash variants folded to ASCII ``-`` before comparison.
DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"

#: Whitespace variants folded to a plain space before collapsing.
SPACE_CHARS = "\u00a0\u2007\u202f\u2009\u200a\u2002\u2003\u2004\u2005\u2006\u2008\t\r\n"

#: Leaf labels and cell values that name no concept (KTD3, KTD6). Compared on the normalized,
#: casefolded whole string — ``Other Tax`` and ``Asset-based - Other`` are *not* excluded.
NON_REFERENTIAL_VALUES = frozenset(
    {
        "other",
        "others",
        "miscellaneous",
        "misc",
        "misc.",
        "n/a",
        "na",
        "none",
        "various",
        "varies",
        "tbd",
        "unknown",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
_DASH_RE = re.compile(f"[{re.escape(DASH_CHARS)}]")
_SPACE_RE = re.compile(f"[{re.escape(SPACE_CHARS)}]")
_ARROW_RE = re.compile(r"(->|-->|=>|→|⟶)")
_RELATION_PREFIX_RE = re.compile(r"^(?:sali|lmss|folio|skos|rdfs|owl):[A-Za-z_][A-Za-z0-9_]*$")
_IRI_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def normalize_dashes(text: str) -> str:
    """Fold every Unicode dash variant to an ASCII hyphen."""
    return _DASH_RE.sub("-", text)


def normalize_whitespace(text: str) -> str:
    """Fold exotic space characters to plain spaces, collapse runs, and strip the ends."""
    return _WHITESPACE_RE.sub(" ", _SPACE_RE.sub(" ", text)).strip()


def normalize_label(text: str) -> str:
    """NFKC, dash folding, whitespace collapse, strip — case preserved.

    This is the *comparison* form of a label. ``label_key`` adds casefolding on top; the raw
    cell text is always carried alongside so the audit gate sees what the curator typed.
    """
    return normalize_whitespace(normalize_dashes(unicodedata.normalize("NFKC", text)))


def label_key(text: str) -> str:
    """The case-insensitive lookup key for a normalized label."""
    return normalize_label(text).casefold()


def is_iri_like(text: str) -> bool:
    """True when the cell holds an IRI (current or legacy namespace) rather than a label."""
    candidate = normalize_whitespace(text)
    if candidate.startswith(FOLIO_IRI_PREFIX):
        return True
    for prefix in LEGACY_IRI_PREFIXES:
        if candidate.startswith(prefix):
            suffix = candidate[len(prefix) :]
            # ``sali:isMemberOf`` is a relation, not an IRI: relations have no ``/`` and read as
            # camelCase predicates, while concept keys are opaque alphanumeric blobs.
            return bool(suffix) and _IRI_KEY_RE.match(suffix) is not None and not _is_predicate(
                candidate
            )
    return False


def _is_predicate(candidate: str) -> bool:
    return _RELATION_PREFIX_RE.match(candidate) is not None and _looks_like_camel_predicate(
        candidate.split(":", 1)[1]
    )


def _looks_like_camel_predicate(suffix: str) -> bool:
    """``isMemberOf`` / ``hasSubsidiary`` / ``drafted`` — lowercase-initial, no digits."""
    return bool(suffix) and suffix[0].islower() and suffix.isalpha()


def normalize_iri(text: str) -> str | None:
    """Rewrite any recognized IRI form into the FOLIO namespace, or return ``None``.

    Handles the workbook's trailing-space IRIs and the ``lmss:``/``http://lmss.sali.org/``
    legacy namespaces (AE3).
    """
    if not is_iri_like(text):
        return None
    candidate = normalize_whitespace(text)
    if candidate.startswith(FOLIO_IRI_PREFIX):
        return candidate
    for prefix in LEGACY_IRI_PREFIXES:
        if candidate.startswith(prefix):
            return FOLIO_IRI_PREFIX + candidate[len(prefix) :]
    return None


def is_relational(text: str) -> bool:
    """True for relational assertions (``sali:isMemberOf``, arrow syntax) — excluded from gold."""
    candidate = normalize_whitespace(text)
    if is_iri_like(candidate):
        return False
    if _ARROW_RE.search(candidate):
        return True
    return _is_predicate(candidate)


def is_non_referential(text: str) -> bool:
    """True for placeholder values that name no concept (``Other``, ``varies``)."""
    return label_key(text) in NON_REFERENTIAL_VALUES


def strip_suspect_marker(text: str) -> tuple[str, bool]:
    """Strip a trailing ``?`` uncertainty marker; returns ``(text, was_marked)``.

    Unmarked text is returned byte-for-byte so that cells which resolve *only* after
    normalization (trailing spaces, dash variants) still reach the resolver in their raw form
    and get logged there (KTD6).
    """
    trimmed = text.rstrip()
    if trimmed.endswith("?"):
        return trimmed[:-1].rstrip(), True
    return text, False


def split_pipe_values(text: str) -> list[str]:
    """Split a pipe-delimited cell into its separate values (empties dropped)."""
    return [part.strip() for part in text.split("|") if part.strip()]


def split_compound_value(text: str) -> tuple[str | None, list[str]]:
    """Split a ``Bucket: Concept`` value into ``(bucket, ordered_right_hand_candidates)``.

    KTD6's parse order is right-hand side first, whole string second, bucket last; this returns
    the pieces and leaves the ordering to the resolver so the branch that fired is recorded.
    """
    candidate = normalize_whitespace(text)
    if ":" not in candidate:
        return None, []
    bucket = candidate.split(":", 1)[0].strip()
    rhs_first = candidate.split(":", 1)[1].strip()
    rhs_last = candidate.rsplit(":", 1)[1].strip()
    right: list[str] = []
    for part in (rhs_last, rhs_first):
        if part and part not in right:
            right.append(part)
    return (bucket or None), right


def plural_variants(text: str) -> list[str]:
    """Deterministic singular/plural variants of a label's final word.

    The lemma rung of the R2 ladder without a spaCy dependency: gold cells routinely say
    *Agreement* where the ontology says *Agreements*. Mirrors the spirit of
    ``src/folio_resolve/lemma.py`` (which needs the ``spacy`` extra) with pure string rules.
    """
    normalized = normalize_label(text)
    if not normalized:
        return []
    head, _, last = normalized.rpartition(" ")
    prefix = f"{head} " if head else ""
    out: list[str] = []

    def add(word: str) -> None:
        variant = f"{prefix}{word}"
        if variant != normalized and variant not in out:
            out.append(variant)

    lower = last.lower()
    if lower.endswith("ies") and len(last) > 3:
        add(last[:-3] + "y")
    if lower.endswith("es") and len(last) > 2:
        add(last[:-2])
    if lower.endswith("s") and not lower.endswith("ss") and len(last) > 1:
        add(last[:-1])
    if lower.endswith("y") and len(last) > 1 and last[-2].lower() not in "aeiou":
        add(last[:-1] + "ies")
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        add(last + "es")
    add(last + "s")
    return out
