---
title: "Leak gate: owner-side scans don't need scrypt, and manifest regeneration has TWO settled filter classes"
lane: eval
tags: [leak-gate, surface-manifest, scrypt, fold-recipe, performance]
status: active
related:
  - docs/plans/2026-08-16-001-feat-synthetic-benchmark-f1-campaign-plan.md
---

# Leak gate: owner-side scans don't need scrypt, and manifest regeneration has TWO settled filter classes

Two learnings from the corpus-v1 release day (2026-08-17), which burned ~5 CPU-hours
and one wrong manifest before landing clean.

## 1. The scrypt cost model conflates two audiences

The U4 firm-surface manifest hashes surfaces with scrypt (n=16384) so that **workers
without gold access** can scan artifacts blind. That per-n-gram cost is enormous on
real corpora: scanning corpus v1 (270 rows, ~597k n-grams at ~24 ms each) is **~4
CPU-hours single-core**. A full-corpus scan was killed at 19% after 45 minutes before
anyone did that arithmetic.

But the **owner-side session has the gold locally**. It can plaintext-match the same
surfaces (the `assert_no_surfaces` code path that already exists in
`eval/folio_eval/leakcheck.py`) and reach the identical verdict in seconds. Damien
ruled 2026-08-17 (ask `folio-resolve-2026-08-17-2054-leak-gate-posture`): **owner
plaintext, workers hashed** — no R7 posture change, the committed manifest stays
hashed for blind checks.

**Same evening, implemented better than specified**: a concurrent agent (branch
`feat/synthetic-baseline-u8`) replaced scrypt with **HMAC-SHA256 keyed by the private
salt** (manifest v3, `digest_algorithm` field, fail-closed on mismatch). With the salt
secret, outsiders cannot compute digests at all, so CPU-hard hashing bought nothing —
HMAC preserves the worker-side blind-check contract at ~1 second per corpus scan.
This satisfies the ruling for both audiences and supersedes the interim 14-way
parallel-scrypt workaround (kept below for history only).

Two operational traps from the same afternoon:

- `xargs -I{} bash -c '{}'` **strips quotes** from the job lines; with `Coding
  Projects` in every path this made all 14 chunk jobs fail instantly with exit 126.
  Use a plain bash runner script with `&`/`wait` instead.
- The checker's `check` subcommand **exits 0 even when it finds collisions** — the
  verdict is in stdout (`collisions=N`), not the exit code. Never gate on exit code
  alone.

## 2. Regenerating the surface manifest after a fold takes BOTH settled filter classes

KTD4 binds the manifest to the gold version, so every fold requires regeneration. The
settled protected set (3,047 digests) is reached by applying **two** filters to the
raw `leakcheck generate` output, and only one of them lives in the allowlist ledger:

1. the **126-entry allowlist ledger** (generic words, doc-type phrases, gold-label
   sub-grams — Damien-ruled), applied by digesting each entry and removing it; and
2. **all public-FOLIO-label digests** (Damien ruled the class generic/public),
   applied by digesting every label in the 18k-label FOLIO dictionary and removing
   matches — this step is NOT in the ledger file and is easy to miss.

A session that applied only the ledger produced a 3,472-digest manifest and 379
phantom collisions (all against public FOLIO labels that v7's added gold brought in).
After applying both classes the v7-bound manifest landed on exactly the settled 3,047
— v7's ~425 added surfaces were all public labels. If a regenerated manifest's
post-filter count differs materially from 3,047, suspect a missed filter class before
suspecting real leakage. Under the owner-plaintext ruling above, the same two classes
apply as plaintext exclusion lists.

**These 379 were adjudicated exclusions, not concealment.** A concurrent agent hit
the same 379 overlaps independently and read them as something "the old manually
filtered manifest concealed" — the truth is the reverse: the old manifest excluded
them *because Damien ruled the classes public/generic*. The v3 `generate` CLI now
takes both classes as first-class inputs: `--allowlist <ledger>` for the 126 settled
entries and `--allow-public-file <artifact>` for public-artifact overlap.
**Pass the public FOLIO label dictionary as the public artifact, never the corpus
under test**: excluding surfaces because they occur in the artifact you are about to
release is circular and would launder a genuine leak once it slipped into a draft.
The dictionary is corpus-independent, reproducible, and reduces the manifest to the
settled firm-specific set.

Corollary: corpus build 5 shipped a **stale v5-bound manifest** because the build
driver only re-filtered the existing file instead of regenerating from current gold.
The `check` subcommand fails closed on that (`manifest stale: local gold identity
does not match surface manifest`) — that refusal is the KTD4 binding working, not an
obstacle to route around.
