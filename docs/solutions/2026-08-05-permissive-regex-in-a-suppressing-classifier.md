---
title: A permissive regex in a *suppressing* classifier fails silently — the ISBN/METADATA bug
date: 2026-08-05
lane: folio-resolve (shared matching engine)
tags: [regex, classifier, silent-failure, fail-open, fail-closed, sources, metadata, testing, blast-radius]
status: solved
related:
  - src/folio_resolve/sources.py
  - src/folio_resolve/pipeline.py
  - tests/test_sources.py
  - docs/migration/2026-08-05-v0.3.1-consumer-impact.md
  - commit ddf1a89 "fix(sources): require a real ISBN before classifying a unit as metadata"
---

# A too-loose regex in a classifier that *suppresses* work is worse than one that over-matches loudly

## What happened

`folio_resolve.sources.classify_source` decides what kind of thing a text unit is, and
`MatchPipeline.match` refuses to tag anything the policy calls non-taggable
(`src/folio_resolve/pipeline.py:193`, `src/folio_resolve/sources.py:25`). One of the
classification rules was a heuristic: *a short unit that is mostly an ISBN / publisher block reads
as metadata* (`sources.py:56-58`).

The ISBN pattern was:

```python
_ISBN_RE = re.compile(r"\b(?:97[89][- ]?)?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?[\dxX]\b")
```

Every quantifier has a lower bound of 1, so the whole thing matches on **four digits**, and the
optional `[- ]?` separators let those digits be split. Anything under 200 characters containing a
run of ≥4 digits was therefore "an ISBN":

| text | old regex | new regex |
|---|---|---|
| `The 2024 amendments to Rule 26` | **match** | no match |
| `Actions under 42 U.S.C. 1983` | **match** | no match |
| `Riehl v. Duckler, 62-CV-26-2379` | **match** | no match |
| `See Smith v. Jones, 512 F.3d 1234 (8th Cir. 2008)` | **match** | no match |
| `pp. 1200-1250` | **match** | no match |
| `Effective January 1, 2026` | **match** | no match |
| `ISBN 978-0-306-40615-7` | match | match |
| `0306406152` | match | match |

(Reproduce with the two patterns side by side; the fix is `(?:\d[- ]?){9}[\dxX]` — a real ISBN-10
or ISBN-13 digit count, `sources.py:41`.)

## Why this class of bug is the dangerous one

A regex that **over-matches in a producer** — an extractor, a linkifier, a highlighter — produces
visible junk. Someone sees a wrong span and files a bug in an afternoon.

This regex over-matched in a **classifier whose output suppresses downstream work**. The
consequence was not a wrong tag. It was **no tag, no error, no log line, no changed exit code**:

```
pre-fix : match("Civil Rights Claims under 42 U.S.C. 1983", section_label="Chapter 5") -> []
post-fix: match("Civil Rights Claims under 42 U.S.C. 1983", section_label="Chapter 5") -> ["R-civil"]
```

The `[]` is indistinguishable from *"we looked and found nothing"*. In a book/treatise ingest, the
observable symptom is a slightly lower tag yield on a corpus nobody has ground truth for — which
reads as *the matcher is not very good*, not as *the matcher was never asked*. That misattribution
is the real cost: it sends people to tune scoring thresholds on units the scorer never saw.

And the misclassified population is not random. It is exactly **years, statute sections, docket
numbers, reporter citations, and page ranges** — the most legally load-bearing sentences in the
corpus. A permissive numeric pattern in a legal-text pipeline is *adversarially* correlated with
the content you most want tagged.

**This is not hypothetical.** In folio-insights — the one consumer that calls this classifier — the
most recent committed tagging run (`output/uat_ta_ch04_v8/extraction.json`, 1,168 units) shows the
metadata gate firing five times, and **all five were false positives**: `Fed. R. Evid. 702-705`,
`Fed. R. Evid. 901-902`, `FRE 1002`, `Rule 1002`, `Evid. 1001-1007`. The chapter is titled *Evidence
and Objections*. The gate's entire output on that run was wrong, and it was wrong in precisely the
chapter whose substance is rule citations.

There was a second, quieter effect. The same predicate is used **inverted** to decide what feeds the
corpus domain prior (`folio_tagger.py:374-387`), so every falsely-metadata unit had its concepts
harvested into the prior that is threaded into the LLM judge for *every other unit in the corpus*.
A committed evidence pack records the result: a `domain_prior.active_subjects` list containing
`"objection"`, `"evidence"`, `"Expert opinion"`, `"non"`, `"rule"` — body vocabulary, not front
matter. And a **workaround for the symptom had already been written without finding the cause**:
`folio_tagger.py:394-397` filters out "single-occurrence one-word ruler fragments ('non', 'rule')",
describing them as "surface accidents of front-matter text." They were not front-matter accidents.
**A downstream noise filter whose comment explains the noise as coming from a source that shouldn't
produce it is a strong signal that an upstream classifier is lying.**

## The general rule

**Ask which way a heuristic fails, and make the permissive side the loud side.**

- A pattern that **enables** work (recall, extraction, candidate generation) can be permissive.
  Its failure mode is noise, and noise is visible.
- A pattern that **suppresses** work (exclusion, veto, gating, filtering, "skip this") must be
  strict, because its failure mode is absence, and absence is invisible.
- When a heuristic gates a whole unit out of a pipeline, its *name* should assert something
  falsifiable. `_ISBN_RE` claims "this is an ISBN." A pattern that matches `2024` does not get to
  make that claim. **If the constant's name is a noun, the pattern must actually recognise that
  noun** — digit count is what makes an ISBN test an ISBN test.

## How to catch it

1. **Test the negative space, not just the positive.** The pre-fix module had no test file at all;
   the fix added `tests/test_sources.py` (148 lines), and the assertions that matter most are the
   ones naming things that must **not** classify as metadata — a year, `§ 1983`, a docket, an
   `F.3d` citation, a dollar amount. For any suppressing classifier, write the "must stay
   eligible" table *first*; it is the table that fails when the pattern loosens.
2. **Count what you suppressed.** Any stage that can return `[]` for policy reasons should be able
   to report *how often* and *why*. A single counter (`units_excluded_by_source_type`) turns a
   silent drop into a number a human will eventually look at. `[]` with no provenance is the bug's
   camouflage.
3. **Bound every quantifier from below.** `\d{1,5}` in a pattern meant to recognise a
   fixed-width identifier is a smell on sight. Identifiers have lengths; encode them. Prefer
   `(?:\d[- ]?){9}[\dxX]` (nine digits plus check) over four optional groups whose minimum nobody
   computed.
4. **Compute the minimum match by hand and write it in the comment.** The reason this survived
   review is that `\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?[\dxX]` *looks* like an ISBN — it has the
   right shape, the right groups, the right optional 978 prefix. Nobody added up `1+1+1+1`. The
   fixed version carries that arithmetic in a comment (`sources.py:36-40`) so the next reader
   inherits it.
5. **Diff a classifier against a real corpus, not against unit tests.** Running old-vs-new
   classification over the actual text units in fixtures and corpora turns "this pattern looks
   loose" into "N units change class, here are ten of them." That is the artifact that makes the
   blast radius arguable.
6. **Check that the guard test can actually reach the rule it guards.** folio-insights *did* have a
   regression test over this exact behavior — `tests/test_folio_resolve_pin.py:143-157`, asserting
   that `ISBN 978-0-13-468599-1` under section `["Front Matter", "Copyright"]` is not taggable and
   that ordinary prose is. It passes identically under the buggy and the fixed regex, because the
   `"copyright"` section-label marker short-circuits at `sources.py:50-52` and the ISBN regex is
   **never evaluated**. A test that exercises an earlier branch of a precedence chain proves nothing
   about the later ones. When a classifier is a cascade, every test must state *which rule* it is
   pinning, and at least one test per rule must be constructed so that only that rule can decide it.
7. **Suspect any veto that predates its tests.** `sources.py` was written to satisfy a specific
   review finding (*"source is metadata — should never have been considered"*, `sources.py:3-6`).
   Code written to *stop* something is written in a mood of "be safe," which biases toward
   over-matching, and it is rarely the module someone volunteers to test.

## Consumer fallout

The recovered-unit behavior change is a **correction**, but it is still a change: consumers that
pass a non-empty `section_label` to `MatchPipeline.match` will now get tags where they previously
got `[]`. Full inventory, per-consumer blast radius, and the golden files that need regeneration:
[`docs/migration/2026-08-05-v0.3.1-consumer-impact.md`](../migration/2026-08-05-v0.3.1-consumer-impact.md).
