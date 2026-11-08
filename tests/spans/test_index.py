import pytest

from folio_resolve.spans.index import NGramIndex


def index(*keys):
    result = NGramIndex[str]()
    for key in keys:
        result.add(key, key)
    return result


def test_all_partial_overlaps_and_offsets_are_retained():
    idx = index("real property", "property tax", "property")
    text = "real property tax"
    hits = idx.lookup(text)
    assert [(h.start, h.end, h.payloads) for h in hits] == [
        (0, 13, ("real property",)),
        (5, 17, ("property tax",)),
        (5, 13, ("property",)),
    ]
    assert [text[h.start : h.end] for h in hits] == ["real property", "property tax", "property"]
    assert hits == idx.lookup(text)


def test_hyphens_and_whitespace_are_equivalent_in_both_directions():
    assert index("fully indexed rate").lookup("fully-indexed rate")[0].end == 18
    assert index("fully-indexed rate").lookup("fully indexed rate")[0].end == 18
    assert index("fully indexed rate").lookup("fully\uff0dindexed rate")[0].end == 18


def test_plural_is_opt_in_noun_only_and_has_fixed_try_order():
    idx = index("tenant", "tax", "taxe", "party", "partie")

    def noun(payload):
        return True

    assert idx.lookup("Tenants", noun=noun)[0].match_method == "plural"
    assert idx.lookup("tenant", noun=noun)[0].match_method == "exact"
    assert idx.lookup("taxes", noun=noun)[0].payloads == ("tax",)
    assert idx.lookup("parties", noun=noun)[0].payloads == ("party",)
    assert idx.lookup("tenants") == []
    assert idx.lookup("tenants", noun=lambda payload: False) == []
    assert (
        index("good tenants", "good tenant").lookup("good tenants", noun=noun)[0].match_method
        == "exact"
    )


def test_apostrophes_case_and_regions():
    text = "Tenant's claim. TENANT\u2019S claim."
    idx = index("tenant's claim")
    hits = idx.lookup(text, regions=[(15, len(text))])
    assert len(hits) == 1
    assert text[hits[0].start : hits[0].end] == "TENANT\u2019S claim"
    assert idx.lookup(text, regions=[]) == []
    assert idx.lookup(text, regions=[(17, len(text))]) == []


@pytest.mark.parametrize(
    "regions",
    [
        None,
        [],
        [(0, 19), (6, 30)],  # Overlapping, without a single region covering the union.
        [(0, 36), (6, 11), (12, 19)],  # Nested ends are not monotone.
        [(20, 36), (6, 19), (0, 11)],  # Unsorted.
        [(6, 19), (0, 11), (6, 19), (0, 11)],  # Duplicated.
        [(0, 11), (11, 30), (30, 36)],  # Adjacent regions cannot jointly contain a span.
    ],
)
def test_region_filter_matches_brute_force_containment(regions):
    text = "alpha bravo charlie delta echo foxtrot"
    words = text.split()
    idx = index(
        *(
            " ".join(words[start:end])
            for start in range(len(words))
            for end in range(start + 1, len(words) + 1)
        )
    )
    unrestricted = idx.lookup(text)
    expected = [
        hit
        for hit in unrestricted
        if regions is None
        or any(start <= hit.start and hit.end <= end for start, end in regions)
    ]
    # Also exercise one-shot iterables: preprocessing must consume them only once.
    hits = idx.lookup(text, regions=None if regions is None else iter(regions))
    assert hits == expected
    if regions == [(0, 11), (11, 30), (30, 36)]:
        assert any((hit.start, hit.end) == (0, 11) for hit in hits)
        assert not any((hit.start, hit.end) == (6, 19) for hit in hits)


def test_function_words_empty_and_nonmatches():
    idx = index("of", "the", "of the", "good faith")
    assert idx.lookup("of the") == []
    assert idx.lookup("") == []
    assert idx.lookup("other text") == []
    assert idx.lookup("good. faith") == []


def test_ruler_stopwords_remain_excluded_with_content_evidence():
    idx = index("the", "the the")
    assert idx.lookup("the the", content_word=lambda word: True) == []


@pytest.mark.parametrize(
    "label", ["AT&T", "attorney/client privilege", "U.S. citizen", "Wills, Trusts and Estates"]
)
def test_internal_punctuation_matches_its_own_surface(label):
    hits = index(label).lookup(label)
    assert [(hit.start, hit.end, hit.payloads) for hit in hits] == [(0, len(label), (label,))]


@pytest.mark.parametrize("gap", ["&", "/", ".", ","])
def test_punctuation_gaps_remain_distinct_from_spaces_and_each_other(gap):
    idx = index(f"tenant{gap}rent")
    assert idx.lookup(f"tenant {gap} rent")[0].payloads == (f"tenant{gap}rent",)
    assert idx.lookup("tenant rent") == []
    assert index("tenant rent").lookup(f"tenant{gap}rent") == []


def test_sentence_boundary_does_not_join_a_phrase():
    assert index("tenant rent").lookup("the tenant. Rent is due") == []


def test_punctuation_walk_is_bounded_by_longest_indexed_key():
    idx = index("tenant/rent/tax")
    assert idx.max_length == 3
    hits = idx.lookup("tenant/rent/tax/tenant/rent/tax")
    assert [(hit.start, hit.end) for hit in hits] == [(0, 15), (16, 31)]
    assert index("tenant/rent/tax").lookup("tenant/rent.tax") == []


@pytest.mark.parametrize(("surface", "labels"), [("½", ("1", "2")), ("㏂", ("a", "m"))])
def test_partial_source_expansions_are_not_matches(surface, labels):
    assert index(*labels).lookup(surface, content_word=lambda word: True) == []


def test_complete_fraction_expansion_survives_competing_partial_keys():
    idx = index("½", "1", "2", "½ interest", "1 interest", "2 interest")
    assert [hit.payloads for hit in idx.lookup("½ interest")] == [("½ interest",), ("½",)]
