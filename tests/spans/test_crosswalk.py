from pathlib import Path

import pytest

from folio_resolve.spans.crosswalk import load_crosswalk

FIXTURE = Path(__file__).parent / "fixtures/folio-opengloss-mini.sssom.tsv"
BREACH = "https://folio.openlegalstandard.org/BreachOfContract"
SKOS = "http://www.w3.org/2004/02/skos/core#"


def test_fixture_links_confidences_metadata_and_expanded_curies():
    crosswalk = load_crosswalk(FIXTURE)
    links = crosswalk.for_subject(BREACH)
    assert [(r.predicate_id, r.object_id, r.confidence) for r in links] == [
        (SKOS + "broadMatch", "breach:noun:0", 0.85),
        (SKOS + "exactMatch", "breach_of_contract:noun:0", 0.95),
    ]
    assert all(r.subject_id == BREACH for r in links)
    assert all(
        r.mapping_justification == "https://w3id.org/semapv/vocab/ManualMappingCuration"
        for r in links
    )
    assert all(r.mapping_set_id == crosswalk.metadata["mapping_set_id"] for r in links)
    assert all(r.mapping_set_version == "0.1.0" for r in links)
    assert crosswalk.metadata["subject_source_version"] == "synthetic-folio-1"
    assert crosswalk.for_subject("missing") == ()


def test_ogs_namespace_is_read_from_header_for_curies_and_expanded_ids(tmp_path):
    contents = (
        FIXTURE.read_text()
        .replace(
            "tag:folio.openlegalstandard.org,2026:opengloss/sense/", "https://example.org/senses/"
        )
        .replace("OGS:breach:noun:0", "https://example.org/senses/breach:noun:0")
    )
    path = tmp_path / "alternate.tsv"
    path.write_text(contents)
    assert load_crosswalk(path).for_subject(BREACH)[0].object_id == "breach:noun:0"


def test_required_column_named_and_extra_column_ignored(tmp_path):
    lines = FIXTURE.read_text().splitlines()
    for mode in ("missing", "extra"):
        changed = []
        for line in lines:
            if not line.startswith("#"):
                cells = line.split("\t")
                if mode == "missing":
                    cells.pop(2)
                else:
                    cells.append("ignored_column" if cells[0] == "subject_id" else "ignored_value")
                line = "\t".join(cells)
            changed.append(line)
        path = tmp_path / f"{mode}.tsv"
        path.write_text("\n".join(changed))
        if mode == "missing":
            with pytest.raises(ValueError, match="predicate_id"):
                load_crosswalk(path)
        else:
            assert len(load_crosswalk(path).for_subject(BREACH)) == 2


def test_non_mapping_predicate_names_row_number(tmp_path):
    contents = FIXTURE.read_text().replace("skos:broadMatch", "skos:prefLabel")
    path = tmp_path / "invalid.tsv"
    path.write_text(contents)
    row_number = next(
        i for i, line in enumerate(contents.splitlines(), 1) if line.startswith("FOLIO:")
    )
    with pytest.raises(ValueError, match=rf"row {row_number}\b"):
        load_crosswalk(path)


def test_quoted_header_scalars_and_prefixes(tmp_path):
    contents = (
        FIXTURE.read_text()
        .replace("#   OGS: tag:", '#   "OGS:": tag:')
        .replace("# mapping_set_version: 0.1.0", "# mapping_set_version: '0.1.0'")
    )
    path = tmp_path / "quoted.tsv"
    path.write_text(contents)
    assert load_crosswalk(path).for_subject(BREACH)[0].mapping_set_version == "0.1.0"


def test_confidence_column_is_optional(tmp_path):
    lines = []
    for line in FIXTURE.read_text().splitlines():
        if not line.startswith("#"):
            cells = line.split("\t")
            cells.pop(7)
            line = "\t".join(cells)
        lines.append(line)
    path = tmp_path / "no-confidence.tsv"
    path.write_text("\n".join(lines))
    assert all(link.confidence is None for link in load_crosswalk(path).for_subject(BREACH))
