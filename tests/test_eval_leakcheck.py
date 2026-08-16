"""Hashed surface leak checker (U4; R6, R7; KTD4) — synthetic fixtures only.

No workbook, no FOLIO, no network: every surface, IRI, manifest, and input document is invented
for these tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from folio_eval.clusters import SurfaceLeakError, assert_no_surfaces, surface_strings
from folio_eval.leakcheck import (
    LeakcheckError,
    Manifest,
    ScryptParams,
    build_manifest,
    canonical_json,
    harvest_surfaces,
    main,
    scan_file,
    scan_text,
)
from folio_eval.normalize import normalize_label
from folio_eval.splits import GoldItemRecord, GoldSet

FAST_SCRYPT = ScryptParams(n=2**4, r=1, p=1, dklen=16)
SALT = b"synthetic-test-salt"
FAKE_SURFACE = "chancery estoppel widgets"


def synthetic_gold(
    *,
    iri: str = "https://example.invalid/folio/FakeConcept",
    extra_surface: str = "invented ancestor",
) -> GoldSet:
    record = GoldItemRecord(
        item_id="synthetic-1",
        firm="invented firm",
        stratum="invented stratum",
        stratum_id="synth-stratum",
        ancestor_path=(extra_surface,),
        leaf=FAKE_SURFACE,
        input_text=f"invented ancestor > {FAKE_SURFACE}",
        gold_iris=frozenset({iri}),
        flags=frozenset(),
        blank=False,
    )
    return GoldSet(
        items=(record,),
        gold_id="synthetic-gold",
        gold_version=4,
        content_sha256="a" * 64,
        ontology_cache_sha256="b" * 64,
        manifest={"gold_version": 4, "content_sha256": "a" * 64},
    )


def manifest_for(*surfaces: str) -> Manifest:
    return build_manifest(
        surfaces,
        SALT,
        gold_version="gold_v4",
        gold_content_sha256="a" * 64,
        scrypt_params=FAST_SCRYPT,
    )


def test_embedded_surface_reports_count_and_item_id_without_surface(tmp_path: Path) -> None:
    manifest = manifest_for(FAKE_SURFACE)
    input_path = tmp_path / "items.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "item_id": "synthetic-item-7",
                "text": f"A longer passage embeds {FAKE_SURFACE} in its middle.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = scan_file(input_path, manifest, SALT)

    assert report.collision_count == 1
    assert report.item_ids == ("synthetic-item-7",)
    assert FAKE_SURFACE not in canonical_json(report.to_json())


def test_clean_text_has_no_collisions() -> None:
    assert scan_text("Entirely unrelated invented prose.", manifest_for(FAKE_SURFACE), SALT) == 0


def test_manifest_generation_is_byte_identical_for_identical_inputs() -> None:
    first = manifest_for(FAKE_SURFACE, "another fabricated surface")
    second = manifest_for("another fabricated surface", FAKE_SURFACE, FAKE_SURFACE)
    assert canonical_json(first.to_json()) == canonical_json(second.to_json())


def test_check_fails_closed_when_local_gold_version_is_newer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = tmp_path / "surface-manifest.json"
    stale_manifest = build_manifest(
        (FAKE_SURFACE,),
        SALT,
        gold_version="gold_v3",
        gold_content_sha256="c" * 64,
        scrypt_params=FAST_SCRYPT,
    )
    manifest_path.write_text(
        canonical_json(stale_manifest.to_json()), encoding="utf-8"
    )
    salt_path = tmp_path / "salt"
    salt_path.write_bytes(SALT)
    input_path = tmp_path / "input.md"
    input_path.write_text("clean synthetic prose", encoding="utf-8")
    local_gold_manifest = tmp_path / "gold_v4.manifest.json"
    local_gold_manifest.write_text('{"gold_version": 4}\n', encoding="utf-8")

    result = main(
        [
            "check",
            "--manifest",
            str(manifest_path),
            "--salt-file",
            str(salt_path),
            str(input_path),
        ],
        local_gold_manifest_paths=(local_gold_manifest,),
    )

    assert result == 2
    output = capsys.readouterr().err
    assert "manifest stale" in output
    assert FAKE_SURFACE not in output


def test_harvest_excludes_gold_iris_and_other_iri_like_values() -> None:
    iri = "https://example.invalid/folio/SecretCoverage"
    other_iri = "https://another.invalid/HiddenSurface"
    gold = synthetic_gold(iri=iri, extra_surface=other_iri)
    surfaces = harvest_surfaces(gold)
    manifest = build_manifest(
        surfaces,
        SALT,
        gold_version="gold_v4",
        gold_content_sha256=gold.content_sha256,
        scrypt_params=FAST_SCRYPT,
    )
    iri_digest = hashlib.scrypt(
        normalize_label(iri).encode(),
        salt=SALT,
        n=FAST_SCRYPT.n,
        r=FAST_SCRYPT.r,
        p=FAST_SCRYPT.p,
        dklen=FAST_SCRYPT.dklen,
    ).hex()
    assert iri not in surfaces
    assert other_iri not in surfaces
    assert iri_digest not in manifest.digests


def test_checker_agrees_with_owner_backstop_for_embedded_surface() -> None:
    raw_surfaces = surface_strings(synthetic_gold())
    passage = f"Prefix words then {FAKE_SURFACE} followed by suffix words."

    with pytest.raises(SurfaceLeakError):
        assert_no_surfaces(passage, raw_surfaces, what="synthetic fixture")
    assert scan_text(passage, manifest_for(*harvest_surfaces(synthetic_gold())), SALT) > 0


def test_ngram_bounds_include_maximum_and_exclude_short_fragment() -> None:
    manifest = manifest_for("alpha beta", "one two three four")
    assert scan_text("prefix one two three four suffix", manifest, SALT) == 1
    assert scan_text("alpha", manifest, SALT) == 0


def test_manifest_rejects_malformed_digest() -> None:
    valid = manifest_for(FAKE_SURFACE)
    malformed = Manifest(
        version=valid.version,
        scrypt_params=valid.scrypt_params,
        normalization=valid.normalization,
        min_tokens=valid.min_tokens,
        max_tokens=valid.max_tokens,
        gold_version=valid.gold_version,
        gold_content_sha256=valid.gold_content_sha256,
        digests=("not-hex",),
    )
    with pytest.raises(LeakcheckError, match="lowercase hexadecimal"):
        scan_text("clean text", malformed, SALT)


def test_unsafe_colliding_item_id_is_hashed_in_report(tmp_path: Path) -> None:
    input_path = tmp_path / "unsafe-id.jsonl"
    input_path.write_text(
        json.dumps({"item_id": FAKE_SURFACE, "text": "clean synthetic prose"}) + "\n",
        encoding="utf-8",
    )

    report = scan_file(input_path, manifest_for(FAKE_SURFACE), SALT)

    assert report.collision_count == 1
    assert report.item_ids[0].startswith("sha256:")
    assert FAKE_SURFACE not in canonical_json(report.to_json())
