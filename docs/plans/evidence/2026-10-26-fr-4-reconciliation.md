# fr-4 repository reconciliation — 2026-10-26

## Recorded revisions

- Planning observation: `faf9bba5a1969daba0cb021f7de01b14e4bfe3e8` (recorded by the committed plan; not treated as the build checkout).
- Build start: `4c2444c13eadcc3c5524ae6964a64b884cf7b9b3`.
- Controller-confirmed base: unavailable in this isolated checkout.

## Visible inventory and dispositions

| Item | Recorded state | Disposition |
|---|---|---|
| Current branch `work` | tip `4c2444c13eadcc3c5524ae6964a64b884cf7b9b3`; one visible worktree; clean before implementation | Retain. This is the controller-provided build worktree even though the dispatch names `drawdown/fr-4`; do not switch or rename it here. |
| Current worktree | attached to `work`; no additional visible worktrees | Retain in place. Implementation adds only the scoped fr-4 changes. |
| Local `main` | not visible | Unknown; absence is not evidence of deletion or merge. Controller owns comparison with its current base. |
| Remotes | none visible | Do not add one. Controller owns publication and remote inventory. |

Historically named `eval/u10-*`, `feat/u10-resumable-pilot*`, and UAT/hygiene refs are not visible. They remain controller-owned unknowns; `fr-1` retains U10 integration ownership. No ref or worktree was moved, removed, pruned, or force-updated. The reconciliation is intentionally limited to refs exposed by this checkout.
