"""Isolated public-benchmark lexical policies; production scoring stays unchanged.

KTD1 intentionally keeps a small copy of the production scorer below. Shared
arithmetic helpers remain production imports. Differential tests constrain drift.
The substring policy reproduces production, including its empty-query definition
bonus; the boundary treatment alone excludes empty containment needles.
"""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import importlib.util
import json
import math
import platform
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Literal, TypedDict

from folio_resolve import InMemoryOntology, MatchPipeline
from folio_resolve.decompose import decompose
from folio_resolve.ontology import Concept
from folio_resolve.scoring import (
    SPECIFICITY_PENALTY_WEIGHT,
    WordSimilarity,
    _as_text,
    _no_vector_similarity,
    content_words,
    word_overlap,
)
from folio_resolve.scoring import (
    compute_relevance_score as baseline_relevance_score,
)

__all__ = ["ContainmentAward", "baseline_relevance_score", "boundary_relevance_score", "containment_start"]


class ContainmentAward(TypedDict):
    field: str
    text: str
    needle: str
    start: int
    end: int
    bonus: float


def _word_character(character: str) -> bool:
    return character == "_" or unicodedata.category(character)[0] in "LNM"


def containment_start(
    text: str, needle: str, policy: Literal["substring", "boundary"] = "boundary",
) -> int | None:
    """Find the first qualifying literal occurrence, including overlapping matches.

    Callers supply production-normalized strings. No tokenization, case folding,
    accent normalization, or internal whitespace rewriting happens here.
    """
    if policy == "substring":
        start = text.find(needle)
        return start if start >= 0 else None
    if policy != "boundary":
        raise ValueError(f"Unknown lexical policy: {policy}")
    if not needle:
        return None
    start = text.find(needle)
    while start >= 0:
        end = start + len(needle)
        if (start == 0 or not _word_character(text[start - 1])) and (
            end == len(text) or not _word_character(text[end])
        ):
            return start
        start = text.find(needle, start + 1)
    return None


def boundary_relevance_score(
    query_content: set[str] | None,
    query_full: str | None,
    label: str | None,
    definition: str | None = None,
    synonyms: list[str] | None = None,
    preferred_label: str | None = None,
    *,
    use_vectors: bool = False,
    word_similarity: WordSimilarity = _no_vector_similarity,
    specificity_penalty: float = 1.0,
    policy: Literal["substring", "boundary"] = "boundary",
    trace: list[ContainmentAward] | None = None,
) -> float:
    """Production arithmetic with only four containment predicates replaced.

    ``trace`` receives awards as the scorer executes those branches. Offsets refer
    to lowercased text (and stripped query), not original Unicode source offsets.
    Awards describe channel bonuses before overlap maxima and specificity penalties;
    they need not be the winning contribution to the final score.
    """
    if policy not in ("substring", "boundary"):
        raise ValueError(f"Unknown lexical policy: {policy}")

    def award(field: str, text: str, needle: str, start: int, bonus: float) -> None:
        if trace is not None:
            trace.append({
                "field": field, "text": text, "needle": needle,
                "start": start, "end": start + len(needle), "bonus": bonus,
            })

    label = _as_text(label)
    if not label:
        return 0.0
    query_full = _as_text(query_full)
    definition = _as_text(definition) or None
    preferred_label = _as_text(preferred_label) or None
    synonyms = [s for s in (synonyms or []) if isinstance(s, str)]
    query_content = {w for w in (query_content or set()) if isinstance(w, str)}

    query_lower = query_full.lower().strip()
    label_lower = label.lower()

    if query_lower == label_lower:
        return 99.0

    label_content = content_words(label)

    label_score = 0.0
    if len(query_lower) >= 4 and (start := containment_start(label_lower, query_lower, policy)) is not None:
        label_score = 92.0
        award("label", label_lower, query_lower, start, 92.0)
    elif (
        len(label_lower) >= 4
        and (start := containment_start(query_lower, label_lower, policy)) is not None
        and len(label_lower) / len(query_lower) > 0.3
    ):
        label_score = 88.0
        award("reverse_label", query_lower, label_lower, start, 88.0)
    overlap = word_overlap(
        query_content, label_content, use_vectors=use_vectors, word_similarity=word_similarity
    )
    if overlap > 0:
        label_score = max(label_score, overlap * 88)

    pref_score = 0.0
    if preferred_label:
        pref_lower = preferred_label.lower()
        if query_lower == pref_lower:
            pref_score = 90.0
        elif len(query_lower) >= 4 and (start := containment_start(pref_lower, query_lower, policy)) is not None:
            pref_score = 84.0
            award("preferred_label", pref_lower, query_lower, start, 84.0)
        else:
            pref_content = content_words(preferred_label)
            p_overlap = word_overlap(
                query_content, pref_content, use_vectors=use_vectors, word_similarity=word_similarity
            )
            if p_overlap > 0:
                pref_score = p_overlap * 86

    syn_score = 0.0
    for syn in synonyms:
        syn_content = content_words(syn)
        s_overlap = word_overlap(
            query_content, syn_content, use_vectors=use_vectors, word_similarity=word_similarity
        )
        if s_overlap > 0:
            syn_score = max(syn_score, s_overlap * 82)

    def_score = 0.0
    if definition:
        def_lower = definition.lower()
        if (start := containment_start(def_lower, query_lower, policy)) is not None:
            def_score = 60.0
            award("definition", def_lower, query_lower, start, 60.0)
        def_content = content_words(definition)
        d_overlap = word_overlap(query_content, def_content, word_similarity=word_similarity)
        if d_overlap > 0:
            def_score = max(def_score, d_overlap * 55)

    primary = max(label_score, pref_score, syn_score)
    final = primary + min(def_score * 0.12, 8) if primary > 0 else def_score

    # Specificity penalty: penalize candidates more specific than the query.
    if label_content and query_content and final > 0 and specificity_penalty > 0:
        extra_words = label_content - query_content
        if extra_words and len(label_content) > len(query_content):
            specificity_ratio = len(extra_words) / len(label_content)
            penalty = specificity_ratio * SPECIFICITY_PENALTY_WEIGHT * specificity_penalty
            final = final * (1.0 - min(max(penalty, 0.0), 1.0))

    return round(min(final, 99.0), 1)



_SPEC = importlib.util.spec_from_file_location(
    'embedding_precision_relations', Path(__file__).with_name('embedding_precision_relations.py')
)
assert _SPEC is not None and _SPEC.loader is not None
relations = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(relations)
precision = relations.precision
baseline = precision.baseline
ROOT = precision.ROOT
POLICIES = ('substring', 'boundary')
GATES = ('baseline', 'selective')
SCHEMA = 'lexical-boundaries/v1'
COLLECTION_PATH = ROOT / 'docs/benchmarks/embedding-precision-collection.json'
JUDGMENTS_PATH = ROOT / 'docs/benchmarks/embedding-precision-judgments.json'
OWNER_RECEIPT = '8141ed7277b9b98a3f9d82f5625378c78b0b69fc4c8c55ab0985f4765623330d'


def concept_score(query, concept, policy, trace=None):
    return boundary_relevance_score(
        content_words(query), query, concept.label, concept.definition,
        list(concept.alternative_labels), concept.preferred_label, policy=policy, trace=trace,
    )


class LexicalOntology(InMemoryOntology):
    """Score the complete corpus before deterministic sorting and provider limits."""

    def __init__(self, concepts, policy):
        super().__init__(concepts)
        if policy not in POLICIES:
            raise ValueError('Unknown lexical policy')
        self.policy = policy
        self.changes = []

    def search_by_label(self, query, *, limit=20):
        scored = []
        for concept in self._concepts:
            if self.policy == 'substring':
                # The actual production provider remains the control scorer.
                score = baseline_relevance_score(
                    content_words(query), query, concept.label, concept.definition,
                    list(concept.alternative_labels), concept.preferred_label,
                )
            else:
                old_awards, new_awards = [], []
                old = concept_score(query, concept, 'substring', old_awards)
                production = baseline_relevance_score(
                    content_words(query), query, concept.label, concept.definition,
                    list(concept.alternative_labels), concept.preferred_label,
                )
                if old != production:
                    raise ValueError(f'Substring scorer drift: {query!r} / {concept.iri}')
                score = concept_score(query, concept, 'boundary', new_awards)
                if old != score or old_awards != new_awards:
                    self.changes.append({
                        'surface_term': query, 'iri': concept.iri,
                        'substring': {'score': old, 'awards': old_awards},
                        'boundary': {'score': score, 'awards': new_awards},
                    })
            if score > 0:
                scored.append((concept, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0].iri))
        return scored[:limit]


class SavedSemanticResponse:
    """A query-specific replay, deliberately incapable of invoking the encoder."""

    def __init__(self, query, response):
        self.text = query
        self.response = copy.deepcopy(response)
        self.calls = 0

    def query(self, text, *, top_k):
        if text != self.text or top_k != 5:
            raise ValueError('Semantic replay query/window differs')
        self.calls += 1
        return copy.deepcopy(self.response)


def load_frozen_inputs():
    collection = json.loads(COLLECTION_PATH.read_text())
    sheet = json.loads(JUDGMENTS_PATH.read_text())
    precision.validate_collection(collection)
    relations.validate_judgments(collection, sheet, OWNER_RECEIPT)
    return collection, sheet


def collect_cases(concepts, semantic_index, fixture, controls):
    """Collect all controls first, then treatment, sharing one encoder call/query."""
    results = []
    for case in fixture['cases']:
        response = [list(row) for row in semantic_index.query(case['query'], top_k=5)]
        saved = SavedSemanticResponse(case['query'], response)
        pipeline = MatchPipeline(LexicalOntology(concepts, 'substring'), semantic_index=saved)
        inputs = precision.retrieve_once(pipeline, case['query'])
        if saved.calls != 1:
            raise ValueError('Semantic replay call count differs')
        results.append({**case, 'semantic_response': response,
                        'substring': precision.rank_pair(pipeline, inputs, case['acceptable_iris'])})
    precision.verify_controls(
        [{**{key: row[key] for key in fixture['cases'][i]}, **row['substring']}
         for i, row in enumerate(results)], controls,
    )
    for case in results:
        ontology = LexicalOntology(concepts, 'boundary')
        saved = SavedSemanticResponse(case['query'], case['semantic_response'])
        pipeline = MatchPipeline(ontology, semantic_index=saved)
        inputs = precision.retrieve_once(pipeline, case['query'])
        if saved.calls != 1:
            raise ValueError('Semantic replay call count differs')
        case['boundary'] = precision.rank_pair(pipeline, inputs, case['acceptable_iris'])
        case['containment_changes'] = ontology.changes
    return results


def source_hashes():
    return {
        **precision.source_hashes(),
        str(Path(relations.__file__).relative_to(ROOT)): baseline.file_digest(Path(relations.__file__)),
        str(Path(__file__).relative_to(ROOT)): baseline.file_digest(Path(__file__)),
    }


def expected_provenance(frozen, sheet):
    return {
        **{key: frozen['provenance'][key] for key in (
            'library_source_sha256', 'corpus_sha256', 'corpus_policy', 'concept_count',
            'ontology', 'model', 'model_files_sha256', 'pipeline', 'ranking_context', 'pool_depth',
        )},
        'measurement': 'full corpus lexical reretrieval; one pinned semantic response per query; four fresh rankings',
        'source_sha256': source_hashes(),
        'fixture_sha256': baseline.stable_digest(frozen['fixture']),
        'approval_payload_sha256': frozen['fixture']['approval']['payload_sha256'],
        'frozen_collection_sha256': baseline.stable_digest(frozen),
        'frozen_judgments_sha256': baseline.stable_digest(sheet),
        'owner_receipt_sha256': OWNER_RECEIPT,
        'lexical_policies': list(POLICIES), 'gate_policies': list(GATES),
        'trace_policy': 'all changed score or containment-award records, including below-cut candidates',
    }


def pool_from_results(results, concepts):
    combined = []
    for result in results:
        for policy in POLICIES:
            combined.append({**{key: result[key] for key in ('id', 'query')}, **result[policy]})
    # Historical helper rejects duplicate query IDs, so invoke per query/policy then union.
    pairs = {}
    for row in combined:
        for pair in precision.candidate_pool([row], concepts):
            pairs[pair['query_id'], pair['iri']] = pair
    return [pairs[row['id'], iri] for row in results
            for iri in sorted(iri for qid, iri in pairs if qid == row['id'])]


def validate_metadata_transfer(pool, frozen_pool):
    old = {(row['query_id'], row['iri']): row for row in frozen_pool}
    for row in pool:
        key = row['query_id'], row['iri']
        if key in old and row != old[key]:
            raise ValueError(f'Judgment transfer concept metadata differs: {key}')


def run_collect(owl, model_path):
    frozen, sheet = load_frozen_inputs()
    fixture = precision.load_fixtures()
    concepts = baseline.load_corpus(owl, fixture['ontology']['sha256'])
    model_files = baseline.verify_model_files(model_path, fixture['model'])
    prior = json.loads((ROOT / 'docs/benchmarks/embedding-baseline-local.json').read_text())
    precision.verify_pins(fixture, concepts, prior, model_files)
    # Verify every historical pool concept before carrying any annotation forward.
    old_pool = precision.candidate_pool(frozen['results'], concepts)
    if old_pool != frozen['pool']:
        raise ValueError('Frozen pool current corpus metadata differs')
    pipeline = baseline.build_pipeline(concepts, 'local', model_path, fixture['model'])
    results = collect_cases(concepts, pipeline.semantic_index, fixture, frozen['results'])
    pool = pool_from_results(results, concepts)
    validate_metadata_transfer(pool, frozen['pool'])
    iris = {p['iri'] for p in pool}
    for row in results:
        iris.update(c['iri'] for policy in POLICIES
                    for c in row[policy]['baseline']['candidate_inputs'])
        iris.update(c['iri'] for c in row['containment_changes'])
    metadata = [asdict(c) for c in sorted(concepts, key=lambda c: c.iri) if c.iri in iris]
    # JSON normalization avoids tuple/list differences on offline reproduction.
    metadata = json.loads(json.dumps(metadata))
    provenance = expected_provenance(frozen, sheet)
    runtime = {'python': platform.python_version(), 'platform': platform.platform(),
               'packages': dict(sorted((d.metadata['Name'], d.version)
                                      for d in importlib.metadata.distributions() if d.metadata['Name']))}
    collection = {
        'schema_version': SCHEMA, 'fixture': fixture, 'provenance': provenance,
        'configuration_sha256': baseline.stable_digest(provenance),
        'runtime': runtime, 'runtime_sha256': baseline.stable_digest(runtime),
        'results': results, 'pool': pool, 'pool_sha256': baseline.stable_digest(pool),
        'concept_metadata': metadata, 'concept_metadata_sha256': baseline.stable_digest(metadata),
        'controls_reproduced': True,
    }
    validate_collection(collection)
    return collection


def validate_collection(collection):
    """Validate this new envelope, then replay ranking without model/corpus inference.

    Corpus completeness is attested by runtime pins and collector source; offline
    replay checks saved lexical scores, semantic streams, metadata, and all ranks.
    """
    frozen, sheet = load_frozen_inputs()
    expected_keys = {'schema_version', 'fixture', 'provenance', 'configuration_sha256',
                     'runtime', 'runtime_sha256', 'results', 'pool', 'pool_sha256',
                     'concept_metadata', 'concept_metadata_sha256', 'controls_reproduced'}
    if set(collection) != expected_keys or collection['schema_version'] != SCHEMA:
        raise ValueError('Lexical collection schema differs')
    if collection['fixture'] != frozen['fixture']:
        raise ValueError('Lexical fixture identity differs')
    expected = expected_provenance(frozen, sheet)
    if set(collection['provenance']) != set(expected):
        raise ValueError('Lexical provenance schema differs')
    for key in expected:
        if collection['provenance'].get(key) != expected.get(key):
            raise ValueError(f'Lexical provenance {key} differs')
    for name, value in (('configuration', collection['provenance']),
                        ('runtime', collection['runtime']), ('pool', collection['pool']),
                        ('concept_metadata', collection['concept_metadata'])):
        if collection[f'{name}_sha256'] != baseline.stable_digest(value):
            raise ValueError(f'Lexical {name} digest differs')
    runtime = collection['runtime']
    if set(runtime) != {'python', 'platform', 'packages'} or not all(
        isinstance(runtime[k], str) and runtime[k] for k in ('python', 'platform')
    ) or not isinstance(runtime['packages'], dict) or not runtime['packages'] or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in runtime['packages'].items()
    ):
        raise ValueError('Runtime dependency evidence differs')
    metadata = collection['concept_metadata']
    concept_keys = {'iri', 'label', 'definition', 'alternative_labels', 'preferred_label', 'branch', 'parent_iris'}
    if any(set(c) != concept_keys for c in metadata):
        raise ValueError('Concept metadata schema differs')
    concepts = [Concept(**c) for c in metadata]
    by_iri = {c.iri: c for c in concepts}
    if len(by_iri) != len(concepts) or list(by_iri) != sorted(by_iri):
        raise ValueError('Concept metadata identity/order differs')
    results = collection['results']
    fixture = collection['fixture']
    if len(results) != len(fixture['cases']):
        raise ValueError('Lexical query count differs')
    controls = []
    required_iris = set()
    for row, case in zip(results, fixture['cases'], strict=True):
        if set(row) != set(case) | {'semantic_response', *POLICIES, 'containment_changes'} or any(
            row[k] != v for k, v in case.items()
        ):
            raise ValueError('Lexical query identity/schema differs')
        response = row['semantic_response']
        if len(response) != 5 or any(
            not isinstance(c, list) or len(c) != 3 or not isinstance(c[2], (int, float))
            or not math.isfinite(c[2]) or not -1.000001 <= c[2] <= 1.000001
            or c[0] not in by_iri or c[1] != by_iri[c[0]].label for c in response
        ) or len({c[0] for c in response}) != len(response):
            raise ValueError('Semantic response schema/identity differs')
        semantic_inputs = None
        for policy in POLICIES:
            if set(row[policy]) != set(GATES):
                raise ValueError('Lexical gate cells differ')
            inputs = row[policy]['baseline']['candidate_inputs']
            if row[policy]['selective']['candidate_inputs'] != inputs:
                raise ValueError('Lexical gate retrieval inputs differ')
            ranked = precision.rank_pair(MatchPipeline(InMemoryOntology([])), inputs, case['acceptable_iris'])
            if ranked != row[policy]:
                raise ValueError('Lexical ranked snapshot differs from saved inputs')
            semantics = [c for c in inputs if c['extraction_path'] == 'semantic']
            expected_semantics = []
            from folio_resolve import MatchCandidate
            for iri, label, cosine in response:
                expected_semantics.append(asdict(MatchCandidate(
                    iri=iri, label=label, score=round(cosine * 100, 1),
                    branch=by_iri[iri].branch, extraction_path='semantic', surface_term=case['query'],
                )))
            if semantics != expected_semantics or (semantic_inputs is not None and semantics != semantic_inputs):
                raise ValueError('Shared semantic response/stream differs')
            semantic_inputs = semantics
            allowed = decompose(case['query'])
            for c in inputs:
                required_iris.add(c['iri'])
                concept = by_iri.get(c['iri'])
                if concept is None or c['label'] != concept.label or c['branch'] != concept.branch:
                    raise ValueError('Retrieval concept metadata differs')
                if c['extraction_path'] != 'semantic':
                    valid_path = c['extraction_path'] == 'label_search' and c['surface_term'] == case['query']
                    valid_path |= c['extraction_path'] == 'decomposition' and c['surface_term'] in allowed[1:]
                    if not valid_path or c['score'] != concept_score(c['surface_term'], concept, policy):
                        raise ValueError('Lexical retrieval score/path differs')
            # Re-run the real retrieval over saved metadata: verifies ordering, limits,
            # decomposition, and all known replacement candidates (not unseen corpus).
            pipe = MatchPipeline(LexicalOntology(concepts, policy),
                                 semantic_index=SavedSemanticResponse(case['query'], response))
            if precision.retrieve_once(pipe, case['query']) != inputs:
                raise ValueError('Lexical saved retrieval replay differs')
        controls.append({**case, **row['substring']})
        seen = set()
        for change in row['containment_changes']:
            if set(change) != {'surface_term', 'iri', *POLICIES}:
                raise ValueError('Containment trace schema differs')
            key = change['surface_term'], change['iri']
            if key in seen or key[0] not in decompose(case['query']) or key[1] not in by_iri:
                raise ValueError('Containment trace identity differs')
            seen.add(key)
            required_iris.add(key[1])
            for policy in POLICIES:
                awards = []
                score = concept_score(key[0], by_iri[key[1]], policy, awards)
                if change[policy] != {'score': score, 'awards': awards}:
                    raise ValueError('Containment trace scorer replay differs')
            if change['substring'] == change['boundary']:
                raise ValueError('Unchanged containment trace')
        # Every affected saved concept must have a trace, even below the ranking cut.
        expected_changes = LexicalOntology(concepts, 'boundary')
        for term in decompose(case['query']):
            expected_changes.search_by_label(term)
        if row['containment_changes'] != expected_changes.changes:
            raise ValueError('Containment trace coverage/order differs')
    if collection['controls_reproduced'] is not True:
        raise ValueError('Lexical controls not reproduced')
    precision.verify_controls(controls, frozen['results'])
    if collection['pool'] != pool_from_results(results, concepts):
        raise ValueError('Lexical pool membership/metadata differs')
    validate_metadata_transfer(collection['pool'], frozen['pool'])
    required_iris.update(p['iri'] for p in collection['pool'])
    if required_iris != set(by_iri):
        raise ValueError('Unexpected concept metadata membership')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    collect = sub.add_parser('collect')
    collect.add_argument('--owl', type=Path, required=True)
    collect.add_argument('--model-path', type=Path, required=True)
    collect.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run_collect(args.owl, args.model_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'queries': len(result['results']), 'controls_reproduced': True}))


if __name__ == '__main__':
    main()
