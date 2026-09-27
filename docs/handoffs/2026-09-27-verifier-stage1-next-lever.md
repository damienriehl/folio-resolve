---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-27T22:36:28Z"
title: "Verifier stage 1 shipped (no-go on abstention): choose the next lever"
summary: "PR #58 merged the calibrated shortlist verifier stage 1; F1 1.75% to 5.64% but abstention failed, so three owner decisions gate what comes next."
keywords: ["verifier", "laya", "jev", "abstention", "retrieval-recall", "stage-1", "pr-58", "no-go"]
cwd: "folio-resolve repository root"
resume_focus: "Get Damien's answers to the three open decisions, then start the chosen lever via ce-brainstorm or ce-plan."
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "main"
head: "2de1a0cdb726c013c530d0ef58365b8846bad611"
---

# Verifier stage 1 shipped — choose the next lever

## Where things stand

Stage 1 (U1–U4) of `docs/plans/2026-09-23-2130-feat-calibrated-shortlist-verifier-plan.md` is complete and merged: PR #58, merge commit `2de1a0c`. Stage 2 (U5–U9: Laya training pool, Kaggle fine-tune, CPU cross-encoder, local inference, Jev arm) has **not started**, and by the plan's own stop condition it does not start after a no-go.

- **Verdict: no-go, failing only "abstention works."** `docs/benchmarks/verifier-ceiling.md` holds the table and the full-precision JSON; the experiment is `attempt-0005` in `eval/reports/synthetic_experiments.jsonl` (decision `park`, `lever_scope: adapter_only`).
- **What worked.** A Codex verifier over each passage's top-100 shortlist raised strict F1 from 1.75% to 5.64% (paired delta +3.9 pts, 95% CI +2.8 to +5.0), recall 4.1% to 18.5%, precision 1.1% to 3.3%.
- **What failed.** No-match controls tagged 27/30 (baseline 30/30). Cut fitting counts controls as false positives, yet every fold chose "never abstain": Codex gives some unrelated concepts p ≥ 0.95 on no-match text, and its passage-level `no_match_p` barely beats a constant (Brier 0.112 vs 0.118).
- **The bigger limit.** `docs/benchmarks/verifier-shortlist-depth.md`: 280 of 363 gold relations never reach the top 200 retrieval survivors; the top-100 shortlist caps recall at 19.3%. No verifier can recover what retrieval never surfaces.
- **Shared-bias caveat.** Corpus gold was graded by two Codex graders and one Claude grader per passage, so the Codex ceiling may be optimistic.

## Open decisions (Damien's; none answered yet)

Filed as Cockpit record `folio-resolve-2026-09-26-0333-verifier-stage1-next` (qids `next-lever`, `jev-after-nogo`). Check `cockpit-answer inspect <stem> --qid <qid>` before re-asking; the sheet may have been answered since.

1. **`next-lever` — what next?** Options: retrieval recall (the agent's recommendation, not yet Damien's), fix abstention then re-test, treat as partial go and train Laya, or stop.
2. **`jev-after-nogo` — test Jev once Damien has TypeSafe access?** Options: yes (agent's recommendation), only if abstention is fixed, no. Damien said earlier in the session that he will obtain Jev access.
3. **Ideation offer** from the completion hook: record `folio-resolve-2026-09-26-0400-ideation-e63aa590523985f1a1957d3a`, choices "Explore ideas" / "Not now". Persist the answer with `cockpit-answer answer <record_id> --qid explore --choice <choice>`; only an explicit "Explore ideas" authorizes `cockpit-ideate accepted --record <record_id>`.

Decisions already settled by Damien in the brainstorm (do not re-ask): the first seam was the shortlist verifier plus abstention; Laya first with Jev as a later arm; Laya fine-tuning on Kaggle's free 2xT4 with a CPU cross-encoder fallback (the home box and the Hetzner dev box have no GPU).

## Plan-review items still unapplied

From the plan's document review (not decided by Damien): reword R4 so any gain is attributed to the verifier and the deeper shortlist together; optionally run a Claude verifier cross-check before any Laya spend; state whether Jev runs after a no-go (now decision 2 above). One PR finding stays open by design: the expanded relevance metric is unavailable because the owner judgments cover different queries (`eval/folio_eval/verifier_report.py`).

## What exists to build on

- `eval/folio_eval/verifier.py` — decision-collection schema, control-aware 5-fold cut fitting, scoring, paired comparison; now rejects arm/baseline pairs with different retrieval inputs.
- `eval/folio_eval/verifier_depth.py` + `eval/run_verifier_depth.py` — checkpointed, sharded depth curve and per-item baseline collection.
- `eval/folio_eval/verifier_collect.py` + `eval/run_verifier_collect.py` — isolated `codex exec` collector (`--jobs`, resume, rejection-reason histogram). Reusable for a Jev or Claude arm behind its runner seam.
- `eval/folio_eval/verifier_report.py` + `eval/run_verifier_report.py` — go/no-go verdict and experiment recording.
- Committed collections under `eval/synthetic/verifier/` make every number recomputable offline.

## Traps the next session should not repeat

- **Never run a long adaptation without checkpoints.** The first live depth run spent ~36 CPU-hours, then its Markdown leak check failed and it wrote nothing. Use the sharded checkpoint flags; a full corpus adaptation takes roughly 9 wall-hours on 8 shards.
- **Generic words can collide with the firm-surface manifest.** one such word appeared in the depth summary (the plural of the ordinary word for a cutoff value); renderers now leak-preflight their fixed prose before any compute.
- **The pristine-tree gate rejects untracked files.** Run scoring/depth/report with `--record-experiment` from a clean worktree under `~/worktrees/`, as `docs/solutions/2026-09-04-u9-iteration-traps.md` §1 describes.
- **codex-cli does not report the served model.** Provenance records the requested model and says so; do not claim which model actually served.
- **Codex workers have no network**, so live collections run on the orchestrator side after fake-runner tests pass.
- The repo's review gate: a recorded Codex review receipt (prompt and verdict retained off-tree, never committed, since review prose can collide with the firm-surface manifest).

## Plausible next steps

These are alternatives, gated on decision 1:

- **Retrieval recall** — a new brainstorm. The depth curve is the baseline evidence; the deferred branch/domain router from the brainstorm is one candidate lever. Stage 1's verifier can be re-run on any improved shortlist through the same collector and report.
- **Fix abstention** — e.g. ask the no-match question in its own call instead of beside 100 candidates; re-collect and re-report with the existing tooling.
- **Partial go to Laya** — resumes plan units U5–U8; U6 needs Damien's Kaggle account.
- **Stop** — retire the plan's remaining units.
