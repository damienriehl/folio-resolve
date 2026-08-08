"""Stable, synthetic benchmark for multi-strategy recall.

This benchmark deliberately uses no private evaluation data.  Its fixed public-query
snapshot guards exact recalled IRIs and scores while timing the production provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from folio_resolve import FolioPythonProvider, MultiStrategyRecall

EXPECTED_DIGEST = "20127799283863c7a5a4a718be39b7e6e453a15b92d85e5f0b8ebc1ee13b10b2"
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


Snapshot = list[list[dict[str, object]]]


def _snapshot(engine: MultiStrategyRecall) -> Snapshot:
    return [[asdict(result) for result in engine.recall(query)] for query in QUERIES]


def _digest(snapshot: Snapshot) -> str:
    payload = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def benchmark_payload(
    first: Snapshot,
    second: Snapshot,
    *,
    elapsed_seconds: float,
    ontology_search_calls: int,
) -> dict[str, int | float]:
    """Build the stable aggregate benchmark contract from measured snapshots."""
    return {
        "elapsed_seconds": round(elapsed_seconds, 6),
        "output_equivalent": int(_digest(first) == EXPECTED_DIGEST),
        "deterministic": int(first == second),
        "query_count": len(QUERIES),
        "ontology_search_calls": ontology_search_calls,
    }


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

    print(
        json.dumps(
            benchmark_payload(
                first,
                second,
                elapsed_seconds=elapsed,
                ontology_search_calls=first_call_count,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
