# Verifier ceiling

Verdict: **no-go**.

Plan Success Criteria — "Abstention works": "well below the current 30 of 30" is operationalized as a no-match FP rate strictly below baseline and at most 0.5. Go also requires the paired strict-F1 95% interval entirely above zero and strict recall at least baseline recall. Laya retention is a later-stage criterion.

The grader mix below is computed from corpus provenance. A Codex verifier may agree with gold partly through shared model tendencies and overstate the ceiling.

Grader mix: {"corpus_rows_without_votes": 15, "passage_count": 225, "passages_by_mix": {"claude: 1, codex: 2": 225}, "scored_items_without_votes": 0, "votes_by_family": {"claude": 225, "codex": 450}}

| Arm | Strict P | Strict R | Strict F1 | TP | FP | FN | No-match FP rate | Failed items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| arm | 0.033300 | 0.184573 | 0.056421 | 67 | 1945 | 296 | 0.900000 | 0 |
| baseline | 0.011111 | 0.041322 | 0.017513 | 15 | 1335 | 348 | 1.000000 | 0 |

Full precision evidence, fold cuts, calibration and retrieval ceiling:

```json
{
  "arm": {
    "candidate_calibration": {
      "brier": 0.12608560627450982,
      "count": 25500,
      "ece": 0.15410972549019608
    },
    "cuts_by_fold": [
      {
        "admission_cut": 0.95,
        "fold": 0,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.95,
        "fold": 1,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.95,
        "fold": 2,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.95,
        "fold": 3,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.95,
        "fold": 4,
        "nomatch_cut": null
      }
    ],
    "expanded_metric": "Unavailable: the U2 scorer supplies no owner-approved expanded metric.",
    "failed_item_count": 0,
    "nomatch_calibration": {
      "brier": 0.11200583921568627,
      "count": 255,
      "ece": 0.11340784313725491
    },
    "nomatch_fp_rate": 0.9,
    "strict": {
      "f1": 0.05642105263157895,
      "fn": 296,
      "fp": 1945,
      "precision": 0.03330019880715706,
      "recall": 0.18457300275482094,
      "tp": 67
    }
  },
  "baseline": {
    "candidate_calibration": {
      "brier": 0.06156862745098039,
      "count": 25500,
      "ece": 0.06156862745098039
    },
    "cuts_by_fold": [
      {
        "admission_cut": 0.5,
        "fold": 0,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.5,
        "fold": 1,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.5,
        "fold": 2,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.5,
        "fold": 3,
        "nomatch_cut": null
      },
      {
        "admission_cut": 0.5,
        "fold": 4,
        "nomatch_cut": null
      }
    ],
    "expanded_metric": "Unavailable: the U2 scorer supplies no owner-approved expanded metric.",
    "failed_item_count": 0,
    "nomatch_calibration": {
      "brier": 0.11764705882352941,
      "count": 255,
      "ece": 0.11764705882352941
    },
    "nomatch_fp_rate": 1.0,
    "strict": {
      "f1": 0.017513134851138354,
      "fn": 348,
      "fp": 1335,
      "precision": 0.011111111111111112,
      "recall": 0.04132231404958678,
      "tp": 15
    }
  },
  "chosen_n": 100,
  "decision": {
    "arm_nomatch_fp_rate": 0.9,
    "arm_strict_f1": 0.05642105263157895,
    "arm_strict_recall": 0.18457300275482094,
    "baseline_nomatch_fp_rate": 1.0,
    "baseline_strict_f1": 0.017513134851138354,
    "baseline_strict_recall": 0.04132231404958678,
    "failing_criteria": [
      "abstention_works"
    ],
    "nomatch_fp_rate_cap": 0.5,
    "paired_delta": {
      "alpha": 0.05,
      "high": 0.049883813431586,
      "low": 0.02811431093456913,
      "n_resamples": 2000,
      "n_units": 225,
      "point": 0.038907917780440594,
      "seed": 20260727
    },
    "verdict": "no-go"
  },
  "grader_caveat": "The grader mix below is computed from corpus provenance. A Codex verifier may agree with gold partly through shared model tendencies and overstate the ceiling.",
  "grader_mix": {
    "corpus_rows_without_votes": 15,
    "passage_count": 225,
    "passages_by_mix": {
      "claude: 1, codex: 2": 225
    },
    "scored_items_without_votes": 0,
    "votes_by_family": {
      "claude": 225,
      "codex": 450
    }
  },
  "unreachable_gold_count_at_200": 280,
  "unreachable_gold_count_at_chosen_n": 293,
  "verdict_rule": "Plan Success Criteria \u2014 \"Abstention works\": \"well below the current 30 of 30\" is operationalized as a no-match FP rate strictly below baseline and at most 0.5. Go also requires the paired strict-F1 95% interval entirely above zero and strict recall at least baseline recall. Laya retention is a later-stage criterion."
}
```
