---
title: Review gate catches self-inflicted regressions
lane: evaluation
tags: [review, validation, leak-gate, regression-testing, reproducibility]
status: active
related:
  - docs/solutions/2026-09-07-u10-comparison-rerun-traps.md
  - docs/solutions/2026-08-17-leak-gate-owner-scans-and-manifest-regeneration.md
---

# Review gate catches self-inflicted regressions (2026-09-07)

Five findings from four review rounds on one campaign-report unit.

## 1. A fix can be the next round's defect

Two findings came from the preceding round's repair. A verdict-versus-bounds check treated values
rounded to six decimal places as exact and rejected legitimate producer output. The symmetric
tolerance added in response then accepted sign-contradictory artifacts that the producer cannot
emit. Each repair had covered the example that prompted it without probing the opposite error
direction. For every validator change, test both false acceptance and false rejection, and name the
safe failure direction. Here, an ambiguous bound must route to owner-decision; it must never choose
the verdict that closes a campaign.

## 2. Rounding cannot reverse a sign

For a value serialized on a six-decimal grid, the correct sign allowance is exactly zero, not a
symmetric epsilon. A magnitude below half of one grid unit becomes `0.0` or `-0.0`; serialization
does not move it across zero. The tolerance band failed because it modeled general numeric noise
rather than the producer's actual quantization. Derive validator allowances from that transform:
permit the zero collapse, but reject any nonzero value whose sign contradicts the verdict.

## 3. A guard built from a regex guess will miss real data

An item-ID detector based on a hyphen-count grammar missed 34 of the 270 IDs committed in the
benchmark files. The grammar guessed at data shape instead of consulting the authoritative data.
Load the exact committed ID set at run time from a path anchored to `__file__`, so the caller's
working directory cannot change the result. Test the mechanisms independently too: an exhaustive
test that first required a companion regex to match every ID masked the exact-set branch. Disable
that companion mechanism in one test and prove the exact-set lookup works alone.

## 4. A green suite is not a clean gate

The orchestrator directly observed one state in which the full suite reported `1268 passed` and the
subsequent manifest-bound leak scan found collisions in two of the four files. Worker reports
documented two further transient nonzero scans inside repair passes; those worker-internal
collisions were fixed before commit.
The suite and publication gates cover different failure classes, so a passing suite says nothing
about restricted-surface collisions or machine-local paths. After every repair pass, rerun both the
scanner and an absolute-path grep instead of deferring either to the end. Treat reviewer and worker
reports as claims and rerun the gates in the owning checkout.

## 5. Review artifacts are not publishable in a public repository

Codex review text produced non-zero collisions against the firm-surface manifest because it repeats
identifiers and ordinary words that occur in firm surfaces. Do not paste review prose into a PR
body, PR comment, or committed document in this repository. Scan every text destined for an
external surface, including a self-authored PR body, before sharing it. Keep the source artifacts
off-tree and record a receipt by verdict and artifact name only.
