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

## Offline consumer adoption scorecards

Run the fixed, generated demonstration after installing the repository's locked development
dependencies:

```bash
uv run --offline --no-sync python eval/run_adoption_scorecard.py \
  --fixture-demo --out-dir scorecard-demo
```

The destination must be new and its parent must exist. The command writes `scorecards.json`
and `scorecards.md`, with one card for enrich deterministic and each of mapper deterministic,
Gemini and Luna. The fixture gives enrich a positive paired gain and mapper deterministic a
zero-gain failure. These are artificial examples, not live measurements. No provider, consumer
checkout, ontology download, protected input or credential is needed.

Each card reports shared recall/verifier gates, its own paired micro-F1 gain, strict flag-off
parity, and baseline/candidate precision, recall, F1 and no-match false-positive rate. Controls
stay outside F1. The control numerator counts items with **any** prediction, not total predictions;
zero controls make the rate unavailable. There is no additional control-rate rejection threshold.
The item bootstrap reuses `report.bootstrap_ci` and `report.f1_delta`: 2,000 resamples, seed
`20260727`, two-sided 95% percentile interval. Gate comparisons use unrounded values. Identical
bytes produce identical reports in the supported Python environment; reordering rows preserves
metrics but changes the raw input digest.

A gate is `pass`, `fail` or `missing`. Any measured failure makes the card `fail`; otherwise any
missing prerequisite makes it `missing`. `complete` separately records whether evidence is
missing. `adoption_authorized` is always false. Source authentication, live measurements,
reviewed opt-in integration and actual R11 adoption remain outside this tool. Existing R17
verdicts and advisory downstream diffs are unchanged.

For a separately prepared bundle, supply all inputs explicitly:

```bash
uv run --offline --no-sync python eval/run_adoption_scorecard.py \
  --input evidence.json --expected-sha256 EXPECTED_RAW_SHA256 \
  --leak-manifest supplied-manifest.json --salt-file supplied-scan-salt \
  --out-dir scorecard-recorded
```

The expected SHA-256 authenticates the exact supplied file bytes before parsing; it does not
establish that a source reference is an authentic R14 baseline. Manifest and salt must be
supplied together and match. This reader validates that explicit manifest without discovering
other files or checking a local gold-version registry; the operator must select the current
appropriate manifest. Fixture mode uses a pinned fixture digest and generated scan material,
and rejects explicit-input flags. It cannot scan arbitrary evidence under the fixed demo.

### Evidence bundle v1

`eval/fixtures/adoption-scorecard-v1.json` is a complete, wholly generated example. The strict
models in `eval/folio_eval/adoption_scorecard.py` are the executable field contract. Unknown
fields, duplicate JSON properties, nonfinite values, invalid counts, duplicate/unequal cohorts,
and mismatched identities reject the whole input. Counts are nonnegative integers, never
booleans. A scoreable row must satisfy `tp + fn = gold_count > 0` and
`tp + fp = predicted_count`. Required keys remain present even when their value is `null`.

| Section | Contract |
| --- | --- |
| Envelope | `schema_version: 1`, `evidence_kind: generated_fixture` or `recorded_input`, common `binding`, `lever_sha256`, `verifier_identity`, `cohort`, `recall`, `verifier`, `arms`. |
| Binding | Lowercase SHA-256 values for `corpus_sha256`, `gold_sha256`, `ontology_sha256`, `scoring_sha256`. Each section must match the common binding and lever. References are digests, not paths; no referenced source is fetched. |
| Cohort | Positive `scoreable_count`, nonnegative `no_match_count`, `scoreable` rows containing opaque `item_key` and positive `gold_count`, and a disjoint `no_match` key list. Keys contain 1–128 letters, digits or `_.:-`; do not encode text or IRIs. |
| Run | `source_sha256`, complete `scoreable` count rows and complete `no_match` rows with `item_key` and `predicted_count`. Per-item gold counts match the declared cohort everywhere. |
| Arm | Fixed `consumer` and `model` (`null` for deterministic), binding/lever, `baseline`, `candidate`, `parity`. A baseline adds `r14_reference_sha256` to a run. Both source digests must differ. Arm slots may be omitted; all four are still reported. Each missing prerequisite is `null`. |
| Verifier | Binding/lever, `identity` (verifier and configuration SHA-256), paired baseline/candidate runs. Identity matches the envelope. Entire section may be `null`. |
| Recall | Binding/lever and verifier identity, baseline/candidate source SHA-256 matching the verifier runs when supplied, total `gold_count`, baseline/candidate `hits` and `depth`. Hits cannot exceed the gold denominator. A positive depth other than 100 is valid evidence of a failed gate. Entire section may be `null`. |
| Parity | Binding/lever, the same arm's baseline/candidate source SHA-256, `flag_enabled: false`, `projection_version: semantic-result-v1`, and baseline/candidate lists of `item_key`, `output_sha256` covering every scoreable and control key. It requires both arm runs; use `null` when observations are absent. |

Producers compute each parity hash using `semantic_digest(complete_result)`. The complete
structured result includes ordered results, scores, abstentions and returned span/location
fields. Only JSON object keys are sorted; meaningful array order is preserved. Timing and
execution metadata belong outside the result. There are no configurable field exclusions.
The digest includes the projection version. Matching hashes establish equality for the declared
observations only; the tool cannot prove a producer captured every semantic output field.

Exit status 0 means a valid report was written, even if gates failed or remain missing. Invalid
arguments/evidence, a leak collision, occupied destination or I/O error returns 2 with a fixed
content-free diagnostic. Both renderings pass the real leak scanner before publication. No
item keys, observation hashes, input paths or arbitrary metadata are emitted. Publication uses
Linux `renameat2(RENAME_NOREPLACE)` to expose the complete pair atomically without replacing even
an empty directory created by another invocation. Other platforms fail closed if that primitive
is unavailable. A failed staging/publish operation retains its generated staging directory in
the destination parent for inspection and leaves prior artifacts untouched.
