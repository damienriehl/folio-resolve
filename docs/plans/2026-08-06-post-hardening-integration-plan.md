---
title: Post-Hardening Integration - Plan
type: chore
date: 2026-08-06
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Post-Hardening Integration - Plan

## Goal Capsule

- Convert the verified folio-resolve and mootloop hardening branches into a measured, integrated, and durable release state.
- Treat confidential F1 evaluation as an owner-run gate and never access protected evaluation rows or gold packets.
- Preserve unrelated local state, including `uv.lock` and mootloop's timer-managed `.claude/RESUME.md`.
- Stop the folio-resolve release path if no owner-produced measurement is available or if the measurement fails its acceptance threshold.

---

## Product Contract

### Summary

Complete the post-hardening integration in dependency order. Prepare the private evaluation command and evidence contract first. Merge the independent mootloop reopen work before revalidating the broader hardening PR. Publish durable coordination artifacts. Make the folio-resolve merge, release, and consumer-pin decision only from owner-produced measurements.

### Problem Frame

The code changes have strong local verification, but three different gates remain. The recall change needs confidential aggregate measurement. The mootloop PRs need sequencing and a post-merge integration run. Local-only handoff and Cockpit commits can still be lost. A consumer update is unsafe until a new folio-resolve version is both justified and present on PyPI.

### Requirements

**Confidential evaluation**

- R1. Provide Damien with an exact private evaluation procedure and expected result artifact without reading or scoring protected rows.
- R2. Do not claim an F1 or recall improvement until Damien supplies aggregate results from the gated runner.
- R3. Merge folio-resolve PR #2 only when the owner-produced result meets the existing plan's acceptance gate without unacceptable regressions.

**Mootloop integration**

- R4. Merge mootloop PR #31 before integrating PR #30 because PR #31 is the smaller operational dependency.
- R5. Revalidate PR #30 against the advanced `main` branch with the complete backend suite and static checks before merging.
- R6. Treat missing GitHub checks as an explicit local-verification posture, not as evidence that CI passed.

**Durability and release**

- R7. Publish the handoff and Cockpit commits without including timer-managed or unrelated working-tree state.
- R8. Determine `uv.lock` policy from repository conventions and packaging intent before adding or deleting it.
- R9. Release and update downstream pins only after the new folio-resolve version is verified as published on PyPI.

### Scope Boundaries

- Protected `eval/data/**`, firm rows, gold packets, and firm scoring are outside the implementer's access boundary.
- `*.pem`, `*.key`, `twin-secrets/`, and `~/.secrets` remain outside scope.
- `fence-litigation/` and `bayless-aerials/` remain outside the perimeter.
- No downstream pin changes occur before a measured folio-resolve merge and verified package publication.

---

## Planning Contract

### Key Technical Decisions

- KTD1. Use an owner-run measurement gate for folio-resolve PR #2. (session-settled: user-approved — chosen over agent-run scoring: the evaluation data is confidential and outside the implementer's boundary.)
- KTD2. Merge PR #31 before revalidating PR #30. (session-settled: user-approved — chosen over merging the broad hardening PR first: the reopen change is smaller and establishes the operational primitive first.)
- KTD3. Use explicit real-box pytest basetemps outside `/tmp` for mootloop verification because Codex can plant `/tmp/.git` and invalidate vault tests.
- KTD4. Treat publication as a separately verified release gate because local versions and PyPI have diverged before.

### Sequencing

U1 prepares the confidential handoff and blocks U5 until owner results arrive. U2 advances mootloop `main`. U3 then integrates and verifies PR #30 against that new base. U4 is independent but must preserve unrelated work. U5 executes only after R3's measurement gate passes.

---

## Implementation Units

### U1. Prepare the owner-run evaluation gate

- **Goal:** Make the confidential measurement reproducible without accessing protected inputs.
- **Requirements:** R1, R2, R3.
- **Files:** `docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md`, safe evaluation harness documentation, and PR #2 metadata.
- **Approach:** Inspect only safe harness code and existing instructions. Record the command, baseline, expected aggregate fields, and decision threshold. Do not execute scoring.
- **Test Scenarios:** The procedure resolves all required paths on Damien's box; its output is aggregate-only; no protected input is printed or committed.
- **Verification:** Review the documented command and privacy boundary without opening `eval/data/**`.

### U2. Merge the reopen verb

- **Goal:** Land mootloop PR #31 as the first integration dependency.
- **Requirements:** R4, R6.
- **Files:** PR #31 and mootloop `main`.
- **Approach:** Refresh mergeability and review state, confirm the verified head SHA, then merge through GitHub.
- **Test Scenarios:** The PR head is unchanged from the fully verified commit; GitHub reports mergeable; no unresolved review exists.
- **Verification:** Confirm the merge commit is reachable from live `main`.

### U3. Revalidate and merge the broad hardening PR

- **Goal:** Prove PR #30 remains correct after U2 and then land it.
- **Requirements:** R5, R6.
- **Files:** PR #30 and mootloop `main`.
- **Approach:** Integrate current `main` into the PR branch, preserve `.claude/RESUME.md`, run Ruff, mypy, and the complete pytest suite with an explicit `/dev/shm` basetemp, push, and merge only if all gates pass.
- **Test Scenarios:** The integration is conflict-free or conflicts are resolved without dropping either PR's contracts; the full collected suite exits zero; static checks pass; the final head is mergeable.
- **Verification:** Use the repository's full backend verification commands and confirm the merge commit on live `main`.

### U4. Publish coordination artifacts and resolve lock policy

- **Goal:** Make the handoffs and Cockpit correction durable while deciding the root `uv.lock` disposition from evidence.
- **Requirements:** R7, R8.
- **Files:** `docs/handoffs/2026-08-06-codex-orientation.md`, the isolated Cockpit queue change, `pyproject.toml`, `.gitignore`, contributor documentation, and `uv.lock` only if policy supports it.
- **Approach:** Push or PR existing isolated commits. Inspect packaging and repository conventions. Preserve `uv.lock` until the policy is established.
- **Test Scenarios:** Published branches contain only intended files; timer-managed state is absent; the lock decision is consistent with repository documentation and CI.
- **Verification:** Inspect each branch diff and remote ref; run the appropriate documentation or packaging checks if the lock policy changes tracked files.

### U5. Execute the measured release decision

- **Goal:** Merge, release, and propagate folio-resolve only when the private measurement supports it.
- **Requirements:** R3, R9.
- **Files:** PR #2, folio-resolve version metadata, release configuration, and downstream dependency files in folio-enrich, folio-insights, folio-mapper, and generative-folio as applicable.
- **Approach:** Compare Damien's aggregate result with the baseline and gate. If accepted, merge, release, verify the exact PyPI artifact, then update and test consumers. If rejected or unavailable, leave PR #2 unmerged and record the measurement outcome for the next single-variable tuning iteration.
- **Test Scenarios:** The published version resolves from PyPI; each consumer installs the published artifact rather than an editable checkout; consumer locks and tests agree with the selected version.
- **Verification:** Query PyPI after publication and run each consumer's real installation and test workflow.

---

## Verification Contract

| Gate | Applies to | Verification | Done signal |
|---|---|---|---|
| Private aggregate evaluation | U1, U5 | Damien runs the gated F1 procedure | Aggregate result and regression fields are supplied without protected rows |
| Mootloop reopen merge | U2 | GitHub head/review/merge inspection | Verified head is reachable from live `main` |
| Mootloop hardening integration | U3 | Ruff, mypy, and full pytest with `/dev/shm` basetemp | Every command exits zero on the integrated head |
| Artifact publication | U4 | Branch diff and remote-ref inspection | Handoffs and Cockpit correction exist remotely with no unrelated files |
| Package release | U5 | PyPI version/artifact lookup plus consumer installs | Exact released version resolves and downstream suites pass |

---

## Definition of Done

- U1 is complete when Damien has a safe, exact measurement procedure and no protected data was accessed.
- U2 is complete when PR #31 is merged and its commit is on live mootloop `main`.
- U3 is complete when PR #30 passes the post-U2 full verification and is merged.
- U4 is complete when both coordination artifacts are remote and the `uv.lock` decision is documented or enacted without unrelated changes.
- U5 is complete only when a passing private result leads to a verified published release and tested consumer pins, or when a non-passing result is durably recorded and the release path stops.
- No abandoned integration edits, temporary branches, or experimental code remain in canonical diffs.
