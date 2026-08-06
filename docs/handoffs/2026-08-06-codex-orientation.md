---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-06T16:53:12Z"
title: "folio-resolve 0.3.1 hardening — verified Codex orientation"
summary: "Verified release, consumer, F1-loop, confidentiality, and local-state facts after the Aug 5 hardening sweep."
keywords: ["folio-resolve", "0.3.1", "consumer-pins", "f1-eval-loop", "codex", "confidentiality"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Keep main stable; continue the F1 loop only from its dedicated worktree and current decision state."
repository: "folio-resolve"
repo_root_sha: "4fb82423cb820c0cee6aa2721b872790c09ad0c5"
branch: "main"
head: "288cf56ee1dc55162a5267608d54bd9bc828d5ab"
worktree_path: "/home/damienriehl/Coding Projects/folio-resolve"
---

# folio-resolve 0.3.1 hardening — verified orientation

## Current release state

- `main` is exactly at `origin/main`, HEAD `288cf56`; tag `v0.3.1` is commit `3cf131e`.
- PyPI's public JSON API reported latest version `0.3.1`, with wheel and sdist uploaded
  2026-08-05. The transition brief's “publish may still be pending” warning is superseded.
- The Aug 5 series fixed twelve correctness/determinism defects. The best overview is
  `docs/migration/2026-08-05-v0.3.1-consumer-impact.md`; its measured addendum supersedes
  three earlier source-level predictions. Do not repeat the older performance claims alone.
- Consumer pins are present at the pushed heads of `folio-enrich`, `folio-insights`, and
  `folio-mapper`. `folio-enrich` also contains the active-interval overlap sweep.

## F1 loop: separate branch, conditional Codex perimeter

- The live harness is not on `main`. It is at pushed branch/worktree
  `feat/f1-eval-loop` / `/home/damienriehl/Coding Projects/.worktrees/folio-resolve-f1`,
  HEAD `ffb170e`.
- Read `docs/handoffs/2026-08-06-f1-eval-loop-codex-transition.md` first. For mechanics,
  use `docs/handoffs/2026-07-28-f1-loop-gate1b-handoff.md` on the F1 branch. The plan is
  `docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md` on that branch.
- Codex may inspect or edit harness code only when no firm rows enter its context.
  `eval/data/**`, gold rows, and packets remain off-limits under KTD12. This is conditional,
  not blanket authorization. Aggregate committed reports are safe only after the harness's
  surface-leak assertion passes.
- Iteration 1 is already scored. The provider-limit fix is correct but measured as an F1
  no-op; raising lexical depth to 200 made F1 slightly worse. The residual evidence points
  to ranking/semantic recall, not another retrieval-depth increase.

## Decision and queue drift

- Cockpit ask `folio-resolve-2026-07-28-gold-audit-gate2` has only q1 (the adjudication
  sheet) open. In `briefs/qa-state.json`, q2 is answered and executed; q3 is answered with
  the conditional Codex fence. Do not re-ask either.
- `briefs/on-deck.json` has two stale folio-resolve entries that still say q2/q3 are pending
  and the scored run is parked. Treat `qa-state.json` plus the F1 transition handoff as the
  current evidence. Updating the cockpit queue is separate work in the parent repository.

## Fragile local state

- Visible worktree state at capture: only untracked `uv.lock` on `main`. The F1 transition
  handoff records that this is deliberately uncommitted because the F1 branch tracks a
  different generated lockfile and committing it here creates a needless conflict.
- A machine-local, ignored, approximately 37 MB stale `eval/` copy is present in the main
  checkout. It is not “a whole untracked harness”: `git ls-files eval` returns zero on main.
  It is data-bearing state protected by `.gitignore` commit `cbd2537`; do not inspect or
  delete it from Codex. The live tracked harness exists only in the F1 worktree.
- Do not invoke `folio-enrich`'s `compare.py`; it writes tracked captures. Use the F1
  downstream runner from the dedicated worktree when authorized.

## Verification on this box

- 2026-08-06: `uv run pytest` collected 556 tests and finished `556 passed in 2.65s`
  under Python 3.13.11. This was a direct host run after the sandbox's read-only uv-cache
  mount prevented the first attempt.
- No eval-data tests or F1 harness commands were run from `main` during this orientation.

## Constraints

- An answered ask records a decision, not proof of execution. Revalidate external and
  irreversible state before acting.
- A bare `/tmp/.git` can be a Codex sandbox mountpoint, not a real repository. Use Git's
  semantics rather than `.exists()` when detecting enclosing repositories.
- Never inspect `*.pem`, `*.key`, `twin-secrets/`, or `~/.secrets`.
- Never enter or touch `fence-litigation/` or `bayless-aerials/`.
