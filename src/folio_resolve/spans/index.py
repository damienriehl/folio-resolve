"""Token n-gram lookup shared by the FOLIO and OpenGloss annotation layers."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from ..entity_ruler import _STOPWORDS
from .function_words import FUNCTION_WORDS
from .tokenize import normalize, tokenize

Payload = TypeVar("Payload")
MatchMethod = Literal["exact", "plural"]


@dataclass(frozen=True)
class IndexMatch(Generic[Payload]):
    start: int
    end: int
    payloads: tuple[Payload, ...]
    match_method: MatchMethod


def plural_candidates(word: str) -> Iterable[str]:
    """Try final-token English plurals in the prescribed order."""
    if word.endswith("ies"):
        yield word[:-3] + "y"
    if word.endswith("es"):
        yield word[:-2]
    if word.endswith("s"):
        yield word[:-1]


class NGramIndex(Generic[Payload]):
    """Keep every payload and every overlap; plural fallback is explicitly opt-in."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, ...], list[Payload]] = {}
        self.max_length = 0

    def add(self, label: str, payload: Payload) -> None:
        key = normalize(label)
        if key:
            self.entries.setdefault(key, []).append(payload)
            self.max_length = max(self.max_length, (len(key) + 1) // 2)

    def lookup(
        self,
        text: str,
        *,
        regions: Iterable[tuple[int, int]] | None = None,
        noun: Callable[[Payload], bool] | None = None,
        content_word: Callable[[str], bool] | None = None,
    ) -> list[IndexMatch[Payload]]:
        tokens = tokenize(text)
        region_starts: list[int] = []
        region_max_ends: list[int] = []
        if regions is not None:
            for start, end in sorted(regions):
                region_starts.append(start)
                region_max_ends.append(
                    max(region_max_ends[-1], end) if region_max_ends else end
                )
        matches: list[IndexMatch[Payload]] = []
        for i, first in enumerate(tokens):
            # Source offsets are monotone. A shared source character with the
            # preceding/following word means the match cuts through its expansion.
            # Peripheral punctuation and gaps produce no word tokens to cover.
            if i and tokens[i - 1].end > first.start:
                continue
            key: tuple[str, ...] = (first.key,)
            has_content = False
            for length in range(1, min(self.max_length, len(tokens) - i) + 1):
                last = tokens[i + length - 1]
                if length > 1:
                    key = (*key, last.gap_before, last.key)
                has_content = has_content or (
                    last.key not in _STOPWORDS
                    and (
                        last.key not in FUNCTION_WORDS
                        if content_word is None
                        else content_word(last.key)
                    )
                )
                if regions is not None:
                    # Among regions starting by this span, one original region
                    # must reach its end; adjacent regions cannot be combined.
                    region_index = bisect_right(region_starts, first.start) - 1
                    if region_index < 0 or region_max_ends[region_index] < last.end:
                        continue
                if not has_content:
                    continue
                if i + length < len(tokens) and tokens[i + length].start < last.end:
                    continue
                payloads = tuple(self.entries.get(key, ()))
                method: MatchMethod = "exact"
                if not payloads and noun is not None:
                    for candidate in plural_candidates(key[-1]):
                        payloads = tuple(
                            item
                            for item in self.entries.get((*key[:-1], candidate), ())
                            if noun(item)
                        )
                        if payloads:
                            method = "plural"
                            break
                if payloads:
                    matches.append(IndexMatch(first.start, last.end, payloads, method))
        return sorted(matches, key=lambda match: (match.start, -match.end))
