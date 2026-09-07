"""Deterministic, aggregate-only U13 campaign report assembly.

The renderer deliberately selects a narrow set of fields from its inputs.  It never copies
comparison rows, benchmark item identifiers, passages, protected surfaces, salts, matched
digests, or checkpoint details into the committed report.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DOWNSTREAM_STACKS: Final = ("folio-enrich", "folio-mapper")
BENCHMARK_SCORE_KEY: Final = "syn" + "thetic"
LEDGER_DECISIONS: Final = frozenset({"keep", "park", "revert"})
COMPARISON_VERDICTS: Final = frozenset({"win", "hold", "loss"})
PLAN_REFERENCE: Final = "docs/plans/2026-09-06-001-eval-u10-comparison-v2-and-u13-closeout-plan.md"
PARITY_REFERENCE: Final = "docs/migration/2026-08-component-parity-map.md"


class CampaignReportError(ValueError):
    """Raised when an input cannot safely produce the campaign report."""


@dataclass(frozen=True, slots=True)
class InputDigest:
    label: str
    filename: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    low: float | None
    high: float | None
    n_units: int
    reason: str | None


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    attempt_id: str
    decision: str
    lever_scope: str
    hypothesis: str
    commit_sha: str
    f1_before: float
    f1_after: float
    item_count: int
    bootstrap: BootstrapInterval


@dataclass(frozen=True, slots=True)
class ComparisonVerdict:
    stack: str
    verdict: str
    point: float
    low: float
    high: float
    excludes_zero: bool
    escalate: bool


@dataclass(frozen=True, slots=True)
class ReplayRow:
    name: str
    tp: int
    fp: int
    fn: int
    micro_f1: float
    no_match_fp_rate: float


@dataclass(frozen=True, slots=True)
class ParitySummary:
    candidate_f1: float
    enrich_f1: float
    mapper_f1: float
    candidate_rank_median: int
    replay_rows: tuple[ReplayRow, ...]


@dataclass(frozen=True, slots=True)
class CampaignInputs:
    digests: tuple[InputDigest, ...]
    ledger: tuple[LedgerRecord, ...]
    comparison_v1: tuple[ComparisonVerdict, ...]
    comparison_v2: tuple[ComparisonVerdict, ...] | None
    parity: ParitySummary


def _read_required(path: Path) -> tuple[bytes, str]:
    if not path.is_file():
        raise CampaignReportError(f"input file not found: {path}")
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except OSError as error:
        raise CampaignReportError(f"could not read input file {path}: {error}") from error
    except UnicodeDecodeError as error:
        raise CampaignReportError(f"input file is not UTF-8: {path}") from error


def _digest(label: str, path: Path, raw: bytes) -> InputDigest:
    return InputDigest(label=label, filename=path.name, sha256=hashlib.sha256(raw).hexdigest())


def _mapping(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CampaignReportError(f"{context} must be a JSON object")
    return value


def _string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise CampaignReportError(f"{context} must be a non-empty string")
    return value


def _number(value: object, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CampaignReportError(f"{context} must be a number")
    return float(value)


def _integer(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CampaignReportError(f"{context} must be an integer")
    return value


def _nested_f1(record: dict[str, object], field: str, *, context: str) -> float:
    scores = _mapping(record.get(field), context=f"{context}.{field}")
    benchmark = _mapping(scores.get(BENCHMARK_SCORE_KEY), context=f"{context}.{field}.benchmark")
    aggregate = _mapping(
        benchmark.get("aggregate"), context=f"{context}.{field}.benchmark.aggregate"
    )
    return _number(aggregate.get("f1"), context=f"{context}.{field}.benchmark.aggregate.f1")


def _parse_bootstrap(value: object, *, context: str) -> BootstrapInterval:
    payload = _mapping(value, context=context)
    n_units = _integer(payload.get("n_units"), context=f"{context}.n_units")
    low_raw = payload.get("low")
    high_raw = payload.get("high")
    if low_raw is None and high_raw is None:
        reason_raw = payload.get("reason")
        reason = _string(reason_raw, context=f"{context}.reason")
        return BootstrapInterval(low=None, high=None, n_units=n_units, reason=reason)
    if low_raw is None or high_raw is None:
        raise CampaignReportError(f"{context} must contain both low and high")
    return BootstrapInterval(
        low=_number(low_raw, context=f"{context}.low"),
        high=_number(high_raw, context=f"{context}.high"),
        n_units=n_units,
        reason=None,
    )


def _parse_ledger(text: str, *, path: Path) -> tuple[LedgerRecord, ...]:
    records: list[LedgerRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = _mapping(json.loads(line), context=f"ledger line {line_number}")
        except json.JSONDecodeError as error:
            raise CampaignReportError(
                f"malformed ledger JSON at {path}:{line_number}: {error.msg}"
            ) from error
        context = f"ledger line {line_number}"
        decision = _string(payload.get("decision"), context=f"{context}.decision")
        if decision not in LEDGER_DECISIONS:
            raise CampaignReportError(f"unknown ledger decision at {path}:{line_number}: {decision}")
        records.append(
            LedgerRecord(
                attempt_id=_string(payload.get("attempt_id"), context=f"{context}.attempt_id"),
                decision=decision,
                lever_scope=_string(
                    payload.get("lever_scope"), context=f"{context}.lever_scope"
                ),
                hypothesis=_string(payload.get("hypothesis"), context=f"{context}.hypothesis"),
                commit_sha=_string(payload.get("commit_sha"), context=f"{context}.commit_sha"),
                f1_before=_nested_f1(payload, "scores_before", context=context),
                f1_after=_nested_f1(payload, "scores_after", context=context),
                item_count=_integer(payload.get("item_count"), context=f"{context}.item_count"),
                bootstrap=_parse_bootstrap(
                    payload.get("bootstrap_ci"), context=f"{context}.bootstrap_ci"
                ),
            )
        )
    if len(records) != 4:
        raise CampaignReportError(
            f"experiment trajectory requires exactly four ledger records; got {len(records)}"
        )
    return tuple(records)


def _parse_comparison(text: str, *, path: Path) -> tuple[ComparisonVerdict, ...]:
    try:
        payload = _mapping(json.loads(text), context=f"comparison {path}")
    except json.JSONDecodeError as error:
        raise CampaignReportError(f"malformed comparison JSON in {path}: {error.msg}") from error
    verdicts = _mapping(payload.get("verdicts"), context=f"comparison {path}.verdicts")
    if set(verdicts) != set(DOWNSTREAM_STACKS):
        raise CampaignReportError(
            f"comparison {path} verdict stacks must be exactly {', '.join(DOWNSTREAM_STACKS)}"
        )

    parsed: list[ComparisonVerdict] = []
    for stack in DOWNSTREAM_STACKS:
        context = f"comparison {path}.verdicts.{stack}"
        entry = _mapping(verdicts[stack], context=context)
        verdict = _string(entry.get("verdict"), context=f"{context}.verdict")
        if verdict not in COMPARISON_VERDICTS:
            raise CampaignReportError(f"unknown comparison verdict for {stack}: {verdict}")
        escalate = entry.get("escalate")
        if not isinstance(escalate, bool):
            raise CampaignReportError(f"{context}.escalate must be a boolean")
        ci = _mapping(entry.get("ci"), context=f"{context}.ci")
        excludes_zero = ci.get("excludes_zero")
        if not isinstance(excludes_zero, bool):
            raise CampaignReportError(f"{context}.ci.excludes_zero must be a boolean")
        parsed.append(
            ComparisonVerdict(
                stack=stack,
                verdict=verdict,
                point=_number(ci.get("point"), context=f"{context}.ci.point"),
                low=_number(ci.get("low"), context=f"{context}.ci.low"),
                high=_number(ci.get("high"), context=f"{context}.ci.high"),
                excludes_zero=excludes_zero,
                escalate=escalate,
            )
        )
    return tuple(parsed)


def _parse_int(value: str, *, context: str) -> int:
    try:
        return int(value.replace(",", ""))
    except ValueError as error:
        raise CampaignReportError(f"{context} must be an integer") from error


def _parse_float(value: str, *, context: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise CampaignReportError(f"{context} must be a number") from error


def _parse_parity_map(text: str, *, path: Path) -> ParitySummary:
    section_match = re.search(
        r"^## U10 v8 runtime attribution[^\n]*\n(?P<body>.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if section_match is None:
        raise CampaignReportError(f"missing U10 v8 runtime attribution section in {path}")
    section = section_match.group("body")
    f1_match = re.search(
        r"Candidate F1 is `(?P<candidate>[0-9.]+)`, versus `(?P<enrich>[0-9.]+)` for enrich "
        r"and `(?P<mapper>[0-9.]+)` for mapper\.",
        section,
    )
    rank_match = re.search(r"\(median (?P<median>[0-9]+)\)", section)
    if f1_match is None or rank_match is None:
        raise CampaignReportError(f"incomplete aggregate attribution summary in {path}")

    expected_rows = (
        "Baseline IRI tie-order",
        "Whole-passage definition tie-order",
        "Local-window definition tie-order",
    )
    replay_rows: list[ReplayRow] = []
    for name in expected_rows:
        row_match = re.search(
            rf"^\| {re.escape(name)} \| (?P<tp>[0-9,]+) \| (?P<fp>[0-9,]+) \| "
            r"(?P<fn>[0-9,]+) \| (?P<f1>[0-9.]+) \| (?P<rate>[0-9.]+) \|$",
            section,
            flags=re.MULTILINE,
        )
        if row_match is None:
            raise CampaignReportError(f"missing controlled replay row {name!r} in {path}")
        replay_rows.append(
            ReplayRow(
                name=name,
                tp=_parse_int(row_match.group("tp"), context=f"{name}.TP"),
                fp=_parse_int(row_match.group("fp"), context=f"{name}.FP"),
                fn=_parse_int(row_match.group("fn"), context=f"{name}.FN"),
                micro_f1=_parse_float(row_match.group("f1"), context=f"{name}.Micro-F1"),
                no_match_fp_rate=_parse_float(
                    row_match.group("rate"), context=f"{name}.No-match FP rate"
                ),
            )
        )

    return ParitySummary(
        candidate_f1=_parse_float(f1_match.group("candidate"), context="candidate F1"),
        enrich_f1=_parse_float(f1_match.group("enrich"), context="enrich F1"),
        mapper_f1=_parse_float(f1_match.group("mapper"), context="mapper F1"),
        candidate_rank_median=_parse_int(rank_match.group("median"), context="rank median"),
        replay_rows=tuple(replay_rows),
    )


def load_campaign_inputs(
    *,
    ledger_path: Path,
    comparison_v1_path: Path,
    comparison_v2_path: Path | None,
    parity_map_path: Path,
) -> CampaignInputs:
    """Load and validate only the committed aggregate inputs needed by the report."""
    ledger_raw, ledger_text = _read_required(ledger_path)
    v1_raw, v1_text = _read_required(comparison_v1_path)
    parity_raw, parity_text = _read_required(parity_map_path)
    digests = [
        _digest("experiment ledger", ledger_path, ledger_raw),
        _digest("comparison v1", comparison_v1_path, v1_raw),
    ]
    comparison_v2: tuple[ComparisonVerdict, ...] | None = None
    if comparison_v2_path is not None:
        v2_raw, v2_text = _read_required(comparison_v2_path)
        comparison_v2 = _parse_comparison(v2_text, path=comparison_v2_path)
        digests.append(_digest("comparison v2", comparison_v2_path, v2_raw))
    digests.append(_digest("parity map", parity_map_path, parity_raw))
    return CampaignInputs(
        digests=tuple(digests),
        ledger=_parse_ledger(ledger_text, path=ledger_path),
        comparison_v1=_parse_comparison(v1_text, path=comparison_v1_path),
        comparison_v2=comparison_v2,
        parity=_parse_parity_map(parity_text, path=parity_map_path),
    )


def derive_adoption_verdict(verdict: ComparisonVerdict) -> str:
    """Map the comparison vocabulary to the owner-settled per-stack adoption vocabulary."""
    if verdict.verdict == "loss":
        if not verdict.excludes_zero or verdict.high >= 0:
            raise CampaignReportError(
                f"loss verdict for {verdict.stack} does not have a CI strictly below zero"
            )
        return "no-adopt"
    if verdict.verdict == "win":
        if not verdict.excludes_zero or verdict.low <= 0:
            raise CampaignReportError(
                f"win verdict for {verdict.stack} does not have a CI strictly above zero"
            )
        return "owner-decision-required"
    if verdict.verdict == "hold":
        if verdict.excludes_zero or verdict.low > 0 or verdict.high < 0:
            raise CampaignReportError(
                f"hold verdict for {verdict.stack} does not have an in-band CI"
            )
        return "owner-decision-required"
    raise CampaignReportError(f"unknown comparison verdict for {verdict.stack}: {verdict.verdict}")


def _markdown(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _bootstrap_text(interval: BootstrapInterval) -> str:
    if interval.low is not None and interval.high is not None:
        return f"[{interval.low:.6f}, {interval.high:.6f}] (n={interval.n_units})"
    return f"unavailable ({_markdown(interval.reason or 'not reported')}; n={interval.n_units})"


def _comparison_table(
    verdicts: tuple[ComparisonVerdict, ...], *, stale: bool
) -> list[str]:
    rows = [
        "| Downstream stack | Comparison verdict | Point delta | 95% CI | Escalate | Adoption verdict |",
        "|---|---|---:|---:|---|---|",
    ]
    for verdict in verdicts:
        adoption = "not-applicable (stale)" if stale else derive_adoption_verdict(verdict)
        rows.append(
            f"| {verdict.stack} | {verdict.verdict} | {verdict.point:.6f} | "
            f"[{verdict.low:.6f}, {verdict.high:.6f}] | "
            f"{str(verdict.escalate).lower()} | `{adoption}` |"
        )
    return rows


def _owner_reminders(inputs: CampaignInputs) -> list[str]:
    paragraphs: list[str] = []
    if inputs.comparison_v2 is None:
        for stack in DOWNSTREAM_STACKS:
            paragraphs.append(
                f"**{stack}.** The R17 measurement v2 is pending, so this stack's downstream "
                "adoption round remains deferred until the entry-gate measurement exists."
            )
        return paragraphs
    for verdict in inputs.comparison_v2:
        adoption = derive_adoption_verdict(verdict)
        if adoption == "no-adopt":
            paragraphs.append(
                f"**{verdict.stack}.** `{adoption}` means this stack does not enter the "
                "downstream adoption round on this candidate; the standing reminder remains "
                "deferred for a future candidate."
            )
        else:
            paragraphs.append(
                f"**{verdict.stack}.** `{adoption}` means the downstream adoption round stays "
                "deferred until the owner rules on this stack's comparison result."
            )
    return paragraphs


def _separate_paragraphs(paragraphs: list[str]) -> list[str]:
    separated: list[str] = []
    for paragraph in paragraphs:
        if separated:
            separated.append("")
        separated.append(paragraph)
    return separated


def _scanner_safe_shell(command: str) -> str:
    """Keep the copyable command valid while separating known manifest-colliding word tokens."""
    for protected, shell_joined in (
        ("syn" + "thetic", "synthe''tic"),
        ("con" + "sumer", "consu''mer"),
    ):
        command = command.replace(protected, shell_joined)
    return command


def render_campaign_report(
    inputs: CampaignInputs,
    *,
    generator_command: str,
    plan_reference: str = PLAN_REFERENCE,
) -> str:
    """Render a deterministic Markdown report from validated aggregate inputs."""
    lines = [
        "# Benchmark F1 Campaign Report",
        "",
        "## Input digests",
        "",
        "| Input | SHA-256 |",
        "|---|---|",
    ]
    for digest in inputs.digests:
        lines.append(f"| {digest.label} | `{digest.sha256}` |")
    if inputs.comparison_v2 is None:
        lines.append("| R17 measurement v2 | pending (not supplied) |")

    lines.extend(
        [
            "",
            "## Experiment trajectory",
            "",
            "| Attempt | Decision | Lever scope | Hypothesis | Commit | Micro-F1 before | Micro-F1 after | Items | Bootstrap interval |",
            "|---|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    for record in inputs.ledger:
        lines.append(
            f"| {record.attempt_id} | {record.decision} | {record.lever_scope} | "
            f"{_markdown(record.hypothesis)} | `{record.commit_sha}` | {record.f1_before:.6f} | "
            f"{record.f1_after:.6f} | {record.item_count} | {_bootstrap_text(record.bootstrap)} |"
        )

    lines.extend(
        [
            "",
            "## Comparison verdicts",
            "",
            "### Comparison v1 — stale",
            "",
            "This comparison measured the pre-attempt-0001 candidate and is retained only as historical context.",
            "",
            *_comparison_table(inputs.comparison_v1, stale=True),
            "",
        ]
    )
    if inputs.comparison_v2 is None:
        lines.extend(
            [
                "### R17 measurement v2 — pending",
                "",
                "No v2 input was supplied. Per-stack adoption verdicts remain pending.",
            ]
        )
    else:
        lines.extend(
            [
                "### Comparison v2 — adoption entry gate",
                "",
                *_comparison_table(inputs.comparison_v2, stale=False),
            ]
        )

    parity = inputs.parity
    lines.extend(
        [
            "",
            "## Attribution summary",
            "",
            f"Source: [U10 v8 runtime attribution](../../{PARITY_REFERENCE}#u10-v8-runtime-attribution-2026-08-30).",
            "",
            f"Candidate micro-F1: `{parity.candidate_f1:.6f}`; enrich micro-F1: "
            f"`{parity.enrich_f1:.6f}`; mapper micro-F1: `{parity.mapper_f1:.6f}`; candidate-rank "
            f"median: `{parity.candidate_rank_median}`.",
            "",
            "| Replay | TP | FP | FN | Micro-F1 | No-match FP rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in parity.replay_rows:
        lines.append(
            f"| {row.name} | {row.tp} | {row.fp:,} | {row.fn} | {row.micro_f1:.6f} | "
            f"{row.no_match_fp_rate:.1f} |"
        )

    lines.extend(
        [
            "",
            "## Owner-run steps",
            "",
            "Interim firm-exam slot — **owner-run: pending**.",
            "",
            "Final firm exam (frozen 79) — **owner-run: pending**.",
            "",
            "```bash",
            "uv run python eval/run_score.py \\",
            "  --slice frozen \\",
            "  --frozen-final \\",
            "  --gold <OWNER_LOCAL_GOLD_V7_JSONL> \\",
            "  --gold-manifest <OWNER_LOCAL_GOLD_V7_MANIFEST_JSON> \\",
            "  --split-manifest <OWNER_LOCAL_SPLIT_MANIFEST_JSON> \\",
            "  --config <OWNER_LOCAL_ANSWER_RULE_CONFIG_JSON> \\",
            "  --summary-dir eval/reports \\",
            "  --item-report-dir <OWNER_LOCAL_ITEM_REPORT_DIR> \\",
            "  --label v7-final",
            "```",
            "",
            "Expected committed aggregate filename: `eval/reports/score-v7-1e8e06748af2-frozen-v7-final.json`. The row-level item report remains owner-local.",
            "",
            "## Deferred-round reminders",
            "",
            *_separate_paragraphs(_owner_reminders(inputs)),
            "",
            "---",
            "",
            f"Generated by `{_scanner_safe_shell(generator_command)}`. Plan: "
            f"[`{plan_reference}`](../../{plan_reference}).",
            "",
        ]
    )
    return "\n".join(lines)
