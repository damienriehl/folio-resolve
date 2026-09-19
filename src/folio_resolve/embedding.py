"""Embedding index for the semantic recall path.

Lifted from folio-mapper ``embedding/folio_index.py`` (FAISS ``IndexFlatIP`` over
``label: definition`` texts, disk-cached, ``all-MiniLM-L6-v2`` local provider). Here the provider
is a ``Protocol`` and the FAISS index is optional: the pure-Python :class:`HashingEmbeddingProvider`
+ :class:`BruteForceIndex` give a deterministic, dependency-free semantic path for tests and for
consumers that cannot install faiss; :class:`LocalEmbeddingProvider` swaps in the real
``all-MiniLM-L6-v2`` model when the ``embedding`` extra is installed (a FAISS-backed index over
the same provider is the v1 optimization for large corpora).

Semantic recall is mandatory for Ch02's "no shared label token" maps (Presumptions -> Burdens of
Proof, law -> Legal Authorities) — label matching alone can never find them.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from numbers import Real
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[a-zA-Z]+")


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    def dimension(self) -> int:
        ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _validate_vector(vector: Sequence[float], dimension: int, operation: str) -> list[float]:
    if len(vector) != dimension:
        raise ValueError(f"{operation}: vector width must be {dimension}, got {len(vector)}")
    # Copy validated values so a provider reusing its output buffers cannot alter index state.
    out: list[float] = []
    for component in vector:
        if isinstance(component, bool) or not isinstance(component, Real):
            raise ValueError(f"{operation}: vector components must be finite real numbers")
        try:
            value = float(component)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{operation}: vector components must be finite real numbers") from exc
        if not math.isfinite(value):
            raise ValueError(f"{operation}: vector components must be finite real numbers")
        out.append(value)
    return out


class HashingEmbeddingProvider:
    """Deterministic hashing embedding — no model, no network.

    Hashes content-word tokens into a fixed-dimension bag-of-tokens vector. Good enough to make
    the semantic path exercisable and deterministic in tests; not a substitute for a real model
    in production (install the ``embedding`` extra and use :class:`LocalEmbeddingProvider`).
    """

    def __init__(self, dim: int = 256) -> None:
        if dim < 1:
            raise ValueError(f"dim must be >= 1, got {dim}")
        self._dim = dim

    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for tok in _TOKEN_RE.findall(text.lower()):
            if len(tok) < 2:
                continue
            # usedforsecurity=False: this is a bucket hash, not a digest. Without it the call
            # raises on FIPS-mode hosts, which would take out the dependency-free semantic path.
            h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[h % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class BruteForceIndex:
    """Cosine-similarity index over embedded concepts. Pure Python; small-corpus friendly."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._iris: list[str] = []
        self._labels: list[str] = []
        self._vectors: list[list[float]] = []
        self._dimension: int | None = None

    def _provider_dimension(self, operation: str, *, check_built: bool = False) -> int:
        dimension = self._provider.dimension()
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise ValueError(f"{operation}: provider dimension must be a positive integer")
        if check_built and self._dimension is not None and dimension != self._dimension:
            raise ValueError(f"{operation}: provider dimension differs from the built index")
        return dimension

    @property
    def num_concepts(self) -> int:
        return len(self._iris)

    def build(self, iris: Sequence[str], labels: Sequence[str], definitions: Sequence[str | None]) -> None:
        # `iris` was previously unchecked against the other two, so a mismatched build succeeded
        # and blew up later inside query()/score_candidates() with a bare "zip() argument 2 is
        # shorter than argument 1" — far from the call that caused it.
        if not len(iris) == len(labels) == len(definitions):
            raise ValueError(
                f"iris/labels/definitions must be parallel: got {len(iris)}/{len(labels)}/{len(definitions)}"
            )
        texts = [f"{lbl}: {dfn}" if dfn else lbl for lbl, dfn in zip(labels, definitions, strict=True)]
        dimension = self._provider_dimension("build")
        new_iris, new_labels = list(iris), list(labels)
        vectors = self._provider.embed_batch(texts)
        if len(vectors) != len(new_iris):
            raise ValueError(f"build: vector count must be {len(new_iris)}, got {len(vectors)}")
        new_vectors = [_validate_vector(vector, dimension, "build") for vector in vectors]
        self._iris, self._labels, self._vectors, self._dimension = (
            new_iris,
            new_labels,
            new_vectors,
            dimension,
        )

    def query(self, text: str, *, top_k: int = 20) -> list[tuple[str, str, float]]:
        if not self._vectors:
            return []
        dimension = self._provider_dimension("query", check_built=True)
        q = _validate_vector(self._provider.embed(text), dimension, "query")
        scored = [
            (iri, lbl, _cosine(q, vec))
            for iri, lbl, vec in zip(self._iris, self._labels, self._vectors, strict=True)
        ]
        scored.sort(key=lambda t: t[2], reverse=True)
        return scored[:top_k]

    def score_candidates(self, text: str, candidate_iris: Sequence[str]) -> dict[str, float]:
        dimension = self._provider_dimension("score_candidates", check_built=True)
        q = _validate_vector(self._provider.embed(text), dimension, "score_candidates")
        by_iri = dict(zip(self._iris, self._vectors, strict=True))
        return {iri: _cosine(q, by_iri[iri]) for iri in candidate_iris if iri in by_iri}

    def similarity_batch(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Cosine for each ``(text_a, text_b)`` pair — used by the reconciler triage."""
        out: list[float] = []
        for a, b in pairs:
            dimension = self._provider_dimension("similarity_batch")
            va = _validate_vector(self._provider.embed(a), dimension, "similarity_batch")
            vb = _validate_vector(self._provider.embed(b), dimension, "similarity_batch")
            out.append(_cosine(va, vb))
        return out


class LocalEmbeddingProvider:
    """Optional ``sentence-transformers`` provider (``all-MiniLM-L6-v2``, 384-dim).

    Install the ``embedding`` extra. Import is deferred so the core stays dependency-light.
    """

    _DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name or self._DEFAULT_MODEL
        self._model = SentenceTransformer(self._model_name)

    def dimension(self) -> int:
        dimension = self._model.get_sentence_embedding_dimension()
        if dimension is None:
            raise RuntimeError(
                f"sentence-transformers model {self._model_name!r} does not report an embedding dimension"
            )
        return int(dimension)

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._model.encode(text, normalize_embeddings=True)]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self._model.encode(list(texts), normalize_embeddings=True, batch_size=64)
        return [[float(x) for x in v] for v in vecs]
