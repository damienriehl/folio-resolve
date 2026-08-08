"""Cross-hash-seed determinism guard.

This library's whole value is that consumers get identical results from identical input, and
several of them commit golden/snapshot baselines that rot silently when order drifts. Two
PYTHONHASHSEED bugs were fixed on 2026-08-05 (``reconciler`` iterating ``set(a) | set(b)``;
``scoring.generate_search_terms`` sorting a set by length alone) and a third — float
accumulation over a set in :func:`word_overlap` — was found by the sweep this module
institutionalizes.

Each test runs the same computation in **subprocesses under different PYTHONHASHSEED values**
and asserts byte-identical output. In-process assertions cannot catch this class at all: within
one process the seed is fixed, so a set's iteration order is stable and the bug is invisible.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Seeds the guard compares. 0 and 1 are the pair that actually split the recorded findings;
# the larger values broaden the sample cheaply.
GUARD_SEEDS = ("0", "1", "2", "12345")


def _run(source: str, seed: str) -> str:
    """Run ``source`` in a subprocess under ``PYTHONHASHSEED=seed`` and return its stdout."""
    # Inherit the caller's environment (PYTHONPATH, venv wiring) and override only the seed.
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"probe failed under seed {seed}:\n{proc.stderr}"
    return proc.stdout


def _assert_seed_invariant(source: str, seeds: tuple[str, ...] = GUARD_SEEDS) -> str:
    outputs = {seed: _run(source, seed) for seed in seeds}
    distinct = set(outputs.values())
    assert len(distinct) == 1, (
        "output varies with PYTHONHASHSEED:\n"
        + "\n".join(f"  seed={s}: {o[:400]}" for s, o in sorted(outputs.items()))
    )
    return next(iter(distinct))


# -- the recorded word_overlap finding -----------------------------------
#
# `_directional_overlap` accumulated `matched += best` while iterating a *set*. Float addition
# is not associative, so the returned overlap depended on the seed. Both fixtures below are
# real: the first uses the DEFAULT (use_vectors=False) path, where the per-word credits are
# 0.8 (prefix match) and 0.7 (4+ char common prefix) and four of them are enough to split the
# sum; the second uses the injected-vectors path, where arbitrary credits split it sooner.

_DEFAULT_PATH_PROBE = """
from folio_resolve.scoring import content_words, word_overlap
q = content_words("contract defense settlement pleading")
t = content_words("contracts defendant settle pleadings")
print(repr(word_overlap(q, t)))
"""

_VECTOR_PATH_PROBE = """
from folio_resolve.scoring import content_words, word_overlap
def sim(a, b):
    return ((len(a) * 7919 + len(b) * 104729 + ord(a[0]) * 31 + ord(b[0])) % 1000) / 3000.0
q = content_words("rules of arbitration")
t = content_words("Northern Mariana Islands")
print(repr(word_overlap(q, t, use_vectors=True, word_similarity=sim)))
"""


def test_word_overlap_default_path_is_hash_seed_independent() -> None:
    """Pre-fix this returned 0.7750000000000001 under seed 0 and 0.7749999999999999 under 1."""
    _assert_seed_invariant(_DEFAULT_PATH_PROBE)


def test_word_overlap_vector_path_is_hash_seed_independent() -> None:
    """Pre-fix this returned 0.21750000000000003 under seed 0 and 0.21749999999999997 under 1."""
    _assert_seed_invariant(_VECTOR_PATH_PROBE)


# -- the durable guard ---------------------------------------------------
#
# Exercises the public entry points end to end so a *new* order dependence anywhere in the
# library — a set fed into a returned list, a truncation under a limit, a tie-break, a chosen
# "best" candidate — fails here rather than in a consumer's committed snapshot. Deliberately
# tie-heavy: several concepts score identically, so only the tie-break can order them.

_PUBLIC_SURFACE_PROBE = '''
import json
from folio_resolve import (
    AliasBlocklist, BlockedAlias, CalibrationSample, Concept, ConceptMatch, DomainPrior,
    DomainPriorSuggester, FOLIOEntityRuler, InMemoryOntology, LabelResolver, MatchPipeline,
    PlaceNameGate, Reconciler, ScoreCalibration, ShortLabelGate, augment_labels,
    compute_label_lemmas, compute_relevance_score, content_words, decompose,
    generate_search_terms, parse_judge_json, tokenize, word_overlap,
)
from folio_resolve.annotate import Annotation, ConceptTag, Span, render_segments
from folio_resolve.embedding import BruteForceIndex, HashingEmbeddingProvider

QUERIES = [
    "Commercial Litigation", "rules of arbitration",
    "Proposed Findings of Fact and Conclusions of Law", "Antitrust and Securities Law",
    "contract defense settlement pleading", "Presumptions", "law", "Action",
    "personal injury deposition practice and procedure",
    "corporate governance shareholder derivative litigation strategy",
]
LABELS = [
    "Arbitration Rules", "Commercial Litigation Practice", "Litigation Defenses",
    "Proposed Findings of Fact", "Proposed Conclusions of Law", "Antitrust Law",
    "Securities Law", "contracts defendant settle pleadings", "Personal Injury",
    "Deposition", "Presumption of Innocence", "Northern Mariana Islands", "Delaware",
    "Auction", "Litigation Burdens of Proof", "Shareholder Derivative Action", "Agreements",
]
# tie-heavy: identical label shapes so every score comes out exactly equal
LABELS += ["Arbitration Rules " + chr(65 + i) for i in range(8)]

out = {}
out["tokenize"] = {q: tokenize(q) for q in QUERIES}
out["content_words"] = {q: sorted(content_words(q)) for q in QUERIES}
out["search_terms"] = {q: generate_search_terms(q) for q in QUERIES}
out["decompose"] = {q: decompose(q) for q in QUERIES}
out["overlap"] = {q + "|" + l: repr(word_overlap(content_words(q), content_words(l)))
                  for q in QUERIES for l in LABELS}
out["score"] = {q + "|" + l + "|" + str(p): compute_relevance_score(
                    content_words(q), q, l,
                    definition="About " + l + " in practice.",
                    synonyms=[l + "s", l.replace(" ", "-")],
                    preferred_label=l.title(), specificity_penalty=p)
                for q in QUERIES for l in LABELS for p in (0.0, 0.5, 1.0)}

CONCEPTS = [Concept(iri="R-%03d" % i, label=l,
                    definition="Definition of " + l + " covering practice and claims.",
                    alternative_labels=(l + "s", l.lower().replace(" ", "-")),
                    preferred_label=(l.title() if i % 3 else None),
                    branch=["Objectives", "Service", "Area of Law", "Location",
                            "Governmental Body"][i % 5])
            for i, l in enumerate(LABELS)]
onto = InMemoryOntology(CONCEPTS)
out["all_labels"] = list(onto.all_labels())
out["search_by_label"] = {q + "|" + str(k): [(c.iri, s) for c, s in onto.search_by_label(q, limit=k)]
                          for q in QUERIES for k in (3, 8, 40)}

ruler = FOLIOEntityRuler()
ruler.load_patterns(onto.all_labels())
out["ruler"] = {t: [(m.start_char, m.end_char, m.entity_id, m.match_type)
                    for m in ruler.find_matches(t)]
                for t in ["Arbitration Rules A and Arbitration Rules B and Litigation Defenses.",
                          "Delaware, Auction, Action and the Shareholder Derivative Action."]}

labels = onto.all_labels()
lm = compute_label_lemmas(labels, lemmatize=lambda ws: [w[:-1] if w.endswith("s") else w for w in ws])
out["lemmas"] = lm
out["augmented"] = list(augment_labels(labels, lemma_map=lm))

idx = BruteForceIndex(HashingEmbeddingProvider(dim=64))
idx.build([c.iri for c in CONCEPTS], [c.label for c in CONCEPTS],
          [c.definition for c in CONCEPTS])
out["semantic"] = {q: idx.query(q, top_k=6) for q in QUERIES}

pipe = MatchPipeline(
    ontology=onto, entity_ruler=ruler, semantic_index=idx,
    blocklist=AliasBlocklist([BlockedAlias("action", "R-013", None, "Action != Auction")]),
    place_gate=PlaceNameGate(min_signals=2, extra_tokens={"macedonia"},
                             extra_markers=("city of",)),
    short_gate=ShortLabelGate(), calibration=ScoreCalibration())
prior = DomainPrior.from_manifest_subjects("t", [("R-002", "Litigation"), ("R-008", "Injury")])
out["pipeline"] = {q: [(c.iri, c.label, c.score, c.extraction_path, c.surface_term, c.gated)
                       for c in pipe.match(q, domain_prior=prior,
                                           heading_terms={"delaware", "auction"})]
                   for q in QUERIES}
out["best_match"] = {q: (pipe.best_match(q).iri if pipe.best_match(q) else None) for q in QUERIES}

res = LabelResolver(search_by_label=lambda t: onto.search_by_label(t, limit=8))
out["resolve"] = {q: [(r.iri, r.label, r.branch, r.score, r.surface) for r in res.resolve(q)]
                  for q in QUERIES}

rc = [ConceptMatch("arbitration", "R-000", "Arbitration Rules", "d", 0.72, "Service", "ruler"),
      ConceptMatch("defenses", "", "Litigation Defenses", "d", 0.55, "Objectives", "ruler"),
      ConceptMatch("action", "R-013", "Auction", "d", 0.9, "Objectives", "ruler"),
      ConceptMatch("law", "R-012", "Delaware", "d", 0.65, "Location", "ruler")]
lc = [ConceptMatch("arbitration", "R-000", "Arbitration Rules", "d", 0.8, "Service", "llm"),
      ConceptMatch("defenses", "R-002", "Litigation Defenses", "d", 0.7, "Objectives", "llm"),
      ConceptMatch("action", "R-015", "Shareholder Derivative Action", "d", 0.85, "Objectives", "llm")]
import copy
rows = lambda rs: [(r.concept.concept_text, r.concept.folio_iri, round(r.concept.confidence, 6),
                    r.concept.source, r.category) for r in rs]
out["reconcile"] = rows(Reconciler().reconcile(copy.deepcopy(rc), copy.deepcopy(lc)))
out["reconcile_triage"] = rows(
    Reconciler(similarity_batch=idx.similarity_batch, index_size=idx.num_concepts)
    .reconcile_with_embedding_triage(copy.deepcopy(rc), copy.deepcopy(lc)))

out["suggest"] = [(t.iri, t.confidence) for t in
                  DomainPriorSuggester(onto, max_suggestions=8, min_score=40.0).suggest(
                      title="Personal Injury Deposition Practice",
                      headings=["Arbitration Rules", "Litigation Defenses"])]

cal = ScoreCalibration.fit([CalibrationSample(float(s), v) for s, v in
                            [(45, "wrong"), (52, "weak"), (60, "correct"), (61, "wrong"),
                             (70, "weak"), (88, "correct"), (90, "wrong"), (92, "correct")]])
out["calibration"] = [cal._steps, [(s, cal.band(float(s))) for s in range(40, 100, 5)],
                      cal.weak_band_bounds()]

out["judge"] = [(j.iri, j.adjusted_score, j.verdict) for j in parse_judge_json(
    json.dumps({"judged": [{"iri_hash": "R-000", "adjusted_score": 95, "verdict": "boosted"},
                           {"iri_hash": "R-002", "adjusted_score": 10, "verdict": "penalized"}]}),
    {"R-000": 60.0, "R-002": 80.0})]

anns = [Annotation(id="an%02d" % i, span=Span(start=st, end=en, text="x"),
                   concepts=[ConceptTag(iri=iri, label=iri, confidence=0.5)])
        for i, (st, en, iri) in enumerate(
            [(0, 30, "A"), (0, 30, "B"), (5, 25, "C"), (5, 25, "D"), (10, 20, "E")])]
out["render"] = [(s.start, s.end, s.annotation_ids) for s in render_segments("x" * 40, anns)]

print(json.dumps(out, sort_keys=False, default=str))
'''


def test_public_entry_points_are_identical_across_hash_seeds() -> None:
    """The durable guard: every public entry point must be byte-identical across seeds.

    A new order dependence anywhere — a set fed into a returned list, a truncation under a
    limit, an unstable tie-break, a differently chosen "best" candidate — fails here instead
    of silently rotting a consumer's committed golden capture.
    """
    digest = _assert_seed_invariant(_PUBLIC_SURFACE_PROBE)
    assert digest.strip(), "the probe produced no output"
