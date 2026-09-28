"""Explicit opt-in smoke test for the pinned, already-downloaded local model."""

import os
from pathlib import Path

import pytest
from folio_eval.recall_embedding_ceiling import load_local_provider, measure_ceiling

from folio_resolve.ontology import Concept

pytestmark = pytest.mark.embedding_integration


def test_pinned_model_embeds_passage() -> None:
    configured = os.environ.get("FOLIO_RESOLVE_EMBEDDING_MODEL_PATH")
    if not configured:
        pytest.fail("Set FOLIO_RESOLVE_EMBEDDING_MODEL_PATH to the pinned snapshot")
    provider, count = load_local_provider(Path(configured))
    result = measure_ceiling(
        [Concept("test:counsel", "Legal counsel", "A lawyer representing clients in court.")],
        {"p": "The attorney represented the defendant in court."},
        {
            "schema_version": 1,
            "relations": [{"item_id": "p", "iri": "test:counsel", "stage": "never_produced"}],
        },
        provider,
        count,
    )
    assert result["rankings"]["whole_passage"]["10"]["recovered_count"] == 1
