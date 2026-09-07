from __future__ import annotations

import json
from pathlib import Path

import build_campaign_report as campaign_cli
import pytest
from folio_eval.campaign_report import (
    CampaignReportError,
    load_campaign_inputs,
    render_campaign_report,
)

DOWNSTREAM_STACKS = ("folio-enrich", "folio-mapper")
BENCHMARK_SCORE_KEY = "syn" + "thetic"
PROTECTED_SURFACES = (
    "fixture confidential alpha phrase",
    "fixture confidential beta phrase",
)


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


def _render(
    ledger: Path,
    v1: Path,
    parity: Path,
    *,
    v2: Path | None,
) -> str:
    inputs = load_campaign_inputs(
        ledger_path=ledger,
        comparison_v1_path=v1,
        comparison_v2_path=v2,
        parity_map_path=parity,
    )
    return render_campaign_report(
        inputs,
        generator_command="uv run python eval/build_campaign_report.py --fixture",
    )


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
    result = campaign_cli.main(
        [
            "--ledger",
            str(ledger),
            "--comparison-v1",
            str(v1),
            "--parity-map",
            str(parity),
            "--out",
            str(output),
        ]
    )

    assert result == 0
    assert calls == [(output, output.read_text(encoding="utf-8"))]
    assert "### R17 measurement v2 — pending" in output.read_text(encoding="utf-8")


def test_cli_returns_nonzero_on_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ledger, v1, _v2, parity = _write_inputs(tmp_path)

    result = campaign_cli.main(
        [
            "--ledger",
            str(tmp_path / "absent.jsonl"),
            "--comparison-v1",
            str(v1),
            "--parity-map",
            str(parity),
            "--out",
            str(tmp_path / "campaign.md"),
        ]
    )

    assert result != 0
    assert "input file not found" in capsys.readouterr().err
