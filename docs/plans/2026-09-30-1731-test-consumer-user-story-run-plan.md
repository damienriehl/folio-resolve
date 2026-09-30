---
title: "test: Consumer user-story run on current main"
date: 2026-09-30
status: active
artifact_contract: ce-unified-plan/v1
execution: code
product_contract_source: cockpit-session-start
---

# Consumer user-story run — 2026-09-30

## Summary

`folio-resolve` is a library, so its "users" are integrators. This run walks
their most common workflows end to end on current `main`, as a consumer would
see them from an installed wheel, not only from inside the checkout. It judges
developer experience (error messages, documentation accuracy, import cost,
typing, defaults), not just pass or fail. Every error found is listed with its
fix. The result goes into `.cockpit-repo.json`'s `uat` block.

## Why now

The last recorded run was 2026-09-02 (`docs/uat/2026-09-02-uat-report.md`,
commit `4cf7876`). PR #45 re-ran it on 2026-09-18 at `20c231f` and found
US-EO-02 failing, a failure since fixed on `main`. Ten `src/` commits have
landed since `v0.4.0`, including a ranking change, and a 0.5.0 release is
pending (hygiene plan H6).

## Workflows walked

Personas and stories come from `docs/uat/personas.md` and
`docs/uat/user-stories.md` (24 stories, 8 personas).

1. **Persona suite, core leg**: `uv run --isolated --extra dev pytest tests/uat -m uat`.
   No optional extras.
2. **Persona suite, extras leg**:
   `FOLIO_RESOLVE_UAT_REAL_ONTOLOGY=1`, with the real FOLIO ontology and the
   `folio` and `spacy` extras installed. This leg needs network, so it runs
   orchestrator-side.
3. **Installed-wheel consumer walk**: build the wheel and install it into a
   fresh virtualenv outside the checkout, then run each workflow as a script:
   - **Quick start**: the README pipeline example, copied verbatim, including
     its documented output (for example, the 99.0 and 88.0 scores).
   - **Scoring only**: `score`, `generate_search_terms`, blocklist and gates.
   - **Resolve**: label-to-IRI resolution, including the "law" → Delaware guard.
   - **Annotate**: confidence, verdicts, reject/restore, notes, insights.
   - **Judge**: a provider-neutral fake judge, malformed model output, and no judge.
   - **Optional extras missing**: calling an extras-only path without the extra
     must fail with an actionable message that names the extra.
   - **Typing**: `py.typed` ships, and a consumer `mypy --strict` snippet type-checks.
   - **Import cost**: `import folio_resolve` stays light, with no heavy optional imports.
4. **Release maintainer**: `uv build` produces an sdist and a wheel, twine-style
   metadata checks pass, the version is exposed, and extras resolve independently.

## Judging criteria

For each step, record: works / works-with-friction / broken. Friction counts as
a finding: a README example that no longer runs verbatim, an error without
remediation text, a surprising default, or a missing type. Each finding gets a
severity (P0 blocks consumers; P1 misleads them; P2 friction; P3 polish) and a
disposition (fixed in this run, with its commit, or deferred, with a reason).

## Units

### T1 — Consumer-walk harness

A Codex worker writes `tests/uat/consumer_walk.py`. It builds the wheel, creates
a temp venv, installs the wheel, and runs each workflow in (3) as a subprocess,
emitting a JSON verdict per step. It must run offline from the uv cache where
possible; steps that need network are tagged and skipped offline, with a reason.
Test-first: a unit test exercises the harness against a failing and a passing
step.

### T2 — Run all legs

The orchestrator runs legs 1–4 with network and keeps the JUnit and JSON outputs
under the gitignored `.codex-out/uat/`.

### T3 — Fix loop

One Codex worker per independent finding, each fix with a regression test.
Re-run the affected leg after each fix. Documentation-drift findings fix the
docs; library defects fix the code.

### T4 — Report and declaration

- Regenerate `docs/uat/2026-09-30-uat-report.md` with
  `tests/uat/build_report.py`, and append a "Consumer walk" section listing
  every finding with its disposition.
- Close PR #45 as superseded (hygiene D6).
- Write `.cockpit-repo.json` `uat`: `status: done` when no P0 or P1 remains
  open, otherwise `in_progress`; `date: 2026-09-30`; `open_failures` set to
  short public-safe strings for anything deferred.
- Leak-preflight the report before commit.

## Verification

- Full core suite green, plus `ruff check` and `mypy`.
- Both persona legs report all 24 stories passing, or each failure is explained
  and dispositioned.
- The consumer walk JSON shows every step `works`, or each step's finding is
  dispositioned.
