"""Offline recall loss report, item bootstrap, and mechanical next-lever rule."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .experiment import (
    DEFAULT_PENDING_PATH,
    DEFAULT_SYNTHETIC_EXPERIMENTS_LOG,
    ExperimentRecord,
    SliceOutcome,
    finish_attempt,
    start_attempt,
)
from .leakcheck import Manifest, _atomic_write_text, load_manifest, scan_json_value, scan_text
from .recall_consumers import APP_STAGE_DISPLAY_LABELS, display_app_stages
from .verifier_report import require_pristine

ROOT = Path(__file__).resolve().parents[2]
SEED = 20260927
RESAMPLES = 4000
GATES = ("blocklist", "place_gate", "short_label_gate", "score_floor")
GUARDRAILS = (
    "The next lever must raise recall at depth 100 and end-to-end F1 with a paired "
    "item-bootstrap 95% interval above zero, while keeping precision at least 3.3%. "
    "The shortlist stays 100 deep. Adoption in folio-enrich or folio-mapper also "
    "requires a paired item-bootstrap 95% interval of F1 gain above that app arm's baseline."
)


def load_bound(path: Path, sha256: str, attribution_sha256: str | None = None) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("input SHA-256 mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    if attribution_sha256 is not None and value.get("attribution_sha256") != attribution_sha256:
        raise ValueError("input bound to a different attribution SHA-256")
    return value


def agreement_concentration(relations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Paired percentile bootstrap: resample whole passages, retaining all their relations."""
    grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row in relations:
        if row["agreement"] not in (2, 3):
            raise ValueError("agreement must be two or three")
        counts = grouped[row["item_id"]]
        two, miss = int(row["agreement"] == 2), int(row["stage"] != "top_100")
        for i, n in enumerate((1, two, miss, two * miss)):
            counts[i] += n
    units = [grouped[key] for key in sorted(grouped)]

    def statistic(sample: Sequence[list[int]]) -> tuple[float, float, float]:
        all_n, all_two, miss_n, miss_two = (sum(u[i] for u in sample) for i in range(4))
        base = all_two / all_n if all_n else 0.0
        missed = miss_two / miss_n if miss_n else 0.0
        # No misses provide no evidence of positive concentration.
        return base, missed, missed - base if miss_n else 0.0

    base, missed, difference = statistic(units)
    rng = random.Random(SEED)
    draws = (
        sorted(statistic(rng.choices(units, k=len(units)))[2] for _ in range(RESAMPLES))
        if units
        else [0.0] * RESAMPLES
    )
    low, high = draws[int(0.025 * (RESAMPLES - 1))], draws[int(0.975 * (RESAMPLES - 1))]
    return dict(
        base_share=base,
        miss_share=missed,
        difference=difference,
        low=low,
        high=high,
        concentrated=difference > 0 and low > 0,
        seed=SEED,
        resamples=RESAMPLES,
        item_count=len(units),
    )


def _committed_misses(arm: Mapping[str, Any]) -> int:
    cross = arm["resolve_misses"]
    if "relations" in cross:
        return len({(r["item_id"], r["iri"]) for r in cross["relations"] if r["committed"]})
    return sum(int(row["committed"]) for row in cross["by_stage"].values())


def app_arms(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Thin adapter for U3b's public aggregate and U4's full return values."""
    arms = payload["arms"]
    if isinstance(arms, list):
        return [dict(arm) for arm in arms]
    result = []
    for key, value in sorted(arms.items()):
        if "stack" in value and "lane" in value:
            result.append(dict(value))
            continue
        stack = next(
            (name for name in ("folio-enrich", "folio-mapper") if key.startswith(name + "-")), None
        )
        if stack is None:
            raise ValueError("unknown app arm")
        model = key[len(stack) + 1 :]
        if model not in ("deterministic", "gemini-3-flash-preview", "gpt-6-luna"):
            raise ValueError("unknown app model")
        result.append(
            dict(
                value,
                stack=stack,
                lane="deterministic" if model == "deterministic" else "llm-on",
                model=None if model == "deterministic" else model,
            )
        )
    return result


def choose_lever(
    attribution: Mapping[str, Any],
    apps: Sequence[Mapping[str, Any]],
    embedding: Mapping[str, Any],
    llm: Mapping[str, Any],
) -> dict[str, Any]:
    """R9/R10; exact integer bars, deterministic ties, no I/O or input mutation."""
    rows = attribution["relations"]
    counts = Counter(row["stage"] for row in rows)
    misses = sum(n for stage, n in counts.items() if stage != "top_100")
    concentration = agreement_concentration(rows)
    base: dict[str, Any] = dict(lever=None, route="Damien", agreement=concentration)
    if concentration["concentrated"]:
        return dict(
            base, reason="Misses have an excess 2-of-3 share with a 95% interval above zero."
        )
    candidates = []
    for arm in apps:
        if arm["lane"] not in ("deterministic", "incumbent"):
            continue
        # U4 records first loss, not first production. Only committed gold proves
        # usable recovery; do not relabel a loss stage as the stage that recovered it.
        recovered_count = _committed_misses(arm)
        if misses and 10 * recovered_count >= misses:
            candidates.append((recovered_count, arm["stack"]))
    if candidates:
        n, stack = sorted(candidates, key=lambda x: (-x[0], x[1]))[0]
        return dict(
            base,
            lever="app_stage",
            route="brainstorm",
            app=stack,
            stage="committed",
            recovered=n,
            recovery_share=n / misses,
            reason="Port or improve the no-LLM path to committed output; its recovery meets the 10% bar.",
        )
    rankings = embedding["rankings"]
    recovered = max(rankings[arm]["50"]["recovered_count"] for arm in rankings)
    never = embedding["never_produced_count"]
    local_ok = never > 0 and recovered * 4 >= never
    # AE3 precedes fallback to a smaller category. "Substantial" means at
    # least ceil(25% of never-produced relations), the same count as R9's bar.
    ranking = counts["rank_101_200"] + counts["rank_below_200"]
    gates = sum(counts[g] for g in GATES)
    llm_never_recovery = max(
        [llm.get("recovered", 0)]
        + [
            sum(
                r["committed"] and r["resolve_stage"] == "never_produced"
                for r in arm["resolve_misses"]["relations"]
            )
            if "relations" in arm["resolve_misses"]
            else arm["resolve_misses"]["by_stage"].get("never_produced", {}).get("committed", 0)
            for arm in apps
            if arm["lane"] == "llm-on"
        ]
    )
    if (
        counts["never_produced"] > max(ranking, gates)
        and not local_ok
        and 4 * llm_never_recovery >= never
        and never > 0
    ):
        return dict(
            base,
            reason="Never-produced misses dominate; only LLM recovery meets the 25% bar; ask Damien.",
        )
    choices = [
        ("ranking", counts["rank_101_200"] + counts["rank_below_200"]),
        ("gate_tuning", sum(counts[g] for g in GATES)),
        ("local_source", counts["never_produced"] if local_ok else 0),
    ]
    lever, count = max(choices, key=lambda pair: pair[1])
    if count:
        return dict(
            base,
            lever=lever,
            route="brainstorm",
            recoverable_stage_count=count,
            reason="Largest eligible share of misses; local search requires 25% recovery at depth 50.",
        )
    llm_recovery = llm.get("recovered", 0) or any(
        arm["lane"] == "llm-on" and _committed_misses(arm) > 0 for arm in apps
    )
    return dict(
        base,
        reason="Only LLM recovery qualifies; ask Damien."
        if llm_recovery
        else "No local lever qualifies; ask Damien.",
    )


def _stages(rows: Mapping[str, Any], total: int) -> dict[str, Any]:
    return {
        stage: dict(
            count=row["count"],
            share=row["count"] / total if total else 0.0,
            by_agreement={
                str(n): dict(
                    count=row["by_agreement"][str(n)],
                    share=row["by_agreement"][str(n)] / row["count"] if row["count"] else 0.0,
                )
                for n in (2, 3)
            },
        )
        for stage, row in rows.items()
    }


def validate_app_arms(apps: Sequence[Mapping[str, Any]], attribution: Mapping[str, Any]) -> None:
    """Require exactly six full scoreable/control cohorts before publication."""
    expected = {
        (stack, model)
        for stack in ("folio-enrich", "folio-mapper")
        for model in (None, "gemini-3-flash-preview", "gpt-6-luna")
    }
    identities = [(a.get("stack"), a.get("model", a.get("llm_model"))) for a in apps]
    if len(identities) != 6 or set(identities) != expected:
        raise ValueError("report requires exactly six unique complete app arms")
    total = attribution["gold_relation_count"]
    items = attribution["scoreable_item_count"]
    for arm, (_, model) in zip(apps, identities, strict=True):
        metrics = arm.get("metrics", {})
        stages = arm.get("overall", {})
        if (
            arm.get("lane") != ("deterministic" if model is None else "llm-on")
            or arm.get("gold_relation_count") != total
            or metrics.get("gold") != total
            or metrics.get("items") != items
            or metrics.get("nomatch_items") != 30
            or sum(row["count"] for row in stages.values()) != total
            or any(sum(row["by_agreement"].values()) != row["count"] for row in stages.values())
        ):
            raise ValueError("report requires six complete app arms with matching populations")


def build_report(
    attribution: Mapping[str, Any],
    apps: Sequence[Mapping[str, Any]],
    embedding: Mapping[str, Any],
    llm: Mapping[str, Any],
    hashes: Mapping[str, str],
) -> dict[str, Any]:
    if not llm.get("publishable"):
        raise ValueError("LLM ceiling is not publishable; inputs are intact")
    validate_app_arms(apps, attribution)
    if not hashes.get("embedding") or llm.get("embedding_sha256") != hashes["embedding"]:
        raise ValueError("LLM embedding SHA-256 binding mismatch")
    from .recall_embedding_ceiling import residual_item_ids

    residual = residual_item_ids(embedding)
    if set(llm.get("per_item", {})) != set(residual) or llm.get("item_count") != len(residual):
        raise ValueError("LLM residual population differs from bound embedding")
    total = attribution["gold_relation_count"]
    result = dict(
        schema_version=1,
        input_sha256=dict(hashes),
        gold_relation_count=total,
        stages=_stages(attribution["overall"], total),
        strata={
            name: _stages(stages, sum(r["count"] for r in stages.values()))
            for name, stages in attribution["by_stratum"].items()
        },
        rank_distance_below_200=attribution["rank_distance_below_200"],
        apps=[
            dict(
                stack=arm["stack"],
                lane=arm["lane"],
                model=arm.get("model", arm.get("llm_model")),
                metrics=arm["metrics"],
                stages=_stages(display_app_stages(arm["overall"]), arm["gold_relation_count"]),
            )
            for arm in apps
        ],
        embedding=dict(embedding),
        llm=dict(llm),
        decision=choose_lever(attribution, apps, embedding, llm),
        guardrails=GUARDRAILS,
    )
    return result


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Recall loss attribution", "", "## Input SHA-256", ""]
    lines += [f"- {name}: `{digest}`" for name, digest in report["input_sha256"].items()]

    def stage_table(title: str, stages: Mapping[str, Any]) -> None:
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Stage | Count | Share | 2-of-3 count (share) | 3-of-3 count (share) |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for stage, row in stages.items():
            a, b = (row["by_agreement"][str(n)] for n in (2, 3))
            lines.append(
                f"| {stage} | {row['count']} | {row['share']:.2%} | {a['count']} ({a['share']:.2%}) | {b['count']} ({b['share']:.2%}) |"
            )

    stage_table("folio-resolve stages", report["stages"])
    for name, stages in report["strata"].items():
        stage_table(f"Doc type {name}", stages)
    lines += [
        "",
        "Agreement shares use the stage count. Stage shares use all gold in the cohort.",
        "",
        "## Distance past rank 200",
        "",
        "| Distance | Relations |",
        "| ---: | ---: |",
    ]
    lines += [
        f"| {distance} | {n} |"
        for distance, n in sorted(
            report["rank_distance_below_200"].items(), key=lambda pair: int(pair[0])
        )
    ]
    lines += [
        "",
        "## App arms",
        "",
        "| App | Arm | Model | P | R | F1 | No-match FP rate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in report["apps"]:
        m = arm["metrics"]
        lines.append(
            f"| {arm['stack']} | {arm['lane']} | {arm['model'] or 'none'} | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} | {m['nomatch_fp_rate']:.4f} |"
        )
    for arm in report["apps"]:
        stage_table(f"{arm['stack']} {arm['lane']} {arm['model'] or 'none'}", arm["stages"])
    lines += [
        "",
        "## Local search ceilings",
        "",
        "| Ranking | Depth | Recovered | Non-gold | Suggestions |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, curve in report["embedding"]["rankings"].items():
        for depth, row in sorted(curve.items(), key=lambda pair: int(pair[0])):
            lines.append(
                f"| {name} | {depth} | {row['recovered_count']} | {row['non_gold_count']} | {row['suggestion_count']} |"
            )
    lines += [
        "",
        "### Non-gold suggestions per passage",
        "",
        "| Passage | Ranking | Depth | Non-gold |",
        "| --- | --- | ---: | ---: |",
    ]
    for item, row in report["embedding"]["passages"].items():
        for arm in report["embedding"]["rankings"]:
            for depth, metrics in sorted(row[arm].items(), key=lambda pair: int(pair[0])):
                lines.append(f"| {item} | {arm} | {depth} | {metrics['non_gold_count']} |")
    campaign = report.get("campaign", {})
    if campaign:
        lines += [
            "",
            f"Paid-arm projection (USD): {campaign.get('projection_usd', 'unavailable')}; reserved (USD): {campaign.get('reserved_usd', 'unavailable')}.",
        ]
    llm = report["llm"]
    lines += [
        "",
        "## LLM ceiling: upper bound",
        "",
        "Shared grader tendencies can overstate recovery. Majority groups are shown separately.",
        "",
        f"Recovered: {llm.get('recovered', 0)}; Codex-only majority: {llm.get('recovered_codex_only_majority', 0)}; Claude-included majority: {llm.get('recovered_claude_included_majority', 0)}.",
        f"Ambiguous: {llm.get('ambiguous', 0)}; unmatched: {llm.get('unmatched', 0)}; failed passages: {llm.get('failed', 0)}.",
        "",
        "| Passage | Non-gold proposals |",
        "| --- | ---: |",
    ]
    lines += [f"| {item} | {row['non_gold_proposals']} |" for item, row in llm["per_item"].items()]
    d = report["decision"]
    a = d["agreement"]
    lines += [
        "",
        "## Next lever",
        "",
        f"Lever: {d['lever'] or 'none'}. Route: {d['route']}.",
        d["reason"],
        f"App: {d.get('app', 'none')}; stage: {d.get('stage', 'none')}.",
        "App evidence establishes committed recovery; finer stage credit needs stage-level recovery evidence.",
        f"2-of-3 share of misses: {a['miss_share']:.4f}; all gold: {a['base_share']:.4f}; difference: {a['difference']:.4f}; 95% interval: [{a['low']:.4f}, {a['high']:.4f}].",
        f"Bootstrap: {a['resamples']} draws over {a['item_count']} passages, seed {a['seed']}.",
        "",
        report["guardrails"],
        "",
    ]
    return "\n".join(lines)


def preflight(manifest: Manifest, salt: bytes) -> None:
    """Render placeholder prose before loading artifacts or running the bootstrap."""
    placeholder: dict[str, Any] = {
        "input_sha256": {},
        "stages": {},
        "strata": {},
        "rank_distance_below_200": {},
        "apps": [],
        "embedding": {"rankings": {}, "passages": {}},
        "llm": {"per_item": {}},
        "guardrails": GUARDRAILS,
        "decision": {
            "lever": None,
            "route": "Damien",
            "reason": "No local lever qualifies; ask Damien.",
            "agreement": dict(
                miss_share=0,
                base_share=0,
                difference=0,
                low=0,
                high=0,
                resamples=RESAMPLES,
                item_count=0,
                seed=SEED,
            ),
        },
    }
    from .recall_attribution import STAGES

    stage = dict(count=0, share=0, by_agreement={str(n): dict(count=0, share=0) for n in (2, 3)})
    placeholder["stages"] = dict.fromkeys(STAGES, stage)
    placeholder["strata"] = {"0": dict.fromkeys(STAGES, stage)}
    app_stages = display_app_stages(dict.fromkeys(APP_STAGE_DISPLAY_LABELS, stage))
    placeholder["apps"] = [
        dict(
            stack=stack,
            lane="deterministic" if model is None else "llm-on",
            model=model,
            stages=app_stages,
            metrics=dict(precision=0, recall=0, f1=0, nomatch_fp_rate=0),
        )
        for stack in ("folio-enrich", "folio-mapper")
        for model in (None, "gemini-3-flash-preview", "gpt-6-luna")
    ]
    placeholder["embedding"] = {
        "rankings": {
            arm: {
                str(n): dict(recovered_count=0, non_gold_count=0, suggestion_count=0)
                for n in (10, 25, 50, 100)
            }
            for arm in ("whole_passage", "sentence_windows")
        },
        "passages": {},
    }
    placeholder["campaign"] = dict(projection_usd=0, reserved_usd=0)
    placeholder["input_sha256"] = dict.fromkeys(("attribution", "apps", "embedding", "llm"), "0")
    # Scan every alternate fixed decision sentence as well as every rendered heading.
    reasons = (
        "Misses have an excess 2-of-3 share with a 95% interval above zero.",
        "Port or improve the no-LLM path to committed output; its recovery meets the 10% bar.",
        "Largest eligible share of misses; local search requires 25% recovery at depth 50.",
        "Only LLM recovery qualifies; ask Damien.",
        "Never-produced misses dominate; only LLM recovery meets the 25% bar; ask Damien.",
        "ranking gate_tuning local_source app_stage brainstorm Recall attribution",
    )
    check_outputs(placeholder, manifest, salt)
    if scan_text("\n".join(reasons), manifest, salt):
        raise ValueError("leak check failed; inputs are intact; before compute")


def check_outputs(report: Mapping[str, Any], manifest: Manifest, salt: bytes) -> str:
    markdown = render_markdown(report)
    if scan_json_value(report, manifest, salt) or scan_text(markdown, manifest, salt):
        raise ValueError(
            "leak check failed; inputs are intact; re-rendering needs no recomputation"
        )
    return markdown


def write_reports(
    report: Mapping[str, Any], output_dir: Path, manifest: Manifest, salt: bytes
) -> None:
    markdown = check_outputs(report, manifest, salt)
    raw = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    _atomic_write_text(output_dir / "recall-loss-attribution.json", raw)
    _atomic_write_text(output_dir / "recall-loss-attribution.md", markdown)


def record_experiment(
    report: Mapping[str, Any],
    fingerprint: Mapping[str, Any],
    manifest: Manifest,
    salt: bytes,
    *,
    root: Path = ROOT,
    experiments_log: Path = DEFAULT_SYNTHETIC_EXPERIMENTS_LOG,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> ExperimentRecord:
    require_pristine(root)
    check_outputs(report, manifest, salt)
    reason = json.dumps(
        dict(decision=report["decision"], input_sha256=report["input_sha256"]), sort_keys=True
    )
    # Diagnostic only: no fabricated precision/F1 or paired per-item outcomes.
    scores = {
        "synthetic": SliceOutcome(
            "synthetic", (), {"gold_relation_count": report["gold_relation_count"]}
        )
    }
    config = fingerprint["answer_rule_config_sha256"]
    version = "synthetic-v1"
    start_attempt(
        hypothesis="Measure recall loss and recovery ceilings.",
        cluster_targeted="recall",
        cluster_size=report["gold_relation_count"],
        gold_version=0,
        ontology_hash=fingerprint["ontology_cache_sha256"],
        config_hash=config,
        surfaces=(),
        manifest_checker=(manifest, salt),
        prior_scores=scores,
        lever_scope="adapter_only",
        corpus_version=version,
        answer_rule_config_sha256=config,
        experiments_log=experiments_log,
        pending_path=pending_path,
    )
    return finish_attempt(
        decision="park",
        reason=reason,
        surfaces=(),
        manifest_checker=(manifest, salt),
        after_scores=scores,
        corpus_version=version,
        answer_rule_config_sha256=config,
        experiments_log=experiments_log,
        pending_path=pending_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("attribution", "apps", "embedding", "llm"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=name != "attribution")
    parser.add_argument("--leak-manifest", type=Path, required=True)
    parser.add_argument("--salt-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/benchmarks")
    parser.add_argument("--record-experiment", action="store_true")
    args = parser.parse_args(argv)
    manifest, salt = load_manifest(args.leak_manifest), args.salt_file.read_bytes()
    preflight(manifest, salt)
    if args.record_experiment:
        require_pristine(ROOT)
    sha = args.attribution_sha256 or Path(str(args.attribution) + ".sha256").read_text().split()[0]
    attribution = load_bound(args.attribution, sha)
    hashes = dict(
        attribution=sha,
        **{name: getattr(args, name + "_sha256") for name in ("apps", "embedding", "llm")},
    )
    inputs = {
        name: load_bound(getattr(args, name), hashes[name], sha)
        for name in ("apps", "embedding", "llm")
    }
    apps = app_arms(inputs["apps"])
    report = build_report(attribution, apps, inputs["embedding"], inputs["llm"], hashes)
    report["campaign"] = inputs["apps"].get("campaign", {})
    check_outputs(report, manifest, salt)
    if args.record_experiment:
        record_experiment(report, attribution["fingerprint"], manifest, salt)
    write_reports(report, args.output_dir, manifest, salt)
    return 0
