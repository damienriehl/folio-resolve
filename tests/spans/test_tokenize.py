from folio_resolve.spans.tokenize import normalize, tokenize


def test_case_apostrophes_nfkc_and_original_offsets():
    text = (
        "The \uff34\uff25\uff2e\uff21\uff2e\uff34\u2019S fully-indexed rate; Straße café cafe\u0301"
    )
    tokens = tokenize(text)
    assert [t.key for t in tokens] == [
        "the",
        "tenant's",
        "fully",
        "indexed",
        "rate",
        "strasse",
        "café",
        "café",
    ]
    assert [text[t.start : t.end] for t in tokens] == [
        "The",
        "\uff34\uff25\uff2e\uff21\uff2e\uff34\u2019S",
        "fully",
        "indexed",
        "rate",
        "Straße",
        "café",
        "cafe\u0301",
    ]
    assert normalize("Fully\u2010indexed\tRATE") == ("fully", " ", "indexed", " ", "rate")


def test_empty_tokenization():
    assert tokenize("") == []
    assert normalize("...") == ()


def test_nfkc_expansions_preserve_normalized_gaps():
    assert (
        normalize("½ interest")
        == normalize("1\u20442 interest")
        == ("1", "\u2044", "2", " ", "interest")
    )
    assert normalize("½ interest") != normalize("1 2 interest")
    assert normalize("㏂ hearing") == normalize("a.m. hearing") == ("a", ".", "m", ".", "hearing")


def test_nfkc_composes_across_characters_with_original_offsets():
    # Hangul Jamo and Bengali vowel signs compose even though their combining class is zero.
    text = "\u1100\u1161 \u0995\u09c7\u09be"
    assert normalize(text) == ("\uac00", " ", "\u0995\u09cb")
    assert [(t.start, t.end) for t in tokenize(text)] == [(0, 2), (3, 6)]
    assert normalize("\u3131\u314f") == ("\uac00",)
