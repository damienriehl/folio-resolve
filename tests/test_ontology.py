"""Focused tests for ontology provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from folio_resolve.ontology import FolioPythonProvider


@dataclass
class _Owl:
    iri: str
    label: str


class _FolioSpy:
    def __init__(self, result_count: int, *, over_return_by: int = 0) -> None:
        self.result_count = result_count
        self.over_return_by = over_return_by
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search_by_label(self, label: str, **kwargs: Any) -> list[tuple[_Owl, float]]:
        self.calls.append((label, kwargs))
        requested_limit = int(kwargs.get("limit", 10))
        count = min(self.result_count, requested_limit + self.over_return_by)
        return [(_Owl(f"iri:{index}", f"Concept {index}"), 100.0) for index in range(count)]


def test_folio_provider_forwards_requested_limit() -> None:
    folio = _FolioSpy(50)

    FolioPythonProvider(folio).search_by_label("contract", limit=27)

    assert folio.calls == [("contract", {"limit": 27})]


def test_folio_provider_can_return_more_than_upstream_default() -> None:
    folio = _FolioSpy(25)

    results = FolioPythonProvider(folio).search_by_label("contract", limit=20)

    assert len(results) == 20


def test_folio_provider_truncates_upstream_over_return() -> None:
    folio = _FolioSpy(25, over_return_by=5)

    results = FolioPythonProvider(folio).search_by_label("contract", limit=12)

    assert len(results) == 12
