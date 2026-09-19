# Synthetic scorer manifest freshness fix

Implement the defect fix authorized in `docs/handoffs/2026-09-18-codex-pickup.md`
and the current session request, on a new branch based on main.

1. Confirm the real loader fails with the machine's local gold manifests.
2. Add a regression in `tests/test_eval_synthetic_score.py` using the real
   `load_manifest` and a temporary mismatched gold manifest, following `f295930`.
   Observe the stale-manifest failure before changing production code.
3. Pass `allow_stale=True` in the synthetic scorer CLI, mirroring the sibling
   experiment CLI. Keep manifest validation and publication leak scanning intact.
4. Run the regression, synthetic scorer and experiment tests, and both core and
   real-ontology UAT suites. Check lint and the final diff.

Do not push, merge, modify the docs-only UAT branch, or touch U10 comparison v2
leak triage. Preserve unrelated pre-existing files. Shipping review belongs to
the eventual shipping session, as specified in the selected handoff.
