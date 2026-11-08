"""Read the agreed SSSOM subset with csv and a small scalar/map header parser."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_REQUIRED = ("subject_id", "predicate_id", "object_id", "mapping_justification")
_MAPPING_PROPERTIES = frozenset(
    {
        "mappingRelation",
        "exactMatch",
        "closeMatch",
        "broadMatch",
        "narrowMatch",
        "relatedMatch",
    }
)
_HEADER_ENTRY = re.compile(r"""^(\s*)(?:"([^"]+)"|'([^']+)'|([^:]+)):\s*(.*)$""")


@dataclass(frozen=True)
class MappingLink:
    subject_id: str
    predicate_id: str
    object_id: str  # Raw OpenGloss sense_id; all other identifiers are expanded IRIs.
    confidence: float | None
    mapping_justification: str
    mapping_set_id: str
    mapping_set_version: str


@dataclass(frozen=True)
class Crosswalk:
    metadata: Mapping[str, str]
    curie_map: Mapping[str, str]
    by_subject: Mapping[str, tuple[MappingLink, ...]]

    def for_subject(self, iri: str) -> tuple[MappingLink, ...]:
        return self.by_subject.get(iri, ())


def _scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        decoded: str = json.loads(value)
        return decoded
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _expand(identifier: str, curies: Mapping[str, str]) -> str:
    prefix, separator, local = identifier.partition(":")
    if separator and prefix in curies:
        return curies[prefix] + local
    return identifier


def load_crosswalk(path: str | Path) -> Crosswalk:
    """Load immutable subject lookups, expanding identifiers through the file's map.

    Header values are flat YAML scalars except for the single flat ``curie_map``.
    Row numbers in errors are physical, one-based file line numbers.
    """
    metadata: dict[str, str] = {}
    curies: dict[str, str] = {}
    table: list[str] = []
    line_numbers: list[int] = []
    in_curies = False
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.startswith("#"):
            header = line[1:]
            if header.startswith(" "):
                header = header[1:]
            match = _HEADER_ENTRY.match(header)
            if not match:
                continue
            indent, double, single, plain, value = match.groups()
            key = double or single or plain
            if indent and in_curies:
                curies[key.rstrip(":")] = _scalar(value)
            elif not indent:
                in_curies = key == "curie_map"
                if not in_curies:
                    metadata[key] = _scalar(value)
        elif line.strip():
            table.append(line)
            line_numbers.append(line_number)
    reader = csv.DictReader(table, delimiter="\t")
    for column in _REQUIRED:
        if column not in (reader.fieldnames or ()):
            raise ValueError(f"Missing required SSSOM column: {column}")
    for key in ("mapping_set_id", "mapping_set_version"):
        if not metadata.get(key):
            raise ValueError(f"Missing required SSSOM header: {key}")
    if not curies.get("OGS"):
        raise ValueError("Missing required SSSOM curie_map prefix: OGS")
    metadata["mapping_set_id"] = _expand(metadata["mapping_set_id"], curies)
    grouped: dict[str, list[MappingLink]] = {}
    mapping_iris = {curies.get("skos", "") + name for name in _MAPPING_PROPERTIES}
    for number, row in enumerate(reader, 1):
        line_number = line_numbers[number]
        predicate = _expand(row["predicate_id"], curies)
        if predicate not in mapping_iris:
            raise ValueError(f"SSSOM row {line_number}: not a SKOS mapping property: {predicate}")
        subject = _expand(row["subject_id"], curies)
        obj = _expand(row["object_id"], curies)
        if not obj.startswith(curies["OGS"]):
            raise ValueError(f"SSSOM row {line_number}: object_id is outside the OGS namespace")
        confidence = row.get("confidence", "")
        link = MappingLink(
            subject_id=subject,
            predicate_id=predicate,
            object_id=obj[len(curies["OGS"]) :],
            confidence=float(confidence) if confidence else None,
            mapping_justification=_expand(row["mapping_justification"], curies),
            mapping_set_id=metadata["mapping_set_id"],
            mapping_set_version=metadata["mapping_set_version"],
        )
        grouped.setdefault(subject, []).append(link)
    return Crosswalk(
        MappingProxyType(metadata),
        MappingProxyType(curies),
        MappingProxyType(
            {
                subject: tuple(
                    sorted(
                        links, key=lambda link: (link.subject_id, link.predicate_id, link.object_id)
                    )
                )
                for subject, links in grouped.items()
            }
        ),
    )
