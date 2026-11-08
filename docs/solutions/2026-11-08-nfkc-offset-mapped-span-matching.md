---
title: "Offset-mapped NFKC span matching needs three invariants together"
lane: folio-resolve (shared matching engine)
module: spans
date: 2026-11-08
problem_type: logic_error
component: service_layer
severity: medium
symptoms:
  - "A label with internal punctuation (AT&T, U.S. citizen) never matches its own surface text"
  - "A label \"½ interest\" matches the text \"1 2 interest\""
  - "The text \"½\" is anchored as the word \"2\"; \"㏂\" is anchored as \"m\""
  - "The label \"a.m.\" fails to match \"㏂\" even though both normalize to the same key"
root_cause: logic_error
resolution_type: code_fix
tags: [unicode, nfkc, normalization, offsets, span-tagging, tokenizer, n-gram, review-regressions]
status: solved
related:
  - docs/solutions/2026-09-07-review-gate-catches-self-inflicted-regressions.md
  - docs/benchmarks/span-tagging-real-data.md
---

# Offset-mapped NFKC span matching needs three invariants together

## Problem

`folio_resolve.spans` (PR #75, v0.5.0) matches labels and headwords on NFKC-casefolded token keys but reports every span in original-text offsets. One NFKC expansion can turn a single source character into several key characters ("½" becomes "1⁄2", "㏂" becomes "a.m."), so several tokens share one source range. Getting matching right took four Codex review rounds, and each fix after the first exposed the next defect. The correct design holds three invariants at once; dropping any one reintroduces a defect below.

## Symptoms

- Round 1: hundreds of real OpenGloss headwords with internal punctuation (the round 1 reviewer counted 775), such as `AT&T` and `attorney/client privilege`, could not match their own text.
- Round 2: "½ interest" matched "1 2 interest", because the "⁄" vanished from the key.
- Round 3: "½" alone tagged OpenGloss senses of "2"; with labels "½ interest" and "2 interest", the suffix concept could replace the fraction concept on the same span.
- Round 4: the round 3 guard rejected the complete match "a.m." against "㏂" and "(株) Company" against "㈱ Company".

## What Didn't Work

- **Keying on words only, joining across whitespace and hyphens.** Tokens matched, but any other gap broke the phrase walk, so punctuated labels were unreachable. Accepting any gap instead would let a phrase cross a sentence boundary ("tenant. Rent" matching "tenant rent").
- **Slicing gaps from the original text between token offsets.** Tokens born inside one expansion share the same source range, so the slice between them is empty and the gap normalizes to a space.
- **Start and end flags per token ("begins at an expansion start", "ends at an expansion end").** This rejected partial expansions, but an expansion that begins or ends with punctuation ("(株)", "a.m.") left its outer word tokens unflagged, so complete matches failed.

## Solution

The three invariants, as built in `src/folio_resolve/spans/`:

1. **Gaps are part of the key.** `normalize()` interleaves word tokens with normalized gaps (`tokenize.py:77`). `normalize_gap()` folds whitespace and hyphen-class gaps to one space and keeps any other punctuation with whitespace removed (`tokenize.py:88`). "fully-indexed" still equals "fully indexed", "AT&T" equals itself, and "tenant. Rent" never equals "tenant rent".
2. **Gaps come from the normalized stream.** Each `Token` carries `gap_before`, cut from the folded string rather than the original text (`tokenize.py:18`, `tokenize.py:66`). Labels and input use the same field, so equivalent spellings produce equal keys.
3. **A match covers whole source characters.** A match is rejected when the word token just outside it, on either side, shares a source character with it (`index.py:70-73` for the left edge, `index.py:95-96` for the right). Peripheral punctuation inside an expansion produces no word token, so it never causes a rejection.

Same-layer hits that land on one span merge their anchors instead of replacing each other.

## Why This Works

Character-level normalization breaks the one-to-one mapping between key positions and source positions. Invariant 1 makes the key distinguish every gap a human reader distinguishes. Invariant 2 makes the key a function of the normalized text alone, so NFKC-equivalent inputs cannot diverge. Invariant 3 restores the source-level unit: a reported span is always a set of whole source characters, so its reported surface normalizes to exactly the key it matched.

## Prevention

- Treat these regressions in `tests/spans/` as one contract and run them all after any tokenizer or index change: `test_internal_punctuation_matches_its_own_surface`, `test_sentence_boundary_does_not_join_a_phrase`, `test_nfkc_fraction_label_does_not_match_space_separated_numbers`, `test_partial_source_expansions_are_not_matches`, `test_complete_expansions_ignore_peripheral_punctuation`, and `test_competing_fraction_labels_anchor_only_the_complete_expansion`.
- When fixing one direction of a matching error (false match), add a test for the opposite direction (false rejection) in the same change. Rounds 2 to 4 here alternated between the two, as the review-gate learning predicts.
- Bump `Provenance.normalization` (`models.py:81`) whenever key construction changes, because stored annotations from an older key scheme are not comparable.
- Rerun `benchmarks/span_tagging_real_data.py`; its headword self-match count is the real-data check that every headword reaches its own surface (0 unexpected failures at v0.5.0).
