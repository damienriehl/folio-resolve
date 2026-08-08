# eval/ — the F1 improvement-loop harness

Measures folio-resolve's strict IRI-set F1 against human-curated firm-taxonomy gold, runs the
correctable-gold audit gates, and drives measured improvement iterations. Authority:
`docs/plans/2026-07-27-001-feat-f1-improvement-loop-plan.md`. Current execution state:
`docs/handoffs/2026-07-28-f1-loop-gate1b-handoff.md`.

**Privacy first (KTD1/KTD12):** the source workbooks are confidential firm data and this repo is
public. Everything under `eval/data/` is gitignored except `MANIFEST.md` (hashed sheet names).
No committed file may contain a firm surface string — `folio_eval.clusters.assert_no_surfaces`
enforces it; run it on any new committed artifact. Original workbooks live outside the repo at
`~/.folio-resolve-eval-data/`.

## Modules (`eval/folio_eval/`)

| module | role |
|---|---|
| `intake.py` | manifest-verified loading of derived in-scope sheets (openpyxl stays function-local; KTD11) |
| `normalize.py` | NFKC/dash/whitespace label normalization, legacy `lmss.sali.org` IRI rewrite, pipe split |
| `resolve_labels.py` | label→IRI ladder (preferred → alternative → normalized → lemma → legacy IRI), offline ontology load with cache-hash abort |
| `gold.py` | gold derivation: v1 cascade (superseded) and v2 per-cell (KTD6 v2); versioned `gold_vN.jsonl` + manifests |
| `splits.py` | stratified frozen/tune split, family-atomic, invariants asserted at load |
| `answer_rule.py` | committed answer set: calibrated threshold + top-k, versioned config, gold-blind |
| `score.py` | strict (item, IRI) micro P/R/F1, recall@k, 1-hop near-miss buckets, per-item CSV (gitignored) |
| `report.py` | aggregate reports (committed), bootstrap CIs, paired-item deltas |
| `clusters.py` | failure clustering (truncated vs unreachable vs below-cutoff…), calibration fit, leak scanner |
| `audit.py` | audit-gate packets, granular decision fold (append-only, rejection memory), gold version bumps |
| `packet_render.py` | the private decision sheet (three-panel, source grids, editable applied rows) |
| `improve.py` | atom-proposal pilot (molecules→atoms pattern from Damien's rulings) |
| `experiment.py` | KTD8 iteration protocol: windows, AE4 tripwire, leak-guarded `experiments.jsonl` |
| `downstream.py` | consumer snapshot/diff (folio-mapper, folio-enrich) — blocking vs advisory per KTD10 |
| `selftest.py` | determinism self-test (subprocess, different hash seed) + ontology pinning |

Launchers: `eval/run_{score,baseline,audit,experiment,downstream}.py`, `eval/build_gold.py`.
Regenerating the audit packet: always pass `--clusters eval/data/reports/clusters_v2.jsonl`.

## Gold versioning

`gold_v1` cascade-union (superseded) → `gold_v2` per-cell (Damien's model) → `gold_v3` (+his Gate-1b
rulings, provenance `damien_corrected`). Every score cites its gold version and ontology cache hash;
gold bumps only at gate/check-in boundaries; Δgold and Δcode are reported as separate trajectory lines.
