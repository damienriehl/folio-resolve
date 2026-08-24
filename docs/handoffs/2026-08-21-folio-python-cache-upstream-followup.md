---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-21T14:59:10-05:00"
title: "Reassess the local folio-python search-cache workaround after upstream PR #20"
summary: "Records the local bounded-cache containment, the merged-but-unreleased upstream replacement, and the evidence required before simplifying or removing the workaround."
keywords: ["folio-python", "cache", "memory", "upstream", "follow-up", "U8"]
cwd: "/home/damienriehl/Coding Projects/folio-resolve"
resume_focus: "Wait for a folio-python release containing merged PR #20; then update the dependency in an isolated branch, rerun the memory and deterministic-baseline gates, and decide which parts of the local workaround can safely be removed."
repository: "damienriehl/folio-resolve"
branch: "docs/folio-python-cache-followup-2026-08-21"
---

# Reassess the local folio-python cache workaround after upstream PR #20

## Current state

- Local containment is committed in folio-resolve as `f9ee23d` (`fix(ontology): bound upstream
  search cache retention (U8)`).
- The local adapter copies normalized results into per-provider 256-entry LRU caches and clears
  folio-python's private `_basic_search`, `_prefix_cache`, and `_ci_prefix_cache` stores after each
  upstream search, including exception paths.
- The corresponding upstream change was reviewed at `fe6d02e` and merged through
  [alea-institute/folio-python#20](https://github.com/alea-institute/folio-python/pull/20) on
  2026-08-22 as squash commit `1b4fb3493815ca7aeb0ce7f454399c19723f9820` after all six CI
  architecture jobs passed.
- PR #20 bounds folio-python's basic-search and prefix-cache families at 128 entries. The source
  change is now merged, but it is **not released**: the latest release remains `v0.4.0`, published
  on 2026-08-18 before the merge. Consequently, the reassessment trigger is only partially met
  and the local containment remains required.

## Why the local workaround exists

The locked folio-python 0.3.6 search layer retains every unique query and its full result corpus.
One synthetic passage created 1,707 basic-search and 766 prefix-cache entries and reached roughly
1.29 GiB RSS. The earlier 225-passage U8 run reached roughly 30.5 GiB RSS. With the local
containment, the same real one-passage path left the upstream caches at zero, retained 768 bounded
outer search entries, and peaked at 715,524 KiB (about 699 MiB).

The local code deliberately probes private upstream attributes for compatibility. That coupling is
acceptable as temporary containment, but it should be reassessed once the upstream package owns a
bounded-cache policy.

## Reassessment trigger

Do not remove the local behavior merely because the PR merges. Start this follow-up only when all
of these are true:

1. Upstream PR #20 is merged.
2. A folio-python release containing the merge is published.
3. folio-resolve can update its locked dependency to that release on an isolated branch.

## Removal and verification checklist

1. Record the upstream merge commit, release tag, and resolved installed version.
2. Update the folio-python dependency and lockfile; run the repository's lock-consistency gate.
3. Run the ontology unit tests, the complete test suite, Ruff, strict mypy, and `git diff --check`.
4. Repeat the real one-passage memory probe and record upstream cache sizes, bounded outer-cache
   size, peak RSS, and exit status.
5. Run the complete 225-passage U8 deterministic baseline with the frozen corpus, config, leak
   manifest, salt, and public-metadata inputs.
6. Compare the resulting report byte hash and metrics with the accepted pre-upgrade baseline. Any
   ranking or metric change requires diagnosis before removing compatibility code.
7. Remove `_release_upstream_search_caches` only if the released upstream bounds are confirmed in
   the installed code and the full-run memory envelope remains safe without forced clearing.
8. Evaluate the provider-level 256-entry copied-result cache separately. Keep it if direct
   `MatchPipeline` or `DomainPriorSuggester` calls still need bounded reuse; upstream containment
   alone does not prove that this local reuse layer is redundant.
9. If folio-resolve must continue supporting folio-python 0.3.6, retain a version-gated or
   attribute-probed compatibility path rather than deleting the workaround wholesale.
10. Commit the dependency update and any justified simplification with the memory and deterministic
    report evidence, then update this handoff with the final disposition.

## Decision rule

- **Remove or simplify** the private-cache clearing only when the released upstream implementation
  passes both the one-passage probe and the full baseline without unacceptable memory growth or
  output drift.
- **Retain the local containment** if the 128-entry upstream caches still retain too much corpus
  data, the supported-version range includes the affected release, or deterministic output changes.
- This is an evidence-bound engineering decision; no owner taste decision is required unless the
  measured memory budget itself needs to change.

## Safety and scope

This handoff contains no credentials, protected corpus content, personal roster data, or private
surface strings. It records aggregate counts and public repository references only.
