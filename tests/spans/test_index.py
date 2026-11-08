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


def test_function_words_empty_and_nonmatches():
    idx = index("of", "the", "of the", "good faith")
    assert idx.lookup("of the") == []
    assert idx.lookup("") == []
    assert idx.lookup("other text") == []
    assert idx.lookup("good. faith") == []
