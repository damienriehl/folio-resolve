"""Collect Codex verifier decisions; --limit checkpoints a smoke run without publishing."""
from __future__ import annotations

import argparse
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.leakcheck import load_manifest
from folio_eval.resolve_labels import folio_cache_file
from folio_eval.synthesize import load_corpus
from folio_eval.verifier import load_collection
from folio_eval.verifier_collect import CodexRunner, Runner, collect, write_collection

ROOT = Path(__file__).resolve().parent.parent


def load_concepts(path: Path, expected_sha256: str) -> dict[str, tuple[str, str]]:
    """Read the pinned local OWL directly, without a loader that might fetch remotely."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('ontology cache hash differs from corpus manifest')
    rdf = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
    skos = '{http://www.w3.org/2004/02/skos/core#}'
    rdfs = '{http://www.w3.org/2000/01/rdf-schema#}'
    result = {}
    for node in ET.fromstring(raw).iter():
        iri = node.get(rdf + 'about')
        if iri:
            # Match folio_resolve.ontology._owl_to_concept's display label.
            label = node.findtext(rdfs + 'label') or node.findtext(skos + 'prefLabel')
            definition = node.findtext(skos + 'definition') or ''
            if label:
                result[iri] = (label, definition)
    return result


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shortlists', type=Path, required=True, help='U1 baseline U2 collection')
    parser.add_argument('--model', required=True,
                        help='Requested model; recorded as requested when CLI omits served identity')
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--salt-file', type=Path, required=True)
    parser.add_argument('--corpus-manifest', type=Path,
                        default=ROOT / 'eval/synthetic/corpus_v1.manifest.json')
    parser.add_argument('--surface-manifest', type=Path,
                        default=ROOT / 'eval/synthetic/firm-surface-manifest-v1.json')
    parser.add_argument('--ontology-cache', type=Path, default=folio_cache_file())
    parser.add_argument('--template', type=Path,
                        default=ROOT / 'eval/synthetic/verifier/prompt_template_v1.md')
    parser.add_argument('--out', type=Path,
                        default=ROOT / 'eval/synthetic/verifier/codex-collection-v1.json')
    parser.add_argument('--limit', type=int, help='Maximum new items; resume without this flag to finish')
    parser.add_argument('--jobs', type=int, choices=range(1, 9), default=1,
                        help='Concurrent items (1-8; default 1)')
    args = parser.parse_args(argv)
    corpus = load_corpus(args.corpus_manifest)
    baseline = load_collection(args.shortlists, corpus)
    concepts = load_concepts(args.ontology_cache, corpus.manifest.ontology_cache_sha256)
    manifest = load_manifest(args.surface_manifest)
    salt = args.salt_file.read_bytes()
    if not salt:
        raise ValueError('salt must not be empty')
    result = collect(
        baseline, corpus, concepts, args.template.read_bytes().decode('utf-8'),
        runner if runner is not None else CodexRunner(args.model),
        checkpoint=args.checkpoint, runner_identity=args.model, limit=args.limit, jobs=args.jobs,
    )
    if result is None:
        print('Smoke limit reached; checkpoints saved; no collection published.')
        return 0
    write_collection(args.out, result, corpus, manifest, salt)
    print(f'Wrote {len(result.decisions)} decisions to {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
