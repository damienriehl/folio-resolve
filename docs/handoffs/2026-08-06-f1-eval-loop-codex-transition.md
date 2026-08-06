---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-06T16:20:00Z"
title: "F1 eval loop — handoff into the Codex harness"
summary: "Iteration 1's recall fix measured as a no-op; the residual is a ranking problem, not retrieval depth — plus the firm-data egress fence a Codex session must not cross."
keywords: ["folio-resolve", "f1-eval-loop", "codex-transition", "egress-perimeter", "gold-audit"]
repository: "folio-resolve"
branch: "main (work lives on feat/f1-eval-loop)"
resume_focus: "Decide the recall-engine port vs. further ranking work, and close gate2 q1/q3."
---

# F1 eval loop — handoff into the Codex harness

Written for a session that has never seen this repo and cannot ask questions.

---

## STOP — read this before running anything

**This repository holds confidential law-firm data, and you are probably running on Codex.**

- `eval/data/` contains derivatives of two law firms' internal taxonomy workbooks.
  **KTD12 of the governing plan names the Anthropic API as the only approved model
  surface for that content.** Codex means OpenAI egress. Do not read, open, print,
  summarize, `grep`, or otherwise pull `eval/data/**` into your context.
- The safe surface for a Codex worker here is `src/`, `tests/`, and `docs/`. That is
  the fence the 2026-07-28 session drew unilaterally when it delegated the limit fix,
  and it is **still awaiting Damien's ratification** — it is question `q3` on the open
  ask `folio-resolve-2026-07-28-gold-audit-gate2`.
- **Unverified:** I do not know whether Damien has classified folio-resolve as a
  privileged/client-content repo under the newer cockpit-wide egress perimeter. If he
  has, the correct reading is Claude-only and Codex needs per-task sign-off. Treat that
  as open, and prefer the narrow fence until he says otherwise. Do not widen it because
  a task would be faster.
- Aggregate outputs under `eval/reports/` are committed and safe **because** no committed
  file may carry a firm surface string; `clusters.assert_no_surfaces` is the choke point.
  Run it over anything new you would commit.

---

## Where the work actually lives

The F1 loop is **not on `main`**. It is on `feat/f1-eval-loop`, checked out in a separate
git worktree. `main` is a release line (v0.3.1) that has moved independently — the eval
harness does not exist in its history.

Authority for the work, in order:

1. `docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md` — the plan, and the real
   authority. Implementation-ready; KTD3/KTD6 were revised to Damien's per-cell gold model.
2. `docs/handoffs/2026-07-28-f1-loop-gate1b-handoff.md` (on the f1 branch) — the deeper
   operational handoff: exact runner commands, data locations, privacy rules, warts.
   **Read that one for mechanics.** This document does not repeat it.
3. This document — what happened since, and what the commits cannot tell you.

---

## The headline: iteration 1 was a measured no-op

This is the single most important thing to carry forward, and it overturns what the
2026-07-28 session confidently believed.

`FolioPythonProvider.search_by_label` accepted a `limit` and never forwarded it upstream,
so folio-python's own default of 10 truncated every result set. That was a real bug, and
the fix (one line, keyword-passed) is correct and committed.

**It changed nothing.** Scored as `attempt-0001` against gold v3: tune F1 `.208931 →
.208931`, Firm-2 `.125984 → .125984`, identical tp/fp/fn on both slices. Delta-code credit
`0.000000`. The decision was *keep* — it is correct, tested, and a precondition for any
future limit lever — but it bought zero F1.

### Why it looked right, and why that reasoning was wrong

The diagnosis rested on direct measurement: 9 results returned where 131 were available
for the same query, and a median gold-concept rank of 35.5 when asking for 200. Every one
of those numbers is true. They are also **the wrong measurement**.

They describe what the *provider could return*. Nobody checked what the *callers actually
asked for*. Every harness entry point defaults `label_search_limit=10` — which is also
folio-python's default — so the fix lifted a cap that nothing was pushing against.

**The gotcha I would have wanted on day one:** when you find a truncation bug, the
evidence that proves the truncation is real does not prove it is *binding*. Trace the
call sites' actual argument values before you believe a ceiling is costing you anything.
The July session had the call sites open (`pipeline.py`, `domain_prior.py`), read the
`limit=3` ones, and never traced `label_search_limit`'s default. One `rg` would have
killed the whole iteration before it was spent.

### The probe that reframes the residual — read this before choosing next work

A follow-on probe (run as a probe, not a scored attempt) raised `label_search_limit` to
200:

- tune F1 **falls** `.208931 → .206210`
- ranked recall@10 rises `.3143 → .3199`
- one Firm-2 item flips correct → incorrect

Lexical depth moves roughly **7 of the 509 truncated misses** into the top 10, and **none**
into the committed top-2.

The conclusion, which should drive the next iteration: **the residual is a ranking problem,
not a retrieval-depth problem.** More candidates make things slightly worse. This discharges
KD9's "cheap fix first" clause — the cheap fix has now been tried and measured — in favour
of the recall-engine port that the plan describes for U7.

Do not spend another iteration widening retrieval.

---

## Decisions made in conversation that never reached a commit message

- **Re-baseline, don't spend an attempt.** The post-fix baseline was intended to run with
  `--rebaseline` under KTD8 rather than counting as a second attempt of the three in the
  check-in window. *Unverified* whether the 2026-08-05 session honoured this — check
  the attempt count before assuming you have three.
- **The frozen 79 stay untouched.** No scoring against the frozen split until the final
  report. This is a discipline gate, not a preference; breaking it invalidates the exam.
- **Simplification pass deliberately deferred** to just before the U10 check-in, so it
  lands as one consolidated pass after the iterations churn `src/`. If `src/` looks like
  it wants tidying, that is expected — leave it.
- **The Codex fence** described at the top was drawn by an agent, not by Damien. It is
  `q3` and still open.

## Approaches considered and rejected

- **Running the scored iteration without Damien's go.** Declined on 2026-07-28 even
  though the code was ready, because scoring spends one of three attempts in the check-in
  window and KTD8 refuses on gold drift while he is still ruling on the sheet. He answered
  "start now" on 2026-08-05 and it was scored then. The instinct to wait was right; the
  cost of being wrong was asymmetric.
- **Committing `uv.lock` to `main`.** `main` has an untracked `uv.lock` that **differs**
  from the one tracked on `feat/f1-eval-loop`. Committing it would guarantee a merge
  conflict on a generated file for no benefit, and libraries conventionally do not pin
  lockfiles. Left untracked deliberately — it is fully regenerable with `uv lock`, so no
  work is at risk. Do not "tidy" this without deciding which convention wins.

---

## Current state, precisely

**Done and scored:** U1 intake · U2 gold builder · U3 scoring harness · U4 baseline and
clusters · U5 audit-gate machinery · U6 iteration runner · U9 downstream baseline · U7
iteration 1 (scored, no-op).

**Gold is at v3.** Baselines: tune `.2089`, Firm-2 `.1260`. Split seed 20260727 —
frozen 79 / tune 634 / firm2 111.

**Half-finished, and exactly where it stops:** the Gate 1b audit sheet. As of 2026-07-28
the outstanding rows were 126 pairing adjudications (106 heuristic-pre-checked, 26 badged
for Damien's eye), 73 consistency groups, 50 suspects, 29 resolution labels, 25 new-gold
candidates, and a 60-proposal atom pilot over 26 cells. **Unverified:** I do not know how
much of that he has since worked. Check `briefs/qa-state.json` in the cockpit repo, keyed
by (stem, qid), before assuming anything — and before re-asking him anything.

**Open decisions** on ask `folio-resolve-2026-07-28-gold-audit-gate2`:
- `q1-sheet-v2-decisions` — the sheet itself. Open.
- `q3-codex-delegation-scope` — ratify the Codex fence. Open. **Answer this before
  dispatching Codex workers at any width beyond `src/` + `tests/`.**
- `q2-start-iteration1-early` — answered and now recorded; do not re-ask.

---

## Verification performed this session

- All four gates green at the limit-fix commit: `uv run pytest` (387 passed, 1 skipped),
  `uv run mypy src`, `uv run mypy eval`, `uv run ruff check`.
- Red-then-green confirmed by hand, not taken on trust: reverting the one-line fix makes
  the new tests fail with `assert 10 == 20` — the exact truncation signature.
- The decision console artifact was rendered at mobile and desktop widths in both themes,
  and its answer payload verified to parse as valid JSON with the correct qids.

## Fragile local state — do not trip over these

- **A 37 MB untracked copy of `eval/` sits in the `main` checkout.** It is a stale
  duplicate; the live tree is in the f1 worktree. `main`'s `.gitignore` had **no** rule
  covering it until this session added one, so a stray `git add .` on `main` would have
  committed law-firm data. The rule now mirrors the f1 branch exactly. The stale copy was
  **not deleted** — deleting data is Damien's call, not an agent's. Do not read it.
- Original workbooks live in a machine-local directory outside the repo (path recorded in
  the private copy of this handoff, not here). They are deleted only at loop end, per the
  plan's Definition of Done.
- `folio-enrich`'s `compare.py` writes tracked files — **never invoke it**; the downstream
  diff runner replaces it. Invoke consumer repos through their own virtualenvs.
- Regenerating the audit packet **must** pass the v2 clusters file explicitly; the default
  is v1 and silently drops 175 score-driven suspects. Exact flags are in the 2026-07-28
  handoff.

## Plausible next steps

1. **Build the recall-engine port** (plan U7's expected payload) — the probe evidence now
   justifies it, and the cheap alternative has been measured and rejected.
2. **Treat ranking, not retrieval, as the lever.** Calibration currently saturates, which
   degenerated the committed rule to top-2 and left the threshold inert. That is where the
   509 misses actually live.
3. **Close `q3`** so worker routing on this repo stops being re-decided per task.
