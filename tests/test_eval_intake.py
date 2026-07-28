"""Tests for folio_eval.intake — manifest hashing, round-trip, and loader verification (U1).

All fixture data here is synthetic (fake column/cell strings) — no real firm content, per KTD1's
public-repo policy (see tests/conftest.py). The loader and manifest round-trip run entirely
against plain Python data structures; nothing here imports openpyxl, so this file runs green
under the base venv (KTD11). One test exercises the real extraction routine against a tiny
in-memory workbook built with openpyxl, and is skipped when openpyxl is not installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from folio_eval.intake import (
    IntakeError,
    Row,
    SheetEntry,
    WorkbookEntry,
    header_signature,
    load_sheet_rows,
    parse_manifest,
    read_manifest,
    rows_content_sha256,
    write_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_jsonl(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _entry_for(rows: list[Row], sheet_name_hash: str) -> SheetEntry:
    return SheetEntry(
        sheet_name_hash=sheet_name_hash,
        row_count=len(rows),
        header_signature=header_signature(rows[0]),
        content_sha256=rows_content_sha256(rows),
    )


SYNTHETIC_ROWS: list[Row] = [
    ["Widget Category", "Widget Level", "Widget Notes"],
    ["Alpha Gadgets", "Top", None],
    ["Beta Gizmos", "Sub", "trailing note"],
    ["Gamma Doohickeys", None, "another"],
]
FAKE_SHEET_HASH = "a" * 64
FAKE_FIRM = "synthfirm"


def _base_manifest(tmp_path: Path) -> tuple[Path, Path, SheetEntry]:
    data_dir = tmp_path / "data"
    manifest_path = data_dir / "MANIFEST.md"
    sheet_entry = _entry_for(SYNTHETIC_ROWS, FAKE_SHEET_HASH)
    _write_jsonl(data_dir / FAKE_FIRM / f"{FAKE_SHEET_HASH}.jsonl", SYNTHETIC_ROWS)
    workbook_entry = WorkbookEntry(
        firm=FAKE_FIRM,
        workbook_path="/fake/external/path/workbook.xlsx",
        workbook_sha256="b" * 64,
        sheets=(sheet_entry,),
    )
    write_manifest(manifest_path, [workbook_entry])
    return data_dir, manifest_path, sheet_entry


def test_loader_accepts_matching_file(tmp_path: Path) -> None:
    data_dir, manifest_path, _ = _base_manifest(tmp_path)
    rows = load_sheet_rows(
        FAKE_FIRM, FAKE_SHEET_HASH, data_dir=data_dir, manifest_path=manifest_path
    )
    assert rows == SYNTHETIC_ROWS


def test_loader_rejects_byte_changed_file(tmp_path: Path) -> None:
    data_dir, manifest_path, _ = _base_manifest(tmp_path)
    # Mutate the derived file after the manifest was written, without updating the manifest —
    # simulating a hand-edit or a stale re-extraction that never rewrote the manifest.
    mutated_rows = [
        SYNTHETIC_ROWS[0],
        ["Alpha Gadgets", "Top", "MUTATED"],
        *SYNTHETIC_ROWS[2:],
    ]
    _write_jsonl(data_dir / FAKE_FIRM / f"{FAKE_SHEET_HASH}.jsonl", mutated_rows)

    with pytest.raises(IntakeError, match="content_sha256"):
        load_sheet_rows(FAKE_FIRM, FAKE_SHEET_HASH, data_dir=data_dir, manifest_path=manifest_path)


def test_loader_rejects_shifted_header_signature(tmp_path: Path) -> None:
    """A header signature recorded before a column insert must be caught even when the file's
    content hash and row count happen to still validate against a (deliberately) stale manifest
    entry for those two fields — isolating the header-signature check from the others."""
    data_dir = tmp_path / "data"
    manifest_path = data_dir / "MANIFEST.md"
    _write_jsonl(data_dir / FAKE_FIRM / f"{FAKE_SHEET_HASH}.jsonl", SYNTHETIC_ROWS)

    correct_entry = _entry_for(SYNTHETIC_ROWS, FAKE_SHEET_HASH)
    stale_header_entry = SheetEntry(
        sheet_name_hash=correct_entry.sheet_name_hash,
        row_count=correct_entry.row_count,
        header_signature=header_signature(["Old Header Before Column Insert"]),
        content_sha256=correct_entry.content_sha256,
    )
    workbook_entry = WorkbookEntry(
        firm=FAKE_FIRM,
        workbook_path="/fake/external/path/workbook.xlsx",
        workbook_sha256="b" * 64,
        sheets=(stale_header_entry,),
    )
    write_manifest(manifest_path, [workbook_entry])

    with pytest.raises(IntakeError, match="header_signature"):
        load_sheet_rows(FAKE_FIRM, FAKE_SHEET_HASH, data_dir=data_dir, manifest_path=manifest_path)


def test_loader_rejects_row_count_mismatch(tmp_path: Path) -> None:
    """Row count is checked independently of the content hash (belt-and-suspenders)."""
    data_dir = tmp_path / "data"
    manifest_path = data_dir / "MANIFEST.md"
    _write_jsonl(data_dir / FAKE_FIRM / f"{FAKE_SHEET_HASH}.jsonl", SYNTHETIC_ROWS)

    correct_entry = _entry_for(SYNTHETIC_ROWS, FAKE_SHEET_HASH)
    wrong_count_entry = SheetEntry(
        sheet_name_hash=correct_entry.sheet_name_hash,
        row_count=correct_entry.row_count + 1,
        header_signature=correct_entry.header_signature,
        content_sha256=correct_entry.content_sha256,
    )
    workbook_entry = WorkbookEntry(
        firm=FAKE_FIRM,
        workbook_path="/fake/external/path/workbook.xlsx",
        workbook_sha256="b" * 64,
        sheets=(wrong_count_entry,),
    )
    write_manifest(manifest_path, [workbook_entry])

    with pytest.raises(IntakeError, match="row_count"):
        load_sheet_rows(FAKE_FIRM, FAKE_SHEET_HASH, data_dir=data_dir, manifest_path=manifest_path)


def test_loader_raises_on_unknown_firm(tmp_path: Path) -> None:
    _, manifest_path, _ = _base_manifest(tmp_path)
    with pytest.raises(IntakeError, match="firm"):
        load_sheet_rows(
            "nonexistent-firm",
            FAKE_SHEET_HASH,
            data_dir=tmp_path / "data",
            manifest_path=manifest_path,
        )


def test_loader_raises_on_unknown_sheet_hash(tmp_path: Path) -> None:
    data_dir, manifest_path, _ = _base_manifest(tmp_path)
    with pytest.raises(IntakeError, match="sheet_name_hash"):
        load_sheet_rows(FAKE_FIRM, "f" * 64, data_dir=data_dir, manifest_path=manifest_path)


def test_manifest_round_trip(tmp_path: Path) -> None:
    entries = [
        WorkbookEntry(
            firm="firm1",
            workbook_path="/ext/firm1_workbook.xlsx",
            workbook_sha256="1" * 64,
            sheets=(
                SheetEntry(
                    sheet_name_hash="2" * 64,
                    row_count=1551,
                    header_signature="3" * 64,
                    content_sha256="4" * 64,
                ),
            ),
        ),
        WorkbookEntry(
            firm="firm2",
            workbook_path="/ext/firm2_workbook.xlsx",
            workbook_sha256="5" * 64,
            sheets=(
                SheetEntry(
                    sheet_name_hash="6" * 64,
                    row_count=145,
                    header_signature="7" * 64,
                    content_sha256="8" * 64,
                ),
                SheetEntry(
                    sheet_name_hash="9" * 64,
                    row_count=54,
                    header_signature="a" * 64,
                    content_sha256="b" * 64,
                ),
            ),
        ),
    ]
    manifest_path = tmp_path / "MANIFEST.md"
    write_manifest(manifest_path, entries)
    roundtripped = read_manifest(manifest_path)
    assert roundtripped == entries

    # parse_manifest on the same rendered text (not just via read_manifest) also round-trips.
    assert parse_manifest(manifest_path.read_text(encoding="utf-8")) == entries


def test_repo_hygiene_eval_data_ignored_but_manifest_is_not() -> None:
    """git check-ignore eval/data/<anything> succeeds; git check-ignore eval/data/MANIFEST.md
    fails -- i.e. the manifest is the one committable path under the ignored eval/data/ tree."""
    result_data = subprocess.run(
        ["git", "check-ignore", "eval/data/some_derived_file.jsonl"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result_data.returncode == 0, result_data.stderr

    result_manifest = subprocess.run(
        ["git", "check-ignore", "eval/data/MANIFEST.md"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result_manifest.returncode == 1, result_manifest.stderr


def test_extract_sheet_against_real_openpyxl_workbook(tmp_path: Path) -> None:
    """Exercises the extraction routine end-to-end against a tiny synthetic workbook.

    Skipped when openpyxl is not installed (base venv, KTD11) — run this file via
    `uv run --with openpyxl pytest tests/test_eval_intake.py` to exercise it.
    """
    openpyxl = pytest.importorskip("openpyxl")
    from folio_eval.intake import extract_sheet, hash_sheet_name

    workbook = openpyxl.Workbook()
    in_scope = workbook.active
    in_scope.title = "In Scope Sheet"
    in_scope.append(["Widget Category", "Widget Level", None, None])  # trailing blanks trimmed
    in_scope.append(["Alpha Gadgets", "Top", None, None])
    in_scope.append(["Beta Gizmos", "Sub", None, None])

    out_of_scope = workbook.create_sheet("Out Of Scope Sheet")
    out_of_scope.append(["Should Never Be Read"])

    workbook_path = tmp_path / "synthetic.xlsx"
    workbook.save(workbook_path)

    out_path = tmp_path / "data" / "synthfirm" / f"{hash_sheet_name('In Scope Sheet')}.jsonl"
    entry = extract_sheet(workbook_path, "In Scope Sheet", out_path)

    assert entry.sheet_name_hash == hash_sheet_name("In Scope Sheet")
    assert entry.row_count == 3
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # Trailing all-blank columns were trimmed to the used width (2), not the padded width (4).
    assert json.loads(lines[0]) == ["Widget Category", "Widget Level"]
    assert json.loads(lines[1]) == ["Alpha Gadgets", "Top"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
