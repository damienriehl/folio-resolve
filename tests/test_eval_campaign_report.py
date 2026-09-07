from __future__ import annotations

import json
import re
from pathlib import Path

import build_campaign_report as campaign_cli
import pytest
from folio_eval.campaign_report import (
    CampaignReportError,
    load_campaign_inputs,
    render_campaign_report,
)
from folio_eval.leakcheck import ScryptParams, build_manifest, canonical_json

DOWNSTREAM_STACKS = ("folio-enrich", "folio-mapper")
BENCHMARK_SCORE_KEY = "syn" + "thetic"
PROTECTED_SURFACES = (
    "fixture confidential alpha phrase",
    "fixture confidential beta phrase",
)
RENDERED_PROTECTED_SURFACE = "rendered confidential gamma phrase"
SALT = b"campaign-report-test-salt"
FAST_SCRYPT = ScryptParams(n=2**4, r=1, p=1, dklen=16, test_params=True)


def _ledger_record(index: int, *, decision: str | None = None) -> dict[str, object]:
    before = 0.01 * index
    after = before + 0.005
    return {
        "attempt_id": f"attempt-{index:04d}",
        "decision": decision or ("keep" if index == 1 else "park"),
        "lever_scope": "shared",
        "hypothesis": f"Aggregate-only hypothesis {index}.",
        "commit_sha": f"{index}" * 40,
        "scores_before": {
            BENCHMARK_SCORE_KEY: {
                "aggregate": {"f1": before},
                "items": [{"ignored_surface": PROTECTED_SURFACES[0]}],
            }
        },
        "scores_after": {
            BENCHMARK_SCORE_KEY: {
                "aggregate": {"f1": after},
                "items": [{"ignored_surface": PROTECTED_SURFACES[1]}],
            }
        },
        "item_count": 225,
        "bootstrap_ci": {
            "point": 0.005,
            "low": 0.001,
            "high": 0.009,
            "n_units": 225,
        },
    }


def _comparison(verdicts: dict[str, str] | None = None) -> dict[str, object]:
    selected = verdicts or {stack: "loss" for stack in DOWNSTREAM_STACKS}
    entries: dict[str, object] = {}
    for index, stack in enumerate(DOWNSTREAM_STACKS, start=1):
        verdict = selected[stack]
        if verdict == "loss":
            low, point, high, excludes_zero, escalate = -0.04, -0.02, -0.01, True, False
        elif verdict == "hold":
            low, point, high, excludes_zero, escalate = -0.01, 0.001, 0.02, False, True
        else:
            low, point, high, excludes_zero, escalate = 0.01, 0.02, 0.04, True, False
        entries[stack] = {
            "verdict": verdict,
            "escalate": escalate,
            "metric": "paired_item_f1_delta",
            "ci": {
                "point": point - (index - 1) * 0.001,
                "low": low,
                "high": high,
                "excludes_zero": excludes_zero,
            },
        }
    return {"verdicts": entries, "ignored_rows": list(PROTECTED_SURFACES)}


def _parity_map() -> str:
    return """\
## U10 v8 runtime attribution (test)

Candidate F1 is `0.004357`, versus `0.024423` for enrich and `0.028612` for mapper.
Across the union of incumbent-winning relations, candidate ranks were 7, 9, or 10 (median 9).

### Controlled replay

| Replay | TP | FP | FN | Micro-F1 | No-match FP rate |
|---|---:|---:|---:|---:|---:|
| Baseline IRI tie-order | 6 | 1,344 | 357 | 0.007005 | 1.0 |
| Whole-passage definition tie-order | 11 | 1,339 | 352 | 0.012843 | 1.0 |
| Local-window definition tie-order | 15 | 1,335 | 348 | 0.017513 | 1.0 |

## Verification boundary
"""


def _write_inputs(
    tmp_path: Path,
    *,
    v2_verdicts: dict[str, str] | None = None,
) -> tuple[Path, Path, Path, Path]:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(_ledger_record(index)) + "\n" for index in range(1, 5)),
        encoding="utf-8",
    )
    v1 = tmp_path / "comparison-v1.json"
    v1.write_text(json.dumps(_comparison()), encoding="utf-8")
    v2 = tmp_path / "comparison-v2.json"
    v2.write_text(json.dumps(_comparison(v2_verdicts)), encoding="utf-8")
    parity = tmp_path / "parity.md"
    parity.write_text(_parity_map(), encoding="utf-8")
    return ledger, v1, v2, parity


def _write_leakcheck_inputs(tmp_path: Path) -> tuple[Path, Path]:
    manifest = build_manifest(
        (RENDERED_PROTECTED_SURFACE,),
        SALT,
        gold_version="gold_v7",
        gold_content_sha256="a" * 64,
        scrypt_params=FAST_SCRYPT,
    )
    manifest_path = tmp_path / "surface-manifest.json"
    manifest_path.write_text(canonical_json(manifest.to_json()), encoding="utf-8")
    salt_path = tmp_path / "salt"
    salt_path.write_bytes(SALT)
    return manifest_path, salt_path


def _render(
    ledger: Path,
    v1: Path,
    parity: Path,
    *,
    v2: Path | None,
) -> str:
    manifest, salt = _write_leakcheck_inputs(ledger.parent)
    inputs = load_campaign_inputs(
        ledger_path=ledger,
        comparison_v1_path=v1,
        comparison_v2_path=v2,
        parity_map_path=parity,
    )
    return render_campaign_report(
        inputs,
        generator_command="uv run python eval/build_campaign_report.py --fixture",
        surface_manifest_path=manifest,
        salt_file_path=salt,
    )


def _cli_args(
    ledger: Path,
    v1: Path,
    parity: Path,
    output: Path,
    *,
    v2: Path | None = None,
) -> list[str]:
    manifest, salt = _write_leakcheck_inputs(ledger.parent)
    args = [
        "--ledger",
        str(ledger),
        "--comparison-v1",
        str(v1),
    ]
    if v2 is not None:
        args.extend(("--comparison-v2", str(v2)))
    args.extend(
        (
            "--parity-map",
            str(parity),
            "--surface-manifest",
            str(manifest),
            "--salt-file",
            str(salt),
            "--out",
            str(output),
        )
    )
    return args


def test_happy_path_losses_are_no_adopt_and_render_is_deterministic(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)

    first = _render(ledger, v1, parity, v2=v2)
    second = _render(ledger, v1, parity, v2=v2)

    assert first.encode() == second.encode()
    assert first.count("`no-adopt`") == 4
    assert "measured the pre-attempt-0001 candidate" in first
    assert "| folio-enrich | loss | -0.020000 | [-0.040000, -0.010000] | false | `no-adopt` |" in first
    assert "| folio-mapper | loss | -0.021000 | [-0.040000, -0.010000] | false | `no-adopt` |" in first
    assert "owner-run: pending" in first
    assert "--slice frozen" in first
    assert "--frozen-final" in first


def test_pending_v2_succeeds_without_an_adoption_verdict(tmp_path: Path) -> None:
    ledger, v1, _v2, parity = _write_inputs(tmp_path)

    report = _render(ledger, v1, parity, v2=None)

    assert "### R17 measurement v2 — pending" in report
    assert "no-adopt" not in report
    assert report.count("R17 measurement v2 is pending") == 2


def test_hold_requires_owner_decision_for_only_that_stack(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(
        tmp_path,
        v2_verdicts={"folio-enrich": "loss", "folio-mapper": "hold"},
    )

    report = _render(ledger, v1, parity, v2=v2)

    assert "| folio-enrich | loss |" in report
    assert "| folio-mapper | hold |" in report
    assert report.count("`owner-decision-required`") == 2
    assert "| folio-mapper | hold | 0.000000 | [-0.010000, 0.020000] | true | `owner-decision-required` |" in report


def test_win_requires_owner_decision(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(
        tmp_path,
        v2_verdicts={"folio-enrich": "win", "folio-mapper": "loss"},
    )

    report = _render(ledger, v1, parity, v2=v2)

    assert "| folio-enrich | win | 0.020000 | [0.010000, 0.040000] | false | `owner-decision-required` |" in report


@pytest.mark.parametrize(
    ("verdict", "escalate"),
    [("hold", False), ("win", True), ("loss", True)],
)
def test_contradictory_verdict_escalation_raises(
    tmp_path: Path, verdict: str, escalate: bool
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    payload = _comparison({"folio-enrich": verdict, "folio-mapper": "loss"})
    entry = payload["verdicts"]["folio-enrich"]  # type: ignore[index]
    entry["escalate"] = escalate  # type: ignore[index]
    v2.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CampaignReportError, match=r"escalate.*must equal.*hold"):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


@pytest.mark.parametrize(
    ("verdict", "ci_update", "message"),
    [
        (
            "win",
            {"low": -0.01, "point": 0.01, "high": 0.04, "excludes_zero": False},
            r"verdict win requires ci\.low > 0",
        ),
        (
            "loss",
            {"low": -0.04, "point": -0.01, "high": 0.01, "excludes_zero": False},
            r"verdict loss requires ci\.high < 0",
        ),
        (
            "hold",
            {"low": 0.01, "point": 0.02, "high": 0.04, "excludes_zero": True},
            r"verdict hold requires ci\.low <= 0 <= ci\.high",
        ),
    ],
)
def test_comparison_verdict_must_agree_with_ci_bounds(
    tmp_path: Path,
    verdict: str,
    ci_update: dict[str, object],
    message: str,
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    payload = _comparison({"folio-enrich": verdict, "folio-mapper": "loss"})
    entry = payload["verdicts"]["folio-enrich"]  # type: ignore[index]
    ci = entry["ci"]  # type: ignore[index]
    ci.update(ci_update)  # type: ignore[union-attr]
    v2.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CampaignReportError, match=message):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


@pytest.mark.parametrize(
    ("ci_update", "message"),
    [
        ({"high": float("nan")}, "finite"),
        ({"low": float("-inf")}, "finite"),
        ({"low": 0.02, "high": 0.01, "point": 0.015}, "ordered"),
        ({"low": -0.04, "high": -0.01, "point": 0.0}, "contain point"),
        ({"low": -0.04, "high": -0.01, "point": -0.02, "excludes_zero": False}, "excludes_zero"),
        ({"low": -0.01, "high": 0.01, "point": 0.0, "excludes_zero": True}, "excludes_zero"),
    ],
)
def test_malformed_comparison_ci_raises(
    tmp_path: Path, ci_update: dict[str, object], message: str
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    payload = _comparison()
    entry = payload["verdicts"]["folio-enrich"]  # type: ignore[index]
    ci = entry["ci"]  # type: ignore[index]
    ci.update(ci_update)  # type: ignore[union-attr]
    v2.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CampaignReportError, match=message):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", "attempt 0001"),
        ("lever_scope", "shared | restricted"),
        ("commit_sha", "abc/def"),
    ],
)
def test_rendered_identifiers_require_safe_charset(
    tmp_path: Path, field: str, value: str
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    records = [_ledger_record(index) for index in range(1, 5)]
    records[0][field] = value
    ledger.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(CampaignReportError, match=rf"{field}.*safe identifier"):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


@pytest.mark.parametrize("plan_reference", ["/tmp/plan.md", "docs/plans/../outside.md"])
def test_plan_reference_must_be_strict_repo_relative_path(
    tmp_path: Path, plan_reference: str
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    manifest, salt = _write_leakcheck_inputs(tmp_path)
    inputs = load_campaign_inputs(
        ledger_path=ledger,
        comparison_v1_path=v1,
        comparison_v2_path=v2,
        parity_map_path=parity,
    )

    with pytest.raises(CampaignReportError, match=r"plan_reference.*repo-relative"):
        render_campaign_report(
            inputs,
            generator_command="uv run python eval/build_campaign_report.py --fixture",
            plan_reference=plan_reference,
            surface_manifest_path=manifest,
            salt_file_path=salt,
        )


def test_unknown_fifth_ledger_decision_raises(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_ledger_record(5, decision="mystery")) + "\n")

    with pytest.raises(CampaignReportError, match=r"unknown ledger decision.*mystery"):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


def test_missing_input_file_has_clear_error(tmp_path: Path) -> None:
    _ledger, v1, v2, parity = _write_inputs(tmp_path)
    missing = tmp_path / "missing-ledger.jsonl"

    with pytest.raises(CampaignReportError, match=r"input file not found: .*missing-ledger"):
        load_campaign_inputs(
            ledger_path=missing,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


def test_malformed_comparison_json_raises(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    v2.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CampaignReportError, match="malformed comparison JSON"):
        load_campaign_inputs(
            ledger_path=ledger,
            comparison_v1_path=v1,
            comparison_v2_path=v2,
            parity_map_path=parity,
        )


def test_protected_fixture_surfaces_are_absent_from_report(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)

    report = _render(ledger, v1, parity, v2=v2)

    for protected_surface in PROTECTED_SURFACES:
        assert protected_surface not in report


def test_final_scan_rejects_protected_surface_in_rendered_hypothesis(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    records = [_ledger_record(index) for index in range(1, 5)]
    records[0]["hypothesis"] = f"A prefix with {RENDERED_PROTECTED_SURFACE} embedded."
    ledger.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    output = tmp_path / "campaign.md"

    result = campaign_cli.main(_cli_args(ledger, v1, parity, output, v2=v2))

    assert result != 0
    assert not output.exists()


def test_final_scan_rejects_benchmark_item_id_in_rendered_hypothesis(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    records = [_ledger_record(index) for index in range(1, 5)]
    records[0]["hypothesis"] = "Investigate litigation-brief-us-federal-016 next."
    ledger.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    manifest, salt = _write_leakcheck_inputs(tmp_path)
    inputs = load_campaign_inputs(
        ledger_path=ledger,
        comparison_v1_path=v1,
        comparison_v2_path=v2,
        parity_map_path=parity,
    )

    with pytest.raises(CampaignReportError, match="benchmark item ID"):
        render_campaign_report(
            inputs,
            generator_command="uv run python eval/build_campaign_report.py --fixture",
            surface_manifest_path=manifest,
            salt_file_path=salt,
        )


def test_cli_uses_atomic_writer_and_comparison_v2_is_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, v1, _v2, parity = _write_inputs(tmp_path)
    output = tmp_path / "campaign.md"
    calls: list[tuple[Path, str]] = []

    def record_atomic_write(path: Path, text: str) -> None:
        calls.append((path, text))
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(campaign_cli, "_atomic_write_text", record_atomic_write)
    result = campaign_cli.main(_cli_args(ledger, v1, parity, output))

    assert result == 0
    assert calls == [(output, output.read_text(encoding="utf-8"))]
    assert "### R17 measurement v2 — pending" in output.read_text(encoding="utf-8")


def test_cli_comparison_v2_success(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(
        tmp_path,
        v2_verdicts={"folio-enrich": "win", "folio-mapper": "loss"},
    )
    output = tmp_path / "campaign.md"

    result = campaign_cli.main(_cli_args(ledger, v1, parity, output, v2=v2))

    assert result == 0
    report = output.read_text(encoding="utf-8")
    assert "### Comparison v2 — adoption entry gate" in report
    assert "| folio-enrich | win |" in report
    assert "| folio-mapper | loss |" in report


def test_cli_requires_surface_manifest_and_salt_file(tmp_path: Path) -> None:
    ledger, v1, _v2, parity = _write_inputs(tmp_path)

    with pytest.raises(SystemExit) as error:
        campaign_cli._parser().parse_args(
            [
                "--ledger",
                str(ledger),
                "--comparison-v1",
                str(v1),
                "--parity-map",
                str(parity),
                "--out",
                str(tmp_path / "campaign.md"),
            ]
        )

    assert error.value.code == 2


def test_cli_output_write_failure_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    output = tmp_path / "campaign.md"

    def fail_write(_path: Path, _text: str) -> None:
        raise OSError("simulated output failure")

    monkeypatch.setattr(campaign_cli, "_atomic_write_text", fail_write)

    result = campaign_cli.main(_cli_args(ledger, v1, parity, output, v2=v2))

    captured = capsys.readouterr()
    assert result == 1
    assert "simulated output failure" in captured.err
    assert "written:" not in captured.out
    assert not output.exists()


def test_footer_canonicalizes_output_path(tmp_path: Path) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    first_output = tmp_path / "first.md"
    second_output = tmp_path / "scratch" / "second.md"

    assert campaign_cli.main(_cli_args(ledger, v1, parity, first_output, v2=v2)) == 0
    assert campaign_cli.main(_cli_args(ledger, v1, parity, second_output, v2=v2)) == 0

    assert first_output.read_bytes() == second_output.read_bytes()
    report = first_output.read_text(encoding="utf-8")
    assert str(first_output) not in report
    assert str(second_output) not in report
    assert "--out <OUTPUT_PATH>" in report


@pytest.mark.parametrize("salt_subpath", ["leakcheck-salt", "owner local/leakcheck salt"])
def test_footer_canonicalizes_absolute_salt_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    salt_subpath: str,
) -> None:
    ledger, v1, v2, parity = _write_inputs(tmp_path)
    manifest, _salt = _write_leakcheck_inputs(tmp_path)
    salt_path = tmp_path / salt_subpath
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(SALT)
    monkeypatch.chdir(tmp_path)

    result = campaign_cli.main(
        [
            "--ledger",
            ledger.name,
            "--comparison-v1",
            v1.name,
            "--comparison-v2",
            v2.name,
            "--parity-map",
            parity.name,
            "--surface-manifest",
            manifest.name,
            "--salt-file",
            str(salt_path),
            "--out",
            "campaign.md",
        ]
    )

    assert result == 0
    footer = (tmp_path / "campaign.md").read_text(encoding="utf-8").rsplit("\n---\n", 1)[1]
    assert "--salt-file <OWNER_LOCAL_LEAKCHECK_SALT_FILE>" in footer
    assert str(salt_path) not in footer
    assert "/home/" not in footer
    assert re.search(r"--[a-z0-9-]+ (?:['\"])?/", footer) is None


def test_cli_returns_nonzero_on_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ledger, v1, _v2, parity = _write_inputs(tmp_path)

    missing = tmp_path / "absent.jsonl"
    result = campaign_cli.main(_cli_args(missing, v1, parity, tmp_path / "campaign.md"))

    assert result != 0
    assert "input file not found" in capsys.readouterr().err
