"""Gold-set construction from the derived firm sheets (U2; R1, R2, R3; KTD1, KTD3, KTD6).

The derivation spec lives in KTD6 and is implemented here in three separable stages so each is
testable on its own:

1. **Parse** (:func:`parse_firm1`, :func:`parse_firm2`) — turn derived sheet rows into
   :class:`RawItem`s keyed per KTD3 ``(firm, term set / practice group, ancestor path, leaf)``.
   The cascade sheet's three heading tiers become the ancestor path, and a Level-3 row's gold
   candidates are the union of its own cells, its Level-2's cells, and its Level-1's cells --
   column position ignored. Pure string work: no ontology, no I/O.
2. **Resolve** (:func:`build_gold`) — push every candidate value through the R2 ladder in
   ``resolve_labels``, recording which rung fired, routing residuals to the resolution batch and
   uncertain rows to the suspects queue. Deduping and per-stratum counting happen here.
3. **Emit** (:func:`write_gold`) — versioned, deterministic artifacts under the gitignored
   ``eval/data/gold/`` tree, plus the worked-examples spec sheet the audit gate needs.

Everything this module writes is gitignored (KTD1): gold rows carry firm surface strings, so no
output of this module may ever be committed, and the CLI prints counts only.

Run it end-to-end with ``uv run python -m folio_eval.gold``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .intake import DEFAULT_DATA_DIR, DEFAULT_MANIFEST_PATH, Row, load_sheet_rows, read_manifest
from .normalize import (
    is_non_referential,
    is_relational,
    normalize_whitespace,
    split_pipe_values,
    strip_suspect_marker,
)
from .resolve_labels import LabelIndex, load_folio_index, resolve_gold_value

#: Bumped when the derivation spec changes; corrections accepted at the audit gate bump it too
#: (R3). Filenames carry it, and every report cites the version it was measured against.
GOLD_VERSION = 1

_LEVEL2_RE = re.compile(r"^Level\s*2\b", re.IGNORECASE)
_LEVEL3_RE = re.compile(r"^Level\s*3\b", re.IGNORECASE)
_SALI_COL_RE = re.compile(r"^SALI\s+(\d+)\b", re.IGNORECASE)
_SALI_NOTES_RE = re.compile(r"^SALI\s+NOTES\b", re.IGNORECASE)
_TERM_LEVEL_RE = re.compile(r"^Level\s+(\d+)\s+Term$", re.IGNORECASE)
_SALI_MAPPING_RE = re.compile(r"^SALI Mapping\b", re.IGNORECASE)
_ADDITIONAL_MAPPING_RE = re.compile(r"^Additional SALI Mapping$", re.IGNORECASE)
_SALI_IRI_RE = re.compile(r"^SALI IRI$", re.IGNORECASE)

#: KTD6's low-confidence pre-flag: curator notes that admit doubt lead the first audit batch and
#: are kept out of the frozen slice (KTD4). ``ask`` is word-anchored so ``task``/``basket`` do
#: not trip it.
_NOTES_FLAG_RE = re.compile(r"\?|discuss|\bask|deprecat", re.IGNORECASE)


# --------------------------------------------------------------------------------------
# Parse stage
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawValue:
    """One gold candidate value as it appeared in the sheet, before resolution."""

    text: str
    origin: str  # own | level2 | level1 | <firm-2 mapping column role>
    column: str
    compound: bool = False
    from_pipe: bool = False
    #: True when the value sat on a row that *also* named a Level-3 leaf. KTD6 reads such a row
    #: as the Level-2's mapping, so it cascades -- but it is the cascade most likely to carry a
    #: sibling's own attribute, so items that inherit one are marked for the audit gate.
    shared_row: bool = False


@dataclass(frozen=True, slots=True)
class RawItem:
    """A scoring item (KTD3 key) with its unresolved gold candidates."""

    firm: str
    stratum: str
    ancestor_path: tuple[str, ...]
    leaf: str
    values: tuple[RawValue, ...]
    notes: str | None = None
    flags: tuple[str, ...] = ()
    source_rows: tuple[int, ...] = ()


@dataclass
class ParseResult:
    """Items plus the parse-stage exclusion bookkeeping for one sheet."""

    items: list[RawItem] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    excluded: list[dict[str, object]] = field(default_factory=list)


def _text(cell: object) -> str:
    return normalize_whitespace(cell) if isinstance(cell, str) else ""


def _header_texts(rows: Sequence[Row]) -> list[str]:
    return [_text(cell) for cell in rows[0]] if rows else []


def _values_from_cells(
    row: Sequence[object],
    columns: Sequence[tuple[int, str]],
    *,
    origin: str,
    counts: Counter[str],
    compound: bool = False,
    shared_row: bool = False,
) -> list[RawValue]:
    """Split every populated cell into one or more :class:`RawValue`s (pipe cells split, KTD6).

    Cell text is carried *raw* (trailing spaces and dash variants intact) so the resolver can
    report which values resolved only after normalization; only pipe-split parts are trimmed.
    """
    out: list[RawValue] = []
    for index, name in columns:
        raw_cell = row[index] if index < len(row) else None
        cell = raw_cell if isinstance(raw_cell, str) and raw_cell.strip() else ""
        if not cell:
            continue
        parts = split_pipe_values(cell) if "|" in cell else [cell]
        if len(parts) > 1:
            counts["pipe_split_cells"] += 1
        for part in parts:
            out.append(
                RawValue(
                    text=part,
                    origin=origin,
                    column=name,
                    compound=compound,
                    from_pipe=len(parts) > 1,
                    shared_row=shared_row,
                )
            )
    return out


def parse_firm1(rows: Sequence[Row], *, firm: str = "firm1") -> ParseResult:
    """Parse the cascade sheet: one scoring item per Level-3 row, gold cascading down (KTD6).

    The sheet's Level-1 column doubles as a code column, so a row is treated as a Level-1
    practice-group header only when it carries no Level-2 label; a code sitting in the Level-1
    column of a Level-2 row is ignored (and counted), and a code sitting in the Level-3 column
    of a Level-1 header row likewise.
    """
    result = ParseResult()
    header = _header_texts(rows)
    level2_col = next((i for i, h in enumerate(header) if _LEVEL2_RE.match(h)), 1)
    level3_col = next((i for i, h in enumerate(header) if _LEVEL3_RE.match(h)), 2)
    level1_col = max(level2_col - 1, 0)
    sali_cols = [(i, h) for i, h in enumerate(header) if _SALI_COL_RE.match(h)]
    notes_col = next((i for i, h in enumerate(header) if _SALI_NOTES_RE.match(h)), None)

    level1: str = ""
    level1_values: list[RawValue] = []
    level2: str = ""
    level2_values: list[RawValue] = []

    for offset, row in enumerate(rows[1:], start=2):
        col1 = _text(row[level1_col]) if level1_col < len(row) else ""
        col2 = _text(row[level2_col]) if level2_col < len(row) else ""
        col3 = _text(row[level3_col]) if level3_col < len(row) else ""
        notes = _text(row[notes_col]) if notes_col is not None and notes_col < len(row) else ""

        if col1 and not col2:
            level1 = col1
            level1_values = _values_from_cells(row, sali_cols, origin="level1", counts=result.counts)
            level2, level2_values = "", []
            result.counts["level1_rows"] += 1
            if col3:
                result.counts["level1_row_with_level3_cell"] += 1
            continue

        if col2:
            if col1:
                result.counts["code_in_level1_column"] += 1
            level2 = col2
            level2_values = _values_from_cells(
                row,
                sali_cols,
                origin="level2",
                counts=result.counts,
                shared_row=bool(col3),
            )
            result.counts["level2_rows"] += 1
            if not col3:
                continue
            # A row carrying both L2 and L3 labels is the L2's mapping cascading to all its
            # children (KTD6) -- so this row's own item inherits, and owns nothing.
            result.counts["level2_level3_same_row"] += 1
            item_values: list[RawValue] = []
            flags: tuple[str, ...] = ("level2_level3_same_row",)
        elif col3:
            item_values = _values_from_cells(row, sali_cols, origin="own", counts=result.counts)
            flags = ()
        else:
            trailing = _values_from_cells(
                row, sali_cols, origin="level2" if level2 else "level1", counts=result.counts
            )
            if trailing:
                # A values-only row continues the currently open heading's mapping.
                result.counts["continuation_rows"] += 1
                (level2_values if level2 else level1_values).extend(trailing)
            else:
                result.counts["empty_rows"] += 1
            continue

        result.counts["level3_rows"] += 1
        if is_non_referential(col3):
            result.counts["excluded_non_referential_leaf"] += 1
            result.excluded.append(
                {
                    "reason": "non_referential_leaf",
                    "firm": firm,
                    "stratum": level1,
                    "ancestor_path": [p for p in (level1, level2) if p],
                    "leaf": col3,
                    "row": offset,
                }
            )
            continue

        if notes and _NOTES_FLAG_RE.search(notes):
            flags = (*flags, "notes_flagged")
            result.counts["notes_flagged_rows"] += 1

        result.items.append(
            RawItem(
                firm=firm,
                stratum=level1 or "(unassigned)",
                ancestor_path=tuple(p for p in (level1, level2) if p),
                leaf=col3,
                values=tuple([*item_values, *level2_values, *level1_values]),
                notes=notes or None,
                flags=flags,
                source_rows=(offset,),
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class _Firm2Columns:
    term_set: int | None
    description: int | None
    levels: tuple[int, ...]
    mappings: tuple[tuple[int, str], ...]
    additional: tuple[int, ...]
    iris: tuple[int, ...]
    deprecated: int | None
    compound: bool


def _firm2_columns(header: Sequence[str]) -> _Firm2Columns:
    levels: list[tuple[int, int]] = []
    mappings: list[tuple[int, str]] = []
    additional: list[int] = []
    iris: list[int] = []
    term_set = description = deprecated = None
    for index, name in enumerate(header):
        level_match = _TERM_LEVEL_RE.match(name)
        if level_match:
            levels.append((int(level_match.group(1)), index))
        elif _ADDITIONAL_MAPPING_RE.match(name):
            additional.append(index)
        elif _SALI_IRI_RE.match(name):
            iris.append(index)
        elif _SALI_MAPPING_RE.match(name):
            mappings.append((index, name))
        elif name.casefold() == "term set name":
            term_set = index
        elif name.casefold() == "term description":
            description = index
        elif name.casefold() == "term depreciated":
            deprecated = index
    return _Firm2Columns(
        term_set=term_set,
        description=description,
        levels=tuple(index for _, index in sorted(levels)),
        mappings=tuple(mappings),
        additional=tuple(additional),
        iris=tuple(iris),
        deprecated=deprecated,
        # The Work Type sheet is the one that splits its mapping across role-qualified columns
        # and writes compound ``Bucket: Concept`` values (KTD6).
        compound=len(mappings) > 1 or any(":" in name for _, name in mappings),
    )


def parse_firm2(rows: Sequence[Row], *, firm: str = "firm2") -> ParseResult:
    """Parse one SharePoint-style term-set sheet; rows sharing a term key union into one item."""
    result = ParseResult()
    header = _header_texts(rows)
    cols = _firm2_columns(header)

    stratum = ""
    grouped: dict[tuple[str, ...], list[tuple[int, list[RawValue], str, tuple[str, ...]]]] = (
        defaultdict(list)
    )
    order: list[tuple[str, ...]] = []

    for offset, row in enumerate(rows[1:], start=2):
        if cols.term_set is not None and cols.term_set < len(row):
            stratum = _text(row[cols.term_set]) or stratum
        path = tuple(
            value
            for value in (_text(row[i]) if i < len(row) else "" for i in cols.levels)
            if value
        )
        if not path and cols.description is not None and cols.description < len(row):
            fallback = _text(row[cols.description])
            path = (fallback,) if fallback else ()
        if not path:
            result.counts["rows_without_term_path"] += 1
            continue

        values = _values_from_cells(
            row, cols.mappings, origin="mapping", counts=result.counts, compound=cols.compound
        )
        values += _values_from_cells(
            row,
            tuple((i, header[i]) for i in cols.iris),
            origin="iri",
            counts=result.counts,
        )
        notes_parts = [
            _text(row[i]) for i in cols.additional if i < len(row) and _text(row[i])
        ]
        flags: tuple[str, ...] = ()
        if notes_parts:
            # 'Additional SALI Mapping' is notes-not-gold, promotable at the audit gate (KTD6).
            result.counts["additional_mapping_notes"] += 1
            flags = (*flags, "has_additional_mapping")
        if (
            cols.deprecated is not None
            and cols.deprecated < len(row)
            and _text(row[cols.deprecated]).casefold() in {"y", "yes", "true"}
        ):
            flags = (*flags, "term_deprecated")
            result.counts["term_deprecated_rows"] += 1

        if path not in grouped:
            order.append(path)
        grouped[path].append((offset, values, " | ".join(notes_parts), flags))
        result.counts["term_rows"] += 1

    for path in order:
        entries = grouped[path]
        if len(entries) > 1:
            result.counts["multi_row_union"] += len(entries) - 1
        leaf = path[-1]
        ancestors = path[:-1]
        if is_non_referential(leaf):
            result.counts["excluded_non_referential_leaf"] += 1
            result.excluded.append(
                {
                    "reason": "non_referential_leaf",
                    "firm": firm,
                    "stratum": stratum,
                    "ancestor_path": list(ancestors),
                    "leaf": leaf,
                    "row": entries[0][0],
                }
            )
            continue
        values = [value for _, entry_values, _, _ in entries for value in entry_values]
        notes = " | ".join(note for _, _, note, _ in entries if note)
        flags = tuple(dict.fromkeys(flag for _, _, _, entry_flags in entries for flag in entry_flags))
        if len(entries) > 1:
            flags = (*flags, "multi_row_union")
        result.items.append(
            RawItem(
                firm=firm,
                stratum=stratum or "(unassigned)",
                ancestor_path=ancestors,
                leaf=leaf,
                values=tuple(values),
                notes=notes or None,
                flags=flags,
                source_rows=tuple(offset for offset, _, _, _ in entries),
            )
        )
    return result


# --------------------------------------------------------------------------------------
# Resolve stage
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldValueRecord:
    """One resolved gold value, with the provenance the audit gate needs."""

    raw: str
    iri: str
    origin: str
    column: str
    branch: str
    parse_branch: str
    ambiguous: bool = False
    suspect: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "iri": self.iri,
            "origin": self.origin,
            "column": self.column,
            "branch": self.branch,
            "parse_branch": self.parse_branch,
            "ambiguous": self.ambiguous,
            "suspect": self.suspect,
        }


@dataclass(frozen=True, slots=True)
class GoldItem:
    """One scoring item with its resolved gold IRI set."""

    item_id: str
    firm: str
    stratum: str
    stratum_id: str
    ancestor_path: tuple[str, ...]
    leaf: str
    input_text: str
    gold_iris: tuple[str, ...]
    gold_labels_raw: tuple[str, ...]
    values: tuple[GoldValueRecord, ...]
    flags: tuple[str, ...]
    rules: tuple[str, ...]
    blank: bool
    notes: str | None
    source_rows: tuple[int, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "firm": self.firm,
            "stratum": self.stratum,
            "stratum_id": self.stratum_id,
            "ancestor_path": list(self.ancestor_path),
            "leaf": self.leaf,
            "input_text": self.input_text,
            "gold_iris": list(self.gold_iris),
            "gold_labels_raw": list(self.gold_labels_raw),
            "values": [value.to_json() for value in self.values],
            "flags": list(self.flags),
            "rules": list(self.rules),
            "blank": self.blank,
            "notes": self.notes,
            "source_rows": list(self.source_rows),
            "provenance": "curator_workbook",
            "gold_version": GOLD_VERSION,
        }


@dataclass
class StratumCount:
    """Per-stratum coverage, publishable by ``stratum_id`` without any firm surface string."""

    firm: str
    items: int = 0
    scored: int = 0
    blank: int = 0
    gold_iris: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "firm": self.firm,
            "items": self.items,
            "scored": self.scored,
            "blank": self.blank,
            "gold_iris": self.gold_iris,
        }


@dataclass
class GoldBuild:
    """Everything the emit stage needs: items, categories, and audit-gate queues."""

    items: list[GoldItem] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    branch_histogram: Counter[str] = field(default_factory=Counter)
    parse_branch_histogram: Counter[str] = field(default_factory=Counter)
    stratum_counts: dict[str, StratumCount] = field(default_factory=dict)
    resolution_batch: list[dict[str, object]] = field(default_factory=list)
    suspects: list[dict[str, object]] = field(default_factory=list)
    normalization_log: list[dict[str, object]] = field(default_factory=list)
    excluded: list[dict[str, object]] = field(default_factory=list)
    examples: dict[str, list[GoldItem]] = field(default_factory=dict)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def item_id(firm: str, stratum: str, ancestor_path: Sequence[str], leaf: str) -> str:
    """Stable 16-hex-char hash of the KTD3 item key."""
    payload = json.dumps([firm, stratum, list(ancestor_path), leaf], ensure_ascii=False)
    return sha256_text(payload)[:16]


def stratum_id(firm: str, stratum: str) -> str:
    """Stable id for a reporting stratum, so counts can be published without firm strings."""
    return sha256_text(f"{firm}|{stratum}")[:12]


def _input_text(ancestor_path: Sequence[str], leaf: str) -> str:
    return " > ".join([*ancestor_path, leaf])


_NORMALIZED_BRANCHES = frozenset({"normalized_preferred", "normalized_alternative", "lemma_variant"})
_PARSE_RULE = {
    "rhs_last": "compound_rhs",
    "rhs_first": "compound_rhs",
    "whole": "compound_whole",
    "bucket": "compound_bucket",
}


def build_gold(parse_results: Iterable[ParseResult], index: LabelIndex) -> GoldBuild:
    """Resolve every parsed item's candidates and assemble the versioned gold build."""
    build = GoldBuild()
    merged: dict[str, GoldItem] = {}
    order: list[str] = []

    for parse_result in parse_results:
        build.counts.update(parse_result.counts)
        build.excluded.extend(parse_result.excluded)
        for raw_item in parse_result.items:
            resolved = _resolve_item(raw_item, index, build)
            if resolved.item_id in merged:
                merged[resolved.item_id] = _merge_items(merged[resolved.item_id], resolved)
                build.counts["deduped_items"] += 1
            else:
                merged[resolved.item_id] = resolved
                order.append(resolved.item_id)

    build.items = sorted(
        (merged[key] for key in order),
        key=lambda item: (item.firm, item.stratum, item.input_text, item.item_id),
    )

    strata: dict[str, StratumCount] = {}
    for item in build.items:
        build.counts["items_total"] += 1
        if item.blank:
            build.counts["items_blank"] += 1
        else:
            build.counts["items_scored"] += 1
            build.counts["gold_iris_total"] += len(item.gold_iris)
        entry = strata.setdefault(item.stratum_id, StratumCount(firm=item.firm))
        entry.items += 1
        entry.scored += 0 if item.blank else 1
        entry.blank += 1 if item.blank else 0
        entry.gold_iris += len(item.gold_iris)
        for rule in item.rules:
            build.examples.setdefault(rule, []).append(item)
    build.stratum_counts = dict(sorted(strata.items()))
    build.counts["suspect_items"] = len(build.suspects)
    build.counts["resolution_batch_size"] = len(build.resolution_batch)
    return build


def _resolve_item(raw_item: RawItem, index: LabelIndex, build: GoldBuild) -> GoldItem:
    identifier = item_id(raw_item.firm, raw_item.stratum, raw_item.ancestor_path, raw_item.leaf)
    records: list[GoldValueRecord] = []
    seen_iris: set[str] = set()
    labels_raw: list[str] = []
    rules: list[str] = []
    flags = list(raw_item.flags)
    suspect_reasons: list[str] = []

    for value in raw_item.values:
        if is_relational(value.text):
            build.counts["excluded_relational"] += 1
            build.excluded.append(
                {
                    "reason": "relational_assertion",
                    "item_id": identifier,
                    "firm": raw_item.firm,
                    "raw": value.text,
                    "column": value.column,
                    "origin": value.origin,
                }
            )
            _note_rule(rules, "excluded_relational")
            continue
        if is_non_referential(value.text):
            build.counts["excluded_non_referential_value"] += 1
            build.excluded.append(
                {
                    "reason": "non_referential_value",
                    "item_id": identifier,
                    "firm": raw_item.firm,
                    "raw": value.text,
                    "column": value.column,
                    "origin": value.origin,
                }
            )
            _note_rule(rules, "excluded_non_referential_value")
            continue

        text, suspect = strip_suspect_marker(value.text)
        if suspect:
            build.counts["suspect_question_mark_values"] += 1
            if "suspect_question_mark" not in flags:
                flags.append("suspect_question_mark")
            suspect_reasons.append(f"question_mark:{value.text}")
            _note_rule(rules, "suspect_question_mark")

        resolution = resolve_gold_value(text, index, compound=value.compound)
        build.branch_histogram[resolution.branch] += 1
        build.parse_branch_histogram[resolution.parse_branch] += 1

        if not resolution.resolved:
            build.counts["unresolved_values"] += 1
            build.resolution_batch.append(
                {
                    "item_id": identifier,
                    "firm": raw_item.firm,
                    "stratum": raw_item.stratum,
                    "ancestor_path": list(raw_item.ancestor_path),
                    "leaf": raw_item.leaf,
                    "raw": value.text,
                    "normalized": resolution.normalized,
                    "column": value.column,
                    "origin": value.origin,
                    "parse_branch": resolution.parse_branch,
                    "reason": resolution.note or "no_label_match",
                }
            )
            _note_rule(rules, "unresolved")
            continue

        if resolution.branch in _NORMALIZED_BRANCHES:
            build.counts["normalization_only_resolutions"] += 1
            build.normalization_log.append(
                {
                    "item_id": identifier,
                    "raw": value.text,
                    "normalized": resolution.normalized,
                    "branch": resolution.branch,
                    "iri": resolution.iri,
                    "note": resolution.note,
                }
            )
            _note_rule(rules, "normalization_only")
        if resolution.ambiguous:
            build.counts["ambiguous_values"] += 1
            suspect_reasons.append(f"ambiguous:{value.text}")
            if "ambiguous_label" not in flags:
                flags.append("ambiguous_label")
            build.resolution_batch.append(
                {
                    "item_id": identifier,
                    "firm": raw_item.firm,
                    "stratum": raw_item.stratum,
                    "ancestor_path": list(raw_item.ancestor_path),
                    "leaf": raw_item.leaf,
                    "raw": value.text,
                    "normalized": resolution.normalized,
                    "column": value.column,
                    "origin": value.origin,
                    "parse_branch": resolution.parse_branch,
                    "reason": "ambiguous_label",
                    "candidates": list(resolution.candidates),
                }
            )
            _note_rule(rules, "ambiguous_label")
        if resolution.branch == "legacy_iri":
            _note_rule(rules, "legacy_iri")
        if resolution.parse_branch in _PARSE_RULE:
            _note_rule(rules, _PARSE_RULE[resolution.parse_branch])
        if value.from_pipe:
            _note_rule(rules, "pipe_split")
        if value.origin == "level1":
            _note_rule(rules, "cascade_level1")
        elif value.origin == "level2":
            _note_rule(rules, "cascade_level2")
            if value.shared_row and "level2_level3_same_row" not in raw_item.flags:
                build.counts["cascade_from_shared_row_values"] += 1
                _note_rule(rules, "cascade_from_shared_row")

        assert resolution.iri is not None
        labels_raw.append(value.text)
        if resolution.iri in seen_iris:
            build.counts["duplicate_gold_iris"] += 1
            continue
        seen_iris.add(resolution.iri)
        records.append(
            GoldValueRecord(
                raw=value.text,
                iri=resolution.iri,
                origin=value.origin,
                column=value.column,
                branch=resolution.branch,
                parse_branch=resolution.parse_branch,
                ambiguous=resolution.ambiguous,
                suspect=suspect,
            )
        )

    for flag in raw_item.flags:
        if flag == "notes_flagged":
            suspect_reasons.append("sali_notes_flagged")
            _note_rule(rules, "notes_flagged")
        elif flag == "level2_level3_same_row":
            _note_rule(rules, "level2_level3_same_row")
        elif flag == "multi_row_union":
            _note_rule(rules, "multi_row_union")

    blank = not records
    if blank:
        _note_rule(rules, "blank_row")

    item = GoldItem(
        item_id=identifier,
        firm=raw_item.firm,
        stratum=raw_item.stratum,
        stratum_id=stratum_id(raw_item.firm, raw_item.stratum),
        ancestor_path=raw_item.ancestor_path,
        leaf=raw_item.leaf,
        input_text=_input_text(raw_item.ancestor_path, raw_item.leaf),
        gold_iris=tuple(sorted(seen_iris)),
        gold_labels_raw=tuple(labels_raw),
        values=tuple(sorted(records, key=lambda record: record.iri)),
        flags=tuple(dict.fromkeys(flags)),
        rules=tuple(dict.fromkeys(rules)),
        blank=blank,
        notes=raw_item.notes,
        source_rows=raw_item.source_rows,
    )

    if suspect_reasons:
        build.suspects.append(
            {
                "item_id": item.item_id,
                "firm": item.firm,
                "stratum": item.stratum,
                "ancestor_path": list(item.ancestor_path),
                "leaf": item.leaf,
                "gold_iris": list(item.gold_iris),
                "gold_labels_raw": list(item.gold_labels_raw),
                "reasons": suspect_reasons,
                "notes": item.notes,
                "source_rows": list(item.source_rows),
            }
        )
    return item


def _note_rule(rules: list[str], rule: str) -> None:
    if rule not in rules:
        rules.append(rule)


def _merge_items(first: GoldItem, second: GoldItem) -> GoldItem:
    """Union two rows that share one KTD3 item key (identical (input, gold) pairs collapse)."""
    records = {record.iri: record for record in (*first.values, *second.values)}
    iris = tuple(sorted(records))
    notes = " | ".join(note for note in (first.notes, second.notes) if note) or None
    return GoldItem(
        item_id=first.item_id,
        firm=first.firm,
        stratum=first.stratum,
        stratum_id=first.stratum_id,
        ancestor_path=first.ancestor_path,
        leaf=first.leaf,
        input_text=first.input_text,
        gold_iris=iris,
        gold_labels_raw=tuple(dict.fromkeys((*first.gold_labels_raw, *second.gold_labels_raw))),
        values=tuple(records[iri] for iri in iris),
        flags=tuple(dict.fromkeys((*first.flags, *second.flags))),
        rules=tuple(dict.fromkeys((*first.rules, *second.rules, "deduped"))),
        blank=not iris,
        notes=notes,
        source_rows=tuple(dict.fromkeys((*first.source_rows, *second.source_rows))),
    )


# --------------------------------------------------------------------------------------
# Emit stage
# --------------------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomic write, mirroring ``src/folio_resolve/annotate/feedback_store.py``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _jsonl(records: Iterable[dict[str, object]]) -> str:
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    return "\n".join(lines) + ("\n" if lines else "")


_WORKED_EXAMPLE_RULES: tuple[tuple[str, str], ...] = (
    ("cascade_level2", "Level-2 cascade: the nearest preceding L2 row's cells join the item's gold"),
    ("cascade_level1", "Level-1 cascade: the enclosing practice group's cells join the item's gold"),
    (
        "level2_level3_same_row",
        "A row carrying both L2 and L3 labels is the L2's mapping, cascading to all its children",
    ),
    (
        "cascade_from_shared_row",
        "A sibling item inheriting values written on such a shared row (audit-gate exposure)",
    ),
    ("pipe_split", "Pipe-delimited cell split into separate gold values"),
    ("normalization_only", "Resolved only after NFKC / dash / whitespace normalization"),
    ("compound_rhs", "`Bucket: Concept` parsed on its right-hand side"),
    ("compound_whole", "`Bucket: Concept` parsed as a whole string"),
    ("compound_bucket", "`Bucket: Concept` parsed on its bucket"),
    ("legacy_iri", "Legacy `lmss.sali.org` IRI normalized into the FOLIO namespace"),
    ("suspect_question_mark", "`?`-suffixed value: kept as gold, routed to the suspects queue"),
    ("notes_flagged", "SALI NOTES contains ?/discuss/ask/deprecat: low-confidence pre-flag"),
    ("ambiguous_label", "Label shared by two or more concepts: deterministic pick, flagged"),
    ("multi_row_union", "Rows sharing a term key union into one item"),
    ("deduped", "Identical (input, gold) rows collapsed into one item"),
    ("blank_row", "Own and inherited sets both empty: coverage, not scored"),
    ("unresolved", "Residual label sent to the resolution batch"),
)

_EXCLUSION_RULES: tuple[tuple[str, str], ...] = (
    ("relational_assertion", "Relational expression excluded from gold as its own category"),
    ("non_referential_value", "Non-referential value (`varies`) excluded and counted"),
    ("non_referential_leaf", "Non-referential leaf (`Other`) excluded as a scoring item"),
)


def render_worked_examples(build: GoldBuild, *, per_rule: int = 6) -> str:
    """The audit-gate spec sheet: real rows showing each derivation rule firing (KTD6)."""
    lines: list[str] = [
        f"# Gold derivation — worked examples (gold v{GOLD_VERSION})",
        "",
        "GITIGNORED: contains real workbook rows (KTD1). One section per derivation rule, each",
        "showing the item the rule produced, the gold set, and the values that fed it.",
        "",
    ]
    for rule, description in _WORKED_EXAMPLE_RULES:
        examples = build.examples.get(rule, [])
        lines.append(f"## `{rule}` — {description}")
        lines.append("")
        lines.append(f"Items where this rule fired: **{len(examples)}**")
        lines.append("")
        if not examples:
            lines.append("_No item in this build exercised this rule._")
            lines.append("")
            continue
        for example in examples[:per_rule]:
            lines.append(
                f"- **{example.input_text}**  (`{example.item_id}`, rows {list(example.source_rows)})"
            )
            for value in example.values:
                lines.append(
                    f"    - `{value.raw}` [{value.origin}/{value.column}]"
                    f" -> `{value.iri}` via `{value.branch}`/`{value.parse_branch}`"
                )
            if not example.values:
                lines.append("    - _(no resolved gold values)_")
            if example.notes:
                lines.append(f"    - notes: {example.notes}")
        lines.append("")

    for reason, description in _EXCLUSION_RULES:
        records = [record for record in build.excluded if record.get("reason") == reason]
        lines.append(f"## `{reason}` — {description}")
        lines.append("")
        lines.append(f"Occurrences: **{len(records)}**")
        lines.append("")
        for record in records[:per_rule]:
            lines.append(f"- `{json.dumps(record, ensure_ascii=False, sort_keys=True)}`")
        lines.append("")

    lines.append("## Residual resolution batch (audit-gate input)")
    lines.append("")
    lines.append(f"Entries: **{len(build.resolution_batch)}**")
    lines.append("")
    for record in build.resolution_batch[:per_rule]:
        lines.append(f"- `{json.dumps(record, ensure_ascii=False, sort_keys=True)}`")
    lines.append("")
    return "\n".join(lines)


def build_manifest(
    build: GoldBuild,
    *,
    content_sha256: str,
    ontology_sha256: str,
    folio_python_version: str,
) -> dict[str, object]:
    return {
        "gold_version": GOLD_VERSION,
        "gold_id": f"v{GOLD_VERSION}-{content_sha256[:12]}",
        "content_sha256": content_sha256,
        "ontology_cache_sha256": ontology_sha256,
        "folio_python_version": folio_python_version,
        "counts": dict(sorted(build.counts.items())),
        "resolution_branch_histogram": dict(sorted(build.branch_histogram.items())),
        "compound_parse_branch_histogram": dict(sorted(build.parse_branch_histogram.items())),
        "stratum_counts": {key: entry.to_json() for key, entry in build.stratum_counts.items()},
        "rules_fired": {rule: len(items) for rule, items in sorted(build.examples.items())},
    }


def write_gold(
    build: GoldBuild,
    *,
    gold_dir: Path,
    reports_dir: Path,
    ontology_sha256: str,
    folio_python_version: str,
) -> dict[str, Path]:
    """Write the versioned gold artifacts deterministically; returns the paths written."""
    gold_text = _jsonl(item.to_json() for item in build.items)
    content_sha256 = sha256_text(gold_text)
    paths = {
        "gold": gold_dir / f"gold_v{GOLD_VERSION}.jsonl",
        "manifest": gold_dir / f"gold_v{GOLD_VERSION}.manifest.json",
        "resolution_batch": gold_dir / f"resolution_batch_v{GOLD_VERSION}.jsonl",
        "suspects": gold_dir / f"suspects_v{GOLD_VERSION}.jsonl",
        "normalization_log": gold_dir / f"normalization_log_v{GOLD_VERSION}.jsonl",
        "excluded": gold_dir / f"excluded_v{GOLD_VERSION}.jsonl",
        "worked_examples": reports_dir / f"worked_examples_v{GOLD_VERSION}.md",
    }
    _atomic_write_text(paths["gold"], gold_text)
    _atomic_write_text(paths["resolution_batch"], _jsonl(build.resolution_batch))
    _atomic_write_text(paths["suspects"], _jsonl(build.suspects))
    _atomic_write_text(paths["normalization_log"], _jsonl(build.normalization_log))
    _atomic_write_text(paths["excluded"], _jsonl(build.excluded))
    _atomic_write_text(paths["worked_examples"], render_worked_examples(build))
    manifest = build_manifest(
        build,
        content_sha256=content_sha256,
        ontology_sha256=ontology_sha256,
        folio_python_version=folio_python_version,
    )
    _atomic_write_text(
        paths["manifest"], json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return paths


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

_PARSERS = {"firm1": parse_firm1, "firm2": parse_firm2}


def parse_all_sheets(
    *, data_dir: Path = DEFAULT_DATA_DIR, manifest_path: Path = DEFAULT_MANIFEST_PATH
) -> list[ParseResult]:
    """Load every in-scope derived sheet named by the intake manifest and parse it."""
    results: list[ParseResult] = []
    for entry in read_manifest(manifest_path):
        parser = _PARSERS.get(entry.firm)
        if parser is None:
            raise ValueError(f"no gold parser registered for firm={entry.firm!r}")
        for sheet in entry.sheets:
            rows = load_sheet_rows(
                entry.firm,
                sheet.sheet_name_hash,
                data_dir=data_dir,
                manifest_path=manifest_path,
            )
            results.append(parser(rows, firm=entry.firm))
    return results


def _print_report(build: GoldBuild, manifest: dict[str, object]) -> None:
    """Counts only — never a firm surface string on stdout (KTD1)."""
    print(f"gold_version={GOLD_VERSION} gold_id={manifest['gold_id']}")
    print(f"ontology_cache_sha256={manifest['ontology_cache_sha256']}")
    print(f"folio_python_version={manifest['folio_python_version']}")
    print("\n[counts]")
    for key, value in sorted(build.counts.items()):
        print(f"  {key}: {value}")
    print("\n[resolution branch histogram]")
    for key, value in sorted(build.branch_histogram.items()):
        print(f"  {key}: {value}")
    print("\n[compound parse branch histogram]")
    for key, value in sorted(build.parse_branch_histogram.items()):
        print(f"  {key}: {value}")
    print("\n[per-stratum item counts] (stratum_id only — names stay in the gitignored gold)")
    for key, entry in build.stratum_counts.items():
        print(
            f"  {entry.firm} {key}: items={entry.items} scored={entry.scored}"
            f" blank={entry.blank} gold_iris={entry.gold_iris}"
        )
    print("\n[rules fired]")
    for rule, items in sorted(build.examples.items()):
        print(f"  {rule}: {len(items)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m folio_eval.gold",
        description="Build the versioned gold set from the derived firm sheets (U2).",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_DATA_DIR / "gold")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_DATA_DIR / "reports")
    args = parser.parse_args(argv)

    index, ontology_sha256, folio_version = load_folio_index()
    build = build_gold(
        parse_all_sheets(data_dir=args.data_dir, manifest_path=args.manifest), index
    )
    paths = write_gold(
        build,
        gold_dir=args.gold_dir,
        reports_dir=args.reports_dir,
        ontology_sha256=ontology_sha256,
        folio_python_version=folio_version,
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    print(f"ontology_labels_indexed={index.label_count}")
    _print_report(build, manifest)
    print("\n[artifacts]")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
