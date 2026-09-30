"""Public scoring, packaged homonym data, and deterministic gates."""
from folio_resolve import (
    AliasBlocklist,
    PlaceNameGate,
    ShortLabelGate,
    compute_relevance_score,
    content_words,
    generate_search_terms,
)

for query, expected in [("arbitration rules", 99.0), ("rules of arbitration", 88.0)]:
    score = compute_relevance_score(content_words(query), query, "Arbitration Rules")
    assert score == expected, (query, score, expected)
assert generate_search_terms("litigation") == [
    "litigation", "litigation practice", "litigation service",
]
blocklist = AliasBlocklist.from_seed()
auction = "https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO"
assert blocklist.is_blocked("Action", auction), "packaged Action/Auction guard missing"
assert not blocklist.is_blocked("Auction", auction)
assert blocklist.filter_candidates("Action", [(auction, 92.3), ("R-action", 99.0)]) == [
    ("R-action", 99.0),
]
place = PlaceNameGate()
assert place.evaluate(query="law", label="Delaware", branch="Location", score=90).demoted
assert not place.evaluate(query="Delaware", label="Delaware", branch="Location", score=99).demoted
short = ShortLabelGate()
assert short.evaluate(query="action", label="Auction", score=92.3).demoted
assert not short.evaluate(query="Auction", label="Auction", score=99).demoted
print("99.0/88.0 scores; search expansions; Action/Auction seed; place and short-label gates passed")
