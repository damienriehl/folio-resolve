---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-17T03:55:00Z"
title: "Gold adjudication landed through v7 — continue with calibration and pipeline recall"
summary: "Damien's full adjudication is folded into gold v7; next is answer-rule calibration on tune and recall work against the committed v6/v7 score baselines."
keywords: ["folio-resolve", "gold-v7", "adjudication", "calibration", "recall", "packet-workspace", "fold"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Calibrate the answer rule on tune against gold v7, then start pipeline recall work"
repository: "damienriehl/folio-resolve"
repo_root_sha: "4fb82423cb82"
branch: "main"
head: "51b5360"
---

# Gold adjudication landed through v7 — continuation

## Objective and where it stands

The F1 campaign's human-adjudication phase is **complete and folded**. Every sitting Damien
produced is either live in gold or was re-affirmed as already-live; nothing is stranded. The
campaign now shifts from gold quality to **pipeline quality**: his rulings made gold more
complete, and the measured gap is the pipeline's to close.

Work happened on `feat/f1-campaign-execution`, which a concurrent session merged as PR #8
mid-stream; **everything continues on `main` now**. Concurrent sessions are active in this
checkout — verify pushed *content* on the remote ref before reporting anything pushed (see
Traps).

## Current state (verify, don't trust)

- **Gold**: `eval/data/gold/gold_v7.jsonl`, id `v7-1e8e06748af2`, `damien_corrected: 119`.
  Lineage v5→v6 (atomic-unit contamination pass, 54 removes / 106 elevates / 82 adds) →v7
  (69 pairing rulings via the contract adapter). Split manifest `split_manifest_v6.json`.
- **Score baselines** (committed): `eval/reports/score-v5-*-tune-v5-pre-fold.json`,
  `score-v6-*-tune-v6-post-fold.json`, `score-v7-*-tune-v7-post-pairing.json`.
  v5→v6: P/R/F1 all +1.7pt-ish. v6→v7: P +0.5pt, R −1.4pt, F1 −0.5pt — **not a regression**:
  his rulings added ~115 tune-slice gold entries the pipeline never predicts (fn 968→1076
  while tp rose and fp fell). That fn pool is the recall target.
- **Live workspace**: https://dashboard.damienriehl.com/folio-resolve-gold-evaluation.html —
  packet key `v7-1e8e06748af2|ontology-unknown|310|05e58d1617953c68`, label index embedded,
  his folded rulings arrive pre-filled (285+ rows).
- **Decision provenance**: `eval/reports/gold_decisions.jsonl` (committed);
  `eval/data/gold/folded_v6.json` / `folded_v7.json`; his raw sittings under the machine-local
  gitignored dir `eval/data/reports/audit_packet_v2/collected-answers-2026-08-16/`.

## Next steps, in order

1. **Calibrate the answer rule** — `eval/run_score.py` prints `UNCALIBRATED
   (threshold=…, k=…) — U4 fits it on tune items only` on every run. Fit on tune against v7.
   This gates every downstream number.
2. **Pipeline recall work** against the fn pool v7 exposed. Compare per-stratum blocks in the
   v6/v7 score summaries to find where the added gold concentrates.
3. **Fold future sittings**: non-pairing decisions fold directly
   (`uv run python eval/run_audit.py --mode fold-v2 --gold <latest> --split-manifest <latest>
   --clusters eval/data/reports/clusters_v2.jsonl --lane firm --decisions <file>`).
   Pairing verdicts exported from a checkbox-format sheet must first pass through
   `eval/adapt_v3_diff_verdicts.py` (checkbox export → `edited_iris` contract; 25 of 69 rows
   folding to nothing meant re-affirmation, which is correct). After any fold: re-score with
   `--build-splits` on a NEW split manifest (the invariant refuses a stale one), then
   `eval/publish-gold-evaluation.sh` + re-render with labels (see Publish path).
4. **Cockpit side** (not this repo): decision-receipt sweep failures in the sync log were
   still recurring as of 2026-08-16; possibly another session's to own.

## Working knowledge that is NOT in the code comments

- **The sheets have no server.** Decisions exist only in the reviewer's browser localStorage,
  keyed `folio-eval-draft:<packet-key>` where the key embeds gold id + row count + content
  fingerprint. Republishing over a re-derived packet mints a new key and strands the open
  sitting — the sheet's recovery button (ranked by human-authored content) and
  `eval/consolidate_answers.py` exist for exactly this. Damien reviews from more than one
  machine; the only durable copy is his Download-JSON export.
- **Publish path**: copy the sheet to `~/Coding Projects/cockpit/briefs/board/<name>.html`
  (machine-local path), run `~/.local/bin/sync-cockpit.sh` from the cockpit repo, then verify
  at origin (`ssh hetzner-dev` + `curl -sk --resolve dashboard.damienriehl.com:443:127.0.0.1`)
  and compare md5 server-vs-local. Board copies are gitignored publish artifacts. Hand Damien
  only the stable root URL, never a `/releases/<id>/` link (they get evicted).
- **Renderer-only changes** ship via `eval/rerender_sheet.py <packet.json> <out> [labels.json]`
  — same packet in, byte-identical packet key out, so open drafts survive.
  `eval/build_folio_labels.py` regenerates the 18k-label index after an ontology bump.
- **Add-concept flow** is IRI-only (label auto-fills from the embedded index; typed label
  wins; leaf level pre-selects per the atomic-unit rule).

## Traps this session hit (do not re-derive)

1. **Concurrent-session clobber**: a commit landed here was later overwritten wholesale by a
   session on a stale base; recovery was cherry-pick, not revert. After any push, grep a
   distinctive marker of your change on the remote ref (`git show origin/<branch>:<file>`),
   not just `git log`.
2. **Silent no-op push**: a concurrent session merged the PR and switched this checkout to
   `main`; a subsequent `git push origin feat/…` said "Everything up-to-date" while three
   commits sat unpushed on `main`. Check `git branch --show-current` before trusting a push.
3. **Machine self-assessed confidence is weak here**: an LLM audit pass over gold scored
   100% precision on its "low"-confidence flags and 59% on "high". Rank machine proposals by
   human-authored signal, never raw counts (a 104-entry sitting held zero human decisions —
   `collect()` emits machine state for every unfolded row).

## Read before acting

- `eval/folio_eval/packet_render.py` — the whole workspace (JS in `_SCRIPT_V2*` constants);
  tests assert over generated strings, so behavioral claims need a browser check.
- `eval/folio_eval/audit.py:3086` — `fold_granular_decisions`, esp. the pairing branch
  (`edited_iris` replaces a row's own per-item contribution; silence never deletes gold).
- `docs/solutions/` in the cockpit repo — the release-URL and push-verification learnings.
