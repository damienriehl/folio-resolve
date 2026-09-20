"""Frozen public-corpus diagnostic; run one variant per fresh Linux process.

Corpus policy: direct named owl:Class entries, merged by IRI and sorted by IRI.
Prefer English, then untagged, then other language labels (lexical tie-break).
Keep all aliases and named parents. Missing labels fall back to IRI; no branch
inference is performed. All named FOLIO classes remain candidates; OWL built-ins are excluded.
Semantic indexing uses the library's label + definition text, without aliases.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import resource
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path

from folio_resolve import Concept, InMemoryOntology, MatchPipeline
from folio_resolve.embedding import (
    BruteForceIndex,
    HashingEmbeddingProvider,
    LocalEmbeddingProvider,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures/embedding_recall.json"
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}
CORPUS_POLICY = "direct-named-folio-owl-classes-merged-by-iri; en-untagged-other-labels; iri-fallback; all-aliases; no-branch-inference; sorted-iri; label-definition-index-v1"


def stable_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def corpus_digest(concepts: list[Concept]) -> str:
    return stable_digest([asdict(c) for c in concepts])


def load_fixtures(path: Path = FIXTURE_PATH) -> dict:
    fixture = json.loads(path.read_text())
    if fixture["schema_version"] != 1 or not fixture["cases"]:
        raise ValueError("Unsupported or empty fixture")
    ids = [c["id"] for c in fixture["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate fixture IDs")
    for case in fixture["cases"]:
        if not case["query"] or (case["kind"] == "negative") != (not case["acceptable_iris"]):
            raise ValueError("Invalid query or negative-control answer set")
    return fixture


def load_corpus(path: Path, expected_sha256: str) -> list[Concept]:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Ontology SHA-256 differs from frozen fixture")
    grouped: dict[str, list] = {}
    for element in ET.fromstring(data).findall("owl:Class", NS):
        iri = element.get(f"{{{NS['rdf']}}}about")
        if iri and iri.startswith("https://folio.openlegalstandard.org/"):
            grouped.setdefault(iri, []).append(element)

    def values(elements: list, field: str) -> list[str]:
        entries = set()
        for element in elements:
            for child in element.findall(field, NS):
                value = (child.text or "").strip()
                if value:
                    language = child.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                    priority = (
                        0 if language.lower().split("-")[0] == "en" else 1 if not language else 2
                    )
                    entries.add((priority, language, value))
        return list(dict.fromkeys(value for _, _, value in sorted(entries)))

    concepts = []
    for iri, elements in sorted(grouped.items()):
        labels = values(elements, "rdfs:label")
        preferred = values(elements, "skos:prefLabel")
        definitions = values(elements, "skos:definition")
        label = next(iter(labels or preferred), iri)
        aliases = sorted(set(values(elements, "skos:altLabel") + labels + preferred) - {label})
        parents = sorted(
            {
                parent.get(f"{{{NS['rdf']}}}resource")
                for element in elements
                for parent in element.findall("rdfs:subClassOf", NS)
                if parent.get(f"{{{NS['rdf']}}}resource")
            }
        )
        concepts.append(
            Concept(
                iri=iri,
                label=label,
                definition="\n".join(definitions) or None,
                preferred_label=next(iter(preferred), None),
                alternative_labels=tuple(aliases),
                parent_iris=tuple(parents),
            )
        )
    if not concepts:
        raise ValueError("Public corpus contains no named classes")
    return concepts


def validate_answers(fixture: dict, concepts: list[Concept]) -> None:
    by_iri = {c.iri: c for c in concepts}
    for case in fixture["cases"]:
        for iri in case["acceptable_iris"]:
            if iri not in by_iri:
                raise ValueError(f"Missing fixture target: {case['id']} {iri}")
            concept = by_iri[iri]
            if concept.label != case["expected_labels"][iri]:
                raise ValueError(f"Target label drift: {case['id']}")
            if case["kind"] == "paraphrase":
                target = " ".join(
                    [
                        concept.label,
                        concept.preferred_label or "",
                        *concept.alternative_labels,
                        concept.definition or "",
                    ]
                )
                overlap = set(re.findall(r"\w+", target.casefold())) & set(
                    re.findall(r"\w+", case["query"].casefold())
                )
                if overlap:
                    raise ValueError(
                        f"Paraphrase literal token overlap: {case['id']} {sorted(overlap)}"
                    )


def build_pipeline(
    concepts: list[Concept], variant: str, model_path: Path | None = None, model: dict | None = None
) -> MatchPipeline:
    index = None
    if variant == "local":
        if (
            model is None
            or model_path is None
            or not model_path.is_dir()
            or model_path.name != model["revision"]
        ):
            raise ValueError("Local model requires the existing pinned snapshot directory")
        if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
            raise ValueError("Local model requires HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")
        provider = LocalEmbeddingProvider(str(model_path))
        if provider.dimension() != model["dimension"]:
            raise ValueError("Local model dimension differs from fixture pin")
        index = BruteForceIndex(provider)
    elif variant == "hashing":
        index = BruteForceIndex(HashingEmbeddingProvider())
    elif variant != "disabled":
        raise ValueError(f"Unknown variant: {variant}")
    if index is not None:
        index.build(
            [c.iri for c in concepts], [c.label for c in concepts], [c.definition for c in concepts]
        )
    return MatchPipeline(ontology=InMemoryOntology(concepts), semantic_index=index)


def recall_metrics(cases: list[dict], ranked: dict[str, list[str]]) -> dict:
    positive = [case for case in cases if case["acceptable_iris"]]
    result = {"positive_queries": len(positive), "negative_queries": len(cases) - len(positive)}
    for k in (1, 5):
        hits = sum(
            bool(set(case["acceptable_iris"]) & set(ranked[case["id"]][:k])) for case in positive
        )
        result[f"recall_at_{k}"] = hits / len(positive) if positive else None
    return result


def percentile(values: list[float], fraction: float) -> float:
    """Linear interpolation between adjacent sorted samples (inclusive endpoints)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def run_benchmark(owl: Path, variant: str, model_path: Path | None, repeats: int) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if platform.system() != "Linux":
        raise ValueError("Peak RSS measurement is defined for Linux only")
    fixture = load_fixtures()
    start = time.perf_counter()
    concepts = load_corpus(owl, fixture["ontology"]["sha256"])
    validate_answers(fixture, concepts)
    pipeline = build_pipeline(concepts, variant, model_path, fixture["model"])
    build_seconds = time.perf_counter() - start
    ranked = {}
    results = []
    all_times = []
    for case in fixture["cases"]:
        # The initial query is excluded from warm timing and retains all ranked survivors.
        candidates = pipeline.match(case["query"])
        ranked[case["id"]] = [c.iri for c in candidates]
        durations = []
        for _ in range(repeats):
            start = time.perf_counter()
            repeated = pipeline.match(case["query"])
            durations.append(time.perf_counter() - start)
            if [asdict(c) for c in repeated] != [asdict(c) for c in candidates]:
                raise ValueError(f"Nondeterministic rankings for {case['id']}")
        all_times.extend(durations)
        results.append(
            {**case, "candidates": [asdict(c) for c in candidates], "warm_seconds": durations}
        )
    peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    packages = {
        dist.metadata["Name"]: dist.version
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"]
    }
    git_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).parents[1],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    provenance = {
        "fixture_sha256": stable_digest(fixture),
        "ontology": fixture["ontology"],
        "corpus_sha256": corpus_digest(concepts),
        "corpus_policy": CORPUS_POLICY,
        "concept_count": len(concepts),
        "model": fixture["model"] if variant == "local" else None,
        "variant": variant,
        "hashing_dimension": 256 if variant == "hashing" else None,
        "pipeline": {
            "score_floor": pipeline.score_floor,
            "label_search_limit": pipeline.label_search_limit,
            "semantic_top_k": 5,
            "entity_ruler": None,
            "recall_engine": None,
            "judge": False,
            "context": None,
        },
        "git_revision": git_revision,
        "benchmark_source_sha256": file_digest(Path(__file__)),
        "library_source_sha256": stable_digest(
            {
                str(p.relative_to(Path(__file__).parents[1])): file_digest(p)
                for p in sorted((Path(__file__).parents[1] / "src/folio_resolve").rglob("*"))
                if p.is_file() and p.suffix in {".py", ".json"}
            }
        ),
        "model_files_sha256": {
            str(p.relative_to(model_path)): file_digest(p)
            for p in sorted(model_path.rglob("*"))
            if p.is_file()
        }
        if variant == "local" and model_path
        else None,
    }
    return {
        "schema_version": 1,
        "provenance": provenance,
        "configuration_sha256": stable_digest(provenance),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cpu_model": next(
                (
                    line.split(":", 1)[1].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                platform.machine(),
            ),
            "threads": {key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")},
        },
        "packages": dict(sorted(packages.items())),
        "metrics": recall_metrics(fixture["cases"], ranked),
        "runtime": {
            "build_seconds": build_seconds,
            "warm_repeats_per_query": repeats,
            "warm_query_p50_seconds": percentile(all_times, 0.5),
            "warm_query_p95_seconds": percentile(all_times, 0.95),
            "peak_process_rss_bytes": peak_rss_bytes,
            "timing_policy": "build includes parse/validation/model/index; one untimed warmup per query; pooled linear percentiles; RSS fresh process through queries before provenance hashing",
        },
        "positive_results": [r for r in results if r["acceptable_iris"]],
        "negative_controls": [r for r in results if not r["acceptable_iris"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owl", type=Path, required=True)
    parser.add_argument("--variant", choices=("disabled", "hashing", "local"), required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = run_benchmark(args.owl, args.variant, args.model_path, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {"variant": args.variant, "metrics": result["metrics"], "runtime": result["runtime"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
