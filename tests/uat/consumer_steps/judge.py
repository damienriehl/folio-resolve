"""Provider-neutral fake transport, malformed output and absent transport."""
from folio_resolve import Concept, InMemoryOntology, MatchPipeline, parse_judge_json

ontology = InMemoryOntology([Concept(iri="R-arb", label="Arbitration Rules", branch="Service")])
calls = []

class FakeJudge:
    def complete(self, system, user):
        calls.append((system, user))
        return '{"judged": [{"iri_hash": "R-arb", "adjusted_score": 97, "verdict": "confirmed"}]}'

matches = MatchPipeline(ontology=ontology, judge=FakeJudge()).match("rules of arbitration", run_judge=True)
assert len(calls) == 1 and matches[0].iri == "R-arb"
# A confirmed verdict may move the original 88 score by at most five points.
assert matches[0].score == 93.0, matches[0]
raw = '''```json
{"judged": [
 {"iri_hash": "bad", "adjusted_score": "high", "verdict": "confirmed"},
 {"iri_hash": "over", "adjusted_score": 150, "verdict": "penalized"},
 {"iri_hash": "rejected", "adjusted_score": 75, "verdict": "rejected"}
]}
```'''
parsed = {r.iri: r.adjusted_score for r in parse_judge_json(raw, {"bad": 80., "over": 80., "rejected": 80.})}
assert parsed == {"over": 100.0, "rejected": 0.0}, parsed
for malformed in (None, "not json", "[]", '{"judged": null}'):
    assert parse_judge_json(malformed, {}) == []
plain = MatchPipeline(ontology=ontology)
assert plain.match("rules of arbitration", run_judge=True) == plain.match("rules of arbitration", run_judge=False)
print("fake judge called, score applied, malformed output handled, no-judge path unchanged")
