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
