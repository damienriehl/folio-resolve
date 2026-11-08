"""Shared Unicode normalization with offsets into the original input."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[\s\-\u00ad\u2010-\u2015\u2212]*\Z")
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})


@dataclass(frozen=True)
class Token:
    key: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """NFKC/casefold words, preserving source ranges through Unicode expansions."""
    normalized: list[str] = []
    offsets: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = start + 1
        chunk = unicodedata.normalize("NFKC", text[start:end])
        while end < len(text):
            following = unicodedata.normalize("NFKC", text[end])
            combined = unicodedata.normalize("NFKC", text[start : end + 1])
            # Composition can cross zero-combining-class characters (e.g. Hangul).
            if not unicodedata.combining(following[0]) and combined == chunk + following:
                break
            chunk = combined
            end += 1
        chunk = chunk.casefold().translate(_APOSTROPHES)
        normalized.append(chunk)
        offsets.extend([(start, end)] * len(chunk))
        start = end
    folded = "".join(normalized)
    tokens: list[Token] = []
    cursor = 0
    while cursor < len(folded):
        if not _word_start(folded[cursor]):
            cursor += 1
            continue
        first = cursor
        cursor += 1
        while cursor < len(folded):
            char = folded[cursor]
            if (
                _word_start(char)
                or unicodedata.category(char).startswith("M")
                or (char == "'" and cursor + 1 < len(folded) and _word_start(folded[cursor + 1]))
            ):
                cursor += 1
            else:
                break
        tokens.append(Token(folded[first:cursor], offsets[first][0], offsets[cursor - 1][1]))
    return tokens


def _word_start(char: str) -> bool:
    return char.isalnum() or char == "_"


def normalize(text: str) -> tuple[str, ...]:
    """Interleave normalized words with their normalized internal gaps."""
    tokens = tokenize(text)
    key: list[str] = []
    for i, token in enumerate(tokens):
        if i:
            key.append(normalize_gap(text[tokens[i - 1].end : token.start]))
        key.append(token.key)
    return tuple(key)


def normalize_gap(text: str) -> str:
    """Fold whitespace/hyphen gaps to a space; preserve other punctuation."""
    folded = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)
    if _SEPARATORS.fullmatch(folded) is not None:
        return " "
    return "".join(char for char in folded if not char.isspace())
