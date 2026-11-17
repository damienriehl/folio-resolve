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

## Content-free campaign readiness

`check_campaign_readiness.py` summarizes an **explicitly supplied, sanitized attestation
snapshot** for `u10` or `retrieval`. It opens only that snapshot and its own runtime/source
dependencies. It does not inspect receipts, locks, consumer checkouts, budgets or evaluation
inputs; run producers; or contact providers. Python 3.11+ and the standard library suffice.

```bash
python3 -I eval/check_campaign_readiness.py --snapshot demo-partial.json
python3 -I eval/check_campaign_readiness.py --snapshot demo-partial.json --format markdown
```

The default format is JSON. Both formats are deterministic, end with a newline and describe
the same normalized states, reason codes and fixed follow-up descriptions. Reordering input
keys or checks does not change output bytes. There is no output-file option or discovery mode.

Every summary states `evidence_basis: operator_attestation`,
`underlying_verification: not_performed`, `execution_authorization: not_granted` and
`adoption_authorization: not_granted`. **Full attestation coverage is not campaign completion,
execution permission, independent verification, or an adoption verdict.** Even fully supplied
records can report failure, conflict or unknown state. Currentness is itself an attestation
about the controller's intended campaign snapshot, not a date calculation or repository probe.
An assigned role neither proves exclusive ownership nor authorizes a run.

### Snapshot schema (version 1)

The root object requires integer `schema_version: 1` (not a boolean or float) and `campaign`,
exactly `u10` or `retrieval`. Its only other allowed key is `checks`, an optional array defaulting
to empty. Each check is an object with a required `check` from the inventory below. Its kind is
fixed by that inventory; callers cannot supply a kind, extra checks or their own denominator.

| Field | Applies to | Accepted values | Omission default |
|---|---|---|---|
| `binding` | Binding | `matched`, `mismatched`, `not_checked`, `conflict` | `not_checked` |
| `presence` | Receipt | `present`, `absent`, `unknown` | `unknown` |
| `outcome` | Receipt | `passed`, `failed`, `not_checked`, `conflict` | `not_checked` |
| `assignment` | All checks | `assigned`, `unassigned`, `unknown`, `conflict` | `unknown` |
| `role` | All checks | `controller`, `evaluation_operator`, `reviewer`, `unassigned` | `unassigned` |
| `currentness` | All checks | `current`, `stale`, `unknown`, `conflict` | `unknown` |

Roles describe responsibility only: `controller` coordinates the campaign and publication;
`evaluation_operator` supplies binding/run attestations; `reviewer` supplies receipt-review
attestations. The `controller_assignment` ownership row allows only `controller` or `unassigned`
as its role. If both assignment and role are supplied, `assigned` requires a concrete role and
`unassigned` requires role `unassigned`. Omitting either keeps the record incomplete.

Receipt `absent` with outcome `passed` or `failed` is invalid. `absent` with `conflict` remains
valid and unresolved. A passed receipt alongside a mismatched binding, stale currentness or
missing ownership remains valid, with every unresolved reason retained. Binding and ownership
rows do not accept receipt fields; receipt and ownership rows do not accept `binding`.

Unknown keys, wrong types/enums, duplicate JSON keys, duplicate checks, non-JSON numeric constants
and unsupported versions are rejected. Never put personal names, prose, evidence paths, item
identifiers, fingerprints, digests, commands, metrics or verdicts into a snapshot. Do not point
the command at raw evaluation artifacts: the controller supplies these finite status records.

### Fixed inventories

| Campaign | Kind | Required checks (each is a separate row) |
|---|---|---|
| `u10` | Binding | `candidate_source_runtime`, `corpus_config`, `leak_manifest`, `public_metadata_output`, `pinned_consumer_environments` |
| `u10` | Ownership | `controller_assignment` |
| `u10` | Receipt | `shard_receipt_validation`, `final_completion`, `independent_regeneration`, `zero_collision_acceptance`, `campaign_report_publication` |
| `retrieval` | Binding | `corpus_config_population`, `attribution_input_artifacts`, `pinned_consumer_environments`, `shared_campaign_directory`, `cumulative_budget_ledger` |
| `retrieval` | Ownership | `controller_assignment` |
| `retrieval` | Receipt | `attribution_finalization_r4`, `canary_projection_review`, `folio-enrich-deterministic`, `folio-mapper-deterministic`, `folio-mapper-gemini-3-flash-preview`, `folio-mapper-gpt-6-luna`, `embedding_ceiling`, `llm_ceiling`, `final_report_pair`, `zero_collision_acceptance`, `experiment_park_entry` |

U10 has 11 required checks; retrieval has 17. U10 independent regeneration is separate from
validating an existing completion receipt. Zero-collision acceptance is an explicit attestation;
a successful process exit does not supply it. The summary performs no manifest-backed leak scan.

The September 30 retrieval and downstream plans describe six consumer arms. The current
`recall_consumers.REQUIRED_PUBLISHED_ARMS` and `recall_report` contract requires the four arms
listed here; paid enrich arms are disabled by owner decision. This summary preserves that
contract and changes neither the U10 R17 verdict nor retrieval R11 adoption gates.

### Fabricated examples

Save this partial example as `demo-partial.json`. It leaves every other required check visible
as missing and marks this row partial because ownership and currentness are omitted.

<!-- readiness-partial-example -->
```json
{
  "schema_version": 1,
  "campaign": "u10",
  "checks": [
    {"check": "final_completion", "presence": "present", "outcome": "passed"}
  ]
}
```

The following Python example generates entirely fabricated favorable U10 attestations. Run
with `PYTHONPATH=eval`, redirect stdout to `demo-favorable.json`, then use the same CLI commands.
Change `campaign` to `retrieval` for its example. These fabricated claims must never be used as
real campaign evidence; the generator only illustrates the schema.

<!-- readiness-favorable-example -->
```python
import json
from folio_eval.campaign_readiness import INVENTORIES

campaign = "u10"
checks = []
for check, kind in INVENTORIES[campaign]:
    record = {
        "check": check,
        "assignment": "assigned",
        "role": "controller",
        "currentness": "current",
    }
    if kind == "binding":
        record["binding"] = "matched"
    elif kind == "receipt":
        record.update(presence="present", outcome="passed")
    checks.append(record)
print(json.dumps({"schema_version": 1, "campaign": campaign, "checks": checks}))
```

### Interpreting results and exits

`counts` reports required, supplied, fully attested and unresolved checks. A row's `attestation`
is `missing`, `partial` or `complete` according to supplied fields, regardless of favorable
or adverse values. Overall `attestation_coverage` is `none` when no rows were supplied,
`complete` when every required row has all applicable fields, and `partial` otherwise.
`unresolved_reasons` preserves missing/partial evidence, binding problems, receipt presence
and outcome, assignment/role gaps, and currentness problems in that order. Fixed `follow_up`
descriptions point to the responsible roles; they are reminders, not executable instructions.

| Exit | Meaning | Stderr diagnostic |
|---|---|---|
| `0` | Summary generated, including incomplete/adverse snapshots; or help displayed | none |
| `2` | Invalid arguments, unreadable/non-UTF-8 snapshot, or invalid schema/JSON | `invalid_arguments`, `snapshot_unreadable`, or `invalid_snapshot` |
| `1` | Output could not be written/flushed | `output_failed` when stderr is writable |

Invalid input produces no partial summary and no echoed values, paths or parser exceptions.
Interpreter/shell errors outside this command are outside its diagnostic contract.
Underlying validators still own receipt validation and measured results. The controller owns
source reconciliation, publication scans and publication. This summary grants no permission
to execute those steps and writes no campaign state.

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
`tp + fp = predicted_count`. When recall and verifier sections are both present, each verifier
true-positive total must not exceed the corresponding shortlist gold hits. Required keys remain
present even when their value is `null`.

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
