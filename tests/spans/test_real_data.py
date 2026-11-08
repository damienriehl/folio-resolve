"""Opt-in characterization of the full, pinned OpenGloss lexicon."""

import os
from pathlib import Path

import pytest

from folio_resolve.spans import SpanTagger, load_lexicon
from folio_resolve.spans.lexicon import DATASET_ID, REVISION

ARTIFACT = os.environ.get("FOLIO_RESOLVE_OPENGLOSS_LEXICON")
pytestmark = pytest.mark.skipif(not ARTIFACT, reason="set FOLIO_RESOLVE_OPENGLOSS_LEXICON")


@pytest.fixture(scope="module")
def real_tagger():
    artifact = Path(ARTIFACT).expanduser()
    manifest = artifact.with_name(artifact.name.removesuffix(".jsonl.gz") + ".manifest.json")
    lexicon = load_lexicon(artifact, manifest)
    assert lexicon.manifest.dataset_id == DATASET_ID
    assert lexicon.manifest.revision == REVISION
    assert lexicon.manifest.headwords is None, "real-data coverage requires the full lexicon"
    return SpanTagger(lexicon=lexicon)


def test_good_faith_ranks_above_its_words(real_tagger):
    result = real_tagger.tag("The tenant acted in good faith.")
    spans = {span.text: span for span in result.spans}
    phrase = spans["good faith"]
    assert phrase.opengloss
    assert all(a.rank == "primary" and a.outranked_by is None for a in phrase.opengloss)
    for word in ("good", "faith"):
        assert spans[word].opengloss
        assert all(
            a.rank == "fallback" and a.outranked_by == phrase.id for a in spans[word].opengloss
        )
        assert spans[word].id in phrase.contains
        assert phrase.id in spans[word].contained_by


def test_property_senses_are_present(real_tagger):
    # Presence characterizes the spike's observation, without claiming legal coverage.
    result = real_tagger.tag("property")
    spans = [span for span in result.spans if span.text == "property"]
    assert len(spans) == 1
    assert spans[0].opengloss
    assert all(a.id and a.headword == "property" for a in spans[0].opengloss)


@pytest.mark.parametrize(("surface", "headwords"), [("½", {"1", "2"}), ("㏂", {"a", "m"})])
def test_partial_unicode_expansions_have_no_word_anchors(real_tagger, surface, headwords):
    assert not any(
        anchor.headword in headwords
        for span in real_tagger.tag(surface).spans
        for anchor in span.opengloss
    )
