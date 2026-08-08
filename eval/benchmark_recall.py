"""Stable, synthetic benchmark for multi-strategy recall.

This benchmark deliberately uses no private evaluation data.  Its fixed public-query
snapshot guards exact recalled IRIs and scores while timing the production provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from folio_resolve import FolioPythonProvider, MultiStrategyRecall

EXPECTED_DIGEST = "441d45d0007cb98cd60d652bbdd4d01bb23d178aff95d780eb2ca2f4fca0933e"
QUERIES = (
    "contract damages",
    "contract claim",
    "employment discrimination",
    "employment law",
    "real property transfer",
    "property claim",
    "tax assessment appeal",
    "tax law",
)


@dataclass
class _CountingOntology:
    ontology: FolioPythonProvider
    search_calls: int = 0

    def _count(self) -> None:
        self.search_calls += 1

    def all_labels(self) -> Any:
        return self.ontology.all_labels()

    def search_by_label(self, query: str, *, limit: int = 20) -> Any:
        self._count()
        return self.ontology.search_by_label(query, limit=limit)

    def get_concept(self, iri: str) -> Any:
        return self.ontology.get_concept(iri)

    def search_by_prefix(self, prefix: str, *, limit: int = 50) -> Any:
        self._count()
        return self.ontology.search_by_prefix(prefix, limit=limit)

    def search_by_definition(self, query: str, *, limit: int = 20) -> Any:
        self._count()
        return self.ontology.search_by_definition(query, limit=limit)

    def parents_of(self, iri: str) -> Any:
        return self.ontology.parents_of(iri)


def _snapshot(engine: MultiStrategyRecall) -> list[list[tuple[str, float]]]:
    return [
        [(result.concept.iri, result.score) for result in engine.recall(query)]
        for query in QUERIES
    ]


def _digest(snapshot: list[list[tuple[str, float]]]) -> str:
    payload = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    ontology = _CountingOntology(FolioPythonProvider())
    engine = MultiStrategyRecall(ontology)
    # The private gate reuses one provider for the full dataset, so ontology construction is
    # startup cost rather than per-batch recall latency.  Warm it outside the timed region.
    ontology.ontology._get()

    started = time.perf_counter()
    first = _snapshot(engine)
    elapsed = time.perf_counter() - started
    first_call_count = ontology.search_calls
    second = _snapshot(engine)
    digest = _digest(first)

    print(
        json.dumps(
            {
                "median_seconds": round(elapsed, 6),
                "output_equivalent": int(digest == EXPECTED_DIGEST),
                "deterministic": int(first == second),
                "query_count": len(QUERIES),
                "ontology_search_calls": first_call_count,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
