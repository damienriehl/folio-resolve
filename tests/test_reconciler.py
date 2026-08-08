"""Reconciler — agreement boost, ruler-only threshold, embedding triage."""

from __future__ import annotations

from folio_resolve import ConceptMatch, Reconciler


def _cm(text: str, iri: str = "", conf: float = 0.7, **kw: object) -> ConceptMatch:
    return ConceptMatch(concept_text=text, folio_iri=iri, confidence=conf, **kw)  # type: ignore[arg-type]


def test_both_agree_gets_boost() -> None:
    r = Reconciler()
    results = r.reconcile([_cm("witness", "R1", 0.7)], [_cm("witness", "R1", 0.6)])
    assert len(results) == 1
    assert results[0].category == "both_agree"
    assert results[0].concept.confidence > 0.7


def test_ruler_only_below_threshold_filtered() -> None:
    r = Reconciler()
    results = r.reconcile([_cm("vague", "R9", 0.35)], [])
    assert results == []


def test_ruler_only_above_threshold_kept() -> None:
    r = Reconciler()
    results = r.reconcile([_cm("deposition", "R2", 0.72)], [])
    assert len(results) == 1
    assert results[0].category == "ruler_only"


def test_llm_only_kept() -> None:
    r = Reconciler()
    results = r.reconcile([], [_cm("presumption", "R3", 0.8)])
    assert results[0].category == "llm_only"


def test_result_order_is_deterministic() -> None:
    """Regression: pass 1 iterated `set(ruler_by_key) | set(llm_by_key)`.

    Set iteration order over string tuples varies with PYTHONHASHSEED, so the same input
    produced a differently ordered result list in different processes — enough to flake any
    consumer that takes results[0] or diffs a committed capture. Ruler insertion order is the
    contract now; this pins it against the (deliberately unsorted) input order.
    """
    names = ["gamma", "alpha", "epsilon", "beta", "delta"]
    ruler = [_cm(n, f"R-{n}", 0.8) for n in names]
    llm = [_cm(n, f"R-{n}", 0.7) for n in reversed(names)]
    for reconcile in ("reconcile", "reconcile_with_embedding_triage"):
        r = Reconciler(similarity_batch=lambda pairs: [0.9] * len(pairs), index_size=10)
        results = getattr(r, reconcile)(ruler, llm)
        assert [x.concept.concept_text for x in results] == names, reconcile


def test_agreement_boost_diminishes_as_confidence_rises() -> None:
    r = Reconciler()
    low = r.reconcile([_cm("a", "R1", 0.2)], [_cm("a", "R1", 0.2)])[0].concept.confidence
    high = r.reconcile([_cm("b", "R2", 0.9)], [_cm("b", "R2", 0.9)])[0].concept.confidence
    assert (low - 0.2) > (high - 0.9)  # the boost shrinks as there is less headroom
    assert high <= 1.0


def test_agreement_boosts_from_the_stronger_of_the_two_paths() -> None:
    r = Reconciler()
    concept = r.reconcile([_cm("a", "R1", 0.9)], [_cm("a", "R1", 0.3)])[0].concept
    assert concept.confidence > 0.9
    assert concept.source == "reconciled"


def test_an_iri_less_ruler_hit_cross_matches_the_llm_by_text() -> None:
    # The ruler found the span but could not resolve it; the LLM's IRI is adopted.
    r = Reconciler()
    results = r.reconcile([_cm("presumption", "", 0.7)], [_cm("presumption", "R-presume", 0.8)])
    assert len(results) == 1
    assert results[0].category == "both_agree"
    assert results[0].concept.folio_iri == "R-presume"


def test_an_iri_less_llm_hit_cross_matches_the_ruler_by_text() -> None:
    r = Reconciler()
    results = r.reconcile([_cm("presumption", "R-presume", 0.7)], [_cm("presumption", "", 0.8)])
    assert len(results) == 1
    assert results[0].concept.folio_iri == "R-presume"


def test_disagreeing_iris_are_kept_as_separate_findings_without_triage() -> None:
    # Plain reconcile has no way to adjudicate, so both survive with their own provenance.
    r = Reconciler()
    results = r.reconcile([_cm("charge", "R-A", 0.7)], [_cm("charge", "R-B", 0.7)])
    assert {(x.category, x.concept.folio_iri) for x in results} == {
        ("ruler_only", "R-A"),
        ("llm_only", "R-B"),
    }


def test_llm_only_hits_bypass_the_ruler_confidence_floor() -> None:
    # The ruler-only floor exists because bare label hits are noisy; the LLM already judged.
    r = Reconciler()
    assert r.reconcile([], [_cm("vague", "R9", 0.1)])[0].category == "llm_only"


def test_reconciling_nothing_yields_nothing() -> None:
    assert Reconciler().reconcile([], []) == []
    assert Reconciler(similarity_batch=lambda pairs: [], index_size=5).reconcile_with_embedding_triage(
        [], []
    ) == []


def test_triage_falls_back_to_plain_reconcile_without_an_index() -> None:
    calls: list[int] = []

    def sim(pairs: list[tuple[str, str]]) -> list[float]:
        calls.append(1)
        return [0.9] * len(pairs)

    ruler = [_cm("charge", "R-A", 0.7)]
    llm = [_cm("charge", "R-B", 0.7)]
    # No similarity function at all...
    plain = Reconciler().reconcile_with_embedding_triage(ruler, llm)
    assert {x.category for x in plain} == {"ruler_only", "llm_only"}
    # ...or a similarity function over an empty index.
    empty = Reconciler(similarity_batch=sim, index_size=0).reconcile_with_embedding_triage(ruler, llm)
    assert {x.category for x in empty} == {"ruler_only", "llm_only"}
    assert calls == []


def test_triage_falls_back_to_definition_overlap_below_the_similarity_threshold() -> None:
    # Both embeddings are weak, so the tie is broken by which definition shares the context.
    r = Reconciler(similarity_batch=lambda pairs: [0.1, 0.2], index_size=10)
    ruler = [_cm("charge", "R-A", 0.7, folio_label="Encumbrance", folio_definition="a lien on property")]
    llm = [_cm("charge", "R-B", 0.7, folio_label="Criminal Charge", folio_definition="a formal charge in a criminal case")]
    resolved = [x for x in r.reconcile_with_embedding_triage(ruler, llm) if x.category == "conflict_resolved"]
    assert len(resolved) == 1
    assert resolved[0].concept.folio_iri == "R-B"


def test_an_unbreakable_conflict_keeps_both_candidates() -> None:
    # Weak embeddings AND no definition signal: escalate both rather than guessing.
    r = Reconciler(similarity_batch=lambda pairs: [0.1, 0.1], index_size=10)
    ruler = [_cm("charge", "R-A", 0.7, folio_label="Encumbrance")]
    llm = [_cm("charge", "R-B", 0.7, folio_label="Criminal Charge")]
    resolved = [x for x in r.reconcile_with_embedding_triage(ruler, llm) if x.category == "conflict_resolved"]
    assert {x.concept.folio_iri for x in resolved} == {"R-A", "R-B"}


def test_triage_promotes_the_winner_confidence_to_its_similarity() -> None:
    r = Reconciler(similarity_batch=lambda pairs: [0.2, 0.99], index_size=10)
    ruler = [_cm("charge", "R-A", 0.7, folio_label="Encumbrance")]
    llm = [_cm("charge", "R-B", 0.5, folio_label="Criminal Charge")]
    winner = next(
        x for x in r.reconcile_with_embedding_triage(ruler, llm) if x.category == "conflict_resolved"
    )
    assert winner.concept.confidence == 0.99
    assert winner.concept.source == "reconciled"


def test_embedding_triage_resolves_iri_conflict() -> None:
    # ruler->R-A, llm->R-B for same text; injected similarity favors R-B.
    def sim_batch(pairs: list[tuple[str, str]]) -> list[float]:
        # pairs: [(text, ruler_label), (text, llm_label)] -> ruler low, llm high
        return [0.2, 0.95]

    r = Reconciler(similarity_batch=sim_batch, index_size=10)
    ruler = [_cm("charge", "R-A", 0.7, folio_label="Encumbrance")]
    llm = [_cm("charge", "R-B", 0.7, folio_label="Criminal Charge")]
    results = r.reconcile_with_embedding_triage(ruler, llm)
    resolved = [x for x in results if x.category == "conflict_resolved"]
    assert len(resolved) == 1
    assert resolved[0].concept.folio_iri == "R-B"


def test_triage_cross_matches_an_empty_iri_and_adopts_the_resolved_one() -> None:
    # One side found the span but could not resolve it: there is no conflict to triage,
    # so the side that HAS an IRI wins outright without consulting the embeddings.
    calls: list[int] = []

    def sim(pairs: list[tuple[str, str]]) -> list[float]:
        calls.append(1)
        return [0.9] * len(pairs)

    r = Reconciler(similarity_batch=sim, index_size=10)
    results = r.reconcile_with_embedding_triage(
        [_cm("presumption", "", 0.7)], [_cm("presumption", "R-presume", 0.6)]
    )
    assert [(x.category, x.concept.folio_iri) for x in results] == [("both_agree", "R-presume")]
    assert results[0].concept.confidence > 0.7  # boosted by the agreement
    assert calls == []


def test_triage_keeps_the_ruler_side_when_the_llm_has_no_iri() -> None:
    r = Reconciler(similarity_batch=lambda pairs: [0.9] * len(pairs), index_size=10)
    results = r.reconcile_with_embedding_triage(
        [_cm("presumption", "R-presume", 0.7)], [_cm("presumption", "", 0.6)]
    )
    assert [(x.category, x.concept.folio_iri) for x in results] == [("both_agree", "R-presume")]


def test_triage_still_emits_the_unmatched_remainder() -> None:
    r = Reconciler(similarity_batch=lambda pairs: [0.9] * len(pairs), index_size=10)
    results = r.reconcile_with_embedding_triage(
        [_cm("deposition", "R-depo", 0.8), _cm("vague", "R-vague", 0.3)],
        [_cm("presumption", "R-presume", 0.9)],
    )
    assert [(x.category, x.concept.folio_iri) for x in results] == [
        ("ruler_only", "R-depo"),
        ("llm_only", "R-presume"),
    ]  # the 0.3 ruler-only hit is below the confidence floor


def test_triage_definition_overlap_can_favor_the_ruler_side() -> None:
    r = Reconciler(similarity_batch=lambda pairs: [0.1, 0.2], index_size=10)
    ruler = [_cm("lien on property", "R-A", 0.7, folio_label="Encumbrance", folio_definition="a lien on property")]
    llm = [_cm("lien on property", "R-B", 0.7, folio_label="Criminal Charge", folio_definition="an accusation")]
    resolved = [x for x in r.reconcile_with_embedding_triage(ruler, llm) if x.category == "conflict_resolved"]
    assert [x.concept.folio_iri for x in resolved] == ["R-A"]
