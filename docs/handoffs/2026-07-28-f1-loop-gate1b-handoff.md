# F1 Improvement Loop — session handoff at Gate 1b (2026-07-28)

Cold-start orientation for the next session continuing the F1 loop. The plan is the authority: `docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md` (implementation-ready; KTD3/KTD6 were revised 2026-07-28 to Damien's per-cell model). This doc carries execution state the plan deliberately does not.

## Where the loop stands

- **Branch:** `feat/f1-eval-loop`, pushed to origin. All gates green at every commit (`uv run pytest` 384 passed, `uv run mypy src`, `uv run mypy eval`, `uv run ruff check`).
- **Done:** U1 intake · U2 gold builder · U3 scoring harness · U4 baseline+clusters · U5 audit-gate machinery (iterated through five sheet versions) · U6 iteration runner · U9 downstream baseline snapshot (both consumers fully green pre-iteration).
- **OPEN — Gate 1b:** he has folded 7 rulings (gold v3); the rest of the sheet is pending. Sheet regenerated 2026-08-05 (byte-identical to the 07-28 packet apart from `generated_at`) and republished to the same private artifact.
- **U7 iteration 1 measured 2026-08-05** (he answered gate2 q2 "start now"; scored against gold v3, membership-identical to the v2 he approved). `attempt-0001` is in `eval/reports/experiments.jsonl`. See "Iteration queue" below — the result changes the plan.
- **Pending:** U8 iterations 2–3, U10 check-in. U11/U12 (synthetic corpus, CI floor) gated on his satisfaction signal.

## Baselines (identical pipeline; only the answer key changed)

| gold | tune F1 | firm2 F1 | note |
|---|---|---|---|
| v1 cascade-union | .0846 | .1260 | superseded — 80% of denominator was inherited gold the input can't reach |
| v2 per-cell | .2093 | .1260 | Damien's model: every cell any level = own item, no inheritance, dedup by text |
| v3 (+his 7 rulings) | .2089 | .1260 | key got *harder* (+5 gold IRIs pipeline can't find) — correct direction |

Split (seed 20260727, v3 membership-identical to v2): frozen 79 / tune 634 / firm2 111. Frozen never scored yet (discipline gate: `--frozen-final` only at final report).

## Damien's canon (learned this session — bake into everything)

1. **Per-cell atomic mapping, any level.** A cell maps to its own tag(s); a child never implies its parent.
2. **Molecules → atoms.** His corrections consistently add implicit atoms (industry / asset / player / practice: Ship, Bank, Attorney, Receiver, Borrower, Public Company). `eval/folio_eval/improve.py` pilots this pattern (section F).
3. **Pipe `|` = multiple tags.** Derivation was correct; keep it visible per-tag in every UI.
4. **Nothing is ever locked.** Applied decisions render pre-filled but editable; amendments append; Copy-decisions emits diffs-from-applied only.
5. **Three-panel display everywhere:** Gold (current version, live file — never a snapshot) / Current pipeline / Proposed, plus original spreadsheet mini-grids and notes on every decision.
6. His stated goal: highest true classification accuracy — improving gold is as valid as improving the matcher; every gold change is his ruling with provenance and re-baselining.

## Pending Damien decisions (Gate 1b sheet, artifact URL below)

- 126 remaining pairing rows (106 pre-checked heuristic per his principle; **26 badged "needs your eye"** — one output block, two inputs).
- Section B consistency groups (73 remaining), C suspects (50), D resolution labels (29), E new-gold (25), F atom-proposal pilot (60 proposals / 26 cells).
- **Commercial composition call:** his edit left `{Commercial Transactions Law}` on row 92's contribution, but the deduped cell's second instance (row 973, still open) contributes Cross-Border Objective + Commercial and Trade Law → item currently has 3 IRIs.
- Gate2 ask q2: may iteration 1 start against current gold while he works the sheet?

## Iteration queue (evidence-backed, in order)

1. ~~**Provider limit forward-fix**~~ — **DONE, and it scored zero.** Commit `a2db64a`; `attempt-0001`, decision `keep`. Tune F1 .208931 → .208931, Firm-2 F1 .125984 → .125984, tp/fp/fn identical on both slices, AE4 clean (0 regressed, 0 improved, CI [0.0, 0.0]). The reason is that every harness entry point defaults `label_search_limit=10`, which is *also* folio-python's own default — the fix removes a cap nothing was pushing against. Only movement: tune `exact_items` 11 → 12, a second-order effect at the `limit=3` call sites (`pipeline.py:103`, `domain_prior.py:162`), where upstream now limits matched labels before class expansion instead of after. Keep it: it is correct, tested, and the precondition for the limit lever. **Δcode credit for iteration 1 is 0.000000.**
2. **The limit lever itself was then probed and does not pay** (gitignored `score-v3-*-{tune,firm2}-probe-limit200.json`). At `--label-search-limit 200`: tune F1 .208931 → .206210, Firm-2 .125984 → .125654. Ranked-list recall does rise — tune recall@10 .3143 → .3199 (hits@10 391 → 398 of 1244), Firm-2 recall@10 .225 → .250 — but nothing reaches the top-2 the answer rule commits, and one Firm-2 item flips correct→incorrect (AE4 would fire). Do not ship a raised limit as an iteration.
3. **One named recalibration** (calibration saturates at P=.373 → rule degenerated to top-2, threshold inert). Note the limit probe leaves it inert too.
4. **Recall-engine port — the residual now justifies it** (KD9's "cheap fix first" clause is discharged; aligns with `docs/migration/SCHEDULE.md` Stage 2). The 982 tune misses decompose as 509 `candidate_gap_truncated` + 337 `candidate_gap_unreachable` + 136 `ranked_below_cutoff`, and item 2 shows lexical depth converts ~7 of the 509 into top-10 and none into answers: those concepts sit around rank 35 in a 200-long lexical list, so the bottleneck is **ranking, not retrieval depth**. Add the 337 unreachable and the 321 `zero_token_overlap` signals — misses sharing no token with any label, which no lexical limit can ever reach — and semantic recall is the only lever left in the queue.

## Mechanics a fresh session needs

- **Fold Damien's sheet paste:** `uv run python eval/run_audit.py --mode fold-v2 --decisions <file>` → gold v4 (amendments append; rejection memory suppresses re-proposals; committed `eval/reports/gold_decisions.jsonl` gets IDs only — no firm strings, enforced by leak scanner).
- **Regenerate the packet/sheet:** `eval/run_audit.py --mode packet-v2` — **must pass `--clusters eval/data/reports/clusters_v2.jsonl`** (defaults to v1 and silently drops 175 score-driven suspects) — `--current-gold` auto-detects the newest `gold_vN.jsonl`; prediction cache makes it ~40s.
- **Re-baseline:** `eval/run_baseline.py`. **Iteration attempts:** `eval/run_experiment.py start|finish|status` (KTD8 window rules: refuses on gold/ontology drift without `--rebaseline`; reverted attempts count toward the 3-per-check-in).
- **Downstream diff at check-ins:** `eval/run_downstream.py diff` (blocking = previously-correct→incorrect or consumer test newly failing; invoke consumers via their own `.venv/bin/python`, never bare `uv run` there).
- **Artifact publishing:** the sheet is a PRIVATE claude.ai artifact (contains firm data; never link-share; delete after the gate folds). A NEW session republishes by passing `url:` `https://claude.ai/code/artifact/b7447b5a-6e0e-49d3-9ef9-3502f25af14a` to the Artifact tool with file `eval/data/reports/audit_packet_v2/sheet.html`. Plan artifact: `.../artifact/b1ac22db-afeb-469f-912b-eaec92ad89b3`.
- **Cockpit:** ask `folio-resolve-2026-07-28-gold-audit-gate2` is OPEN (q1 sheet decisions, q2 early-start). Gate-1 ask closed. On receiving answers in chat, write the answers file per cockpit rules.

## Data locations & privacy (KTD1/KTD12 — non-negotiable)

- Originals: `~/.folio-resolve-eval-data/` (+ session uploads dir). Deleted only at loop end per Definition of Done, recorded in the final check-in.
- Gitignored: everything under `eval/data/` except `MANIFEST.md` (hashed sheet names only). Gold v1–v3 + manifests + split manifests + configs + packets + Damien's decision notes live there.
- Committed: `eval/reports/` aggregates only (baseline-v1/2/3, downstream-baseline-v1, gold_decisions.jsonl). **No committed file may carry a firm surface string** — `clusters.assert_no_surfaces` is the choke point; run it on anything new.
- Approved processing surfaces: this machine + Anthropic API only; in-scope taxonomy sheets only, never the matter/client sheets. CI (future U12) gets synthetic fixtures only. Note global delegation rule change 2026-07-28 (root CLAUDE.md): past 80% weekly usage, execution delegates to Codex/GPT workers — **firm-data work is exempt unless Damien explicitly approves that egress** (KTD12 names Anthropic API as the only model surface for firm content).

## Known warts

- `eval/data/gold/pairing_note_v2.txt` paraphrases (not verbatim) Damien's Islamic Finance ruling.
- Consistency-vs-pairing supersedence is compositional: his later pairing edits override earlier keep-rulings on the same item (matches his atomic principle; he has been told).
- 4 folio-mapper probe-candidate files have no driver/items and are excluded from the downstream set.
- folio-python: repo venv pins 0.3.6 via uv.lock (user-site has 0.3.3, sibling checkout 0.3.7). Ontology cache hash pinned in every artifact; drift aborts runs.
- folio-enrich's `compare.py` writes tracked files — never invoke it; `downstream.py diff` replaces it.
- Simplification pass deliberately deferred to just before the U10 check-in (one consolidated pass after iterations churn src/).
