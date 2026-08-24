"""Shared constants and types for synthetic scoring and its durable checkpoints."""

from __future__ import annotations

from typing import Literal

SyntheticItemKind = Literal["scoreable", "nomatch"]
SUPPRESSION_CATEGORIES = ("blocklist", "place_gate", "short_label_gate", "score_floor")
