"""Self-match coverage must expose suppressed content senses as regressions."""

import runpy
from pathlib import Path

from folio_resolve import InMemoryOntology
from folio_resolve.spans import SpanTagger, load_lexicon

ROOT = Path(__file__).resolve().parents[2]
CHECK_SELF_MATCHES = runpy.run_path(str(ROOT / "benchmarks/span_tagging_real_data.py"))[
    "check_headword_self_matches"
]


def test_benchmark_does_not_explain_missing_will_as_function_word_exclusion():
    fixtures = Path(__file__).parent / "fixtures"
    lexicon = load_lexicon(
        fixtures / "opengloss-mini.jsonl.gz", fixtures / "opengloss-mini.manifest.json"
    )
    # A tagger with no labels models omitted anchors independently of exclusion classification.
    missing = CHECK_SELF_MATCHES(SpanTagger(InMemoryOntology([]), folio_release="empty"), lexicon)
    assert "will" in missing["unmatched_examples_by_reason"]["unexpected"]
    assert "will" not in missing["unmatched_examples_by_reason"]["function_word_only"]
    actual = CHECK_SELF_MATCHES(SpanTagger(lexicon=lexicon), lexicon)
    assert actual["unexpected_unmatched_count"] == 0
    assert actual["matched_count"] > 0
