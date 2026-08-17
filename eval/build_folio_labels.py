"""Dump ``{short_iri: rdfs_label}`` for every FOLIO concept, for embedding in review sheets.

Usage: uv run python eval/build_folio_labels.py OUT_JSON
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from folio import FOLIO

    index: dict[str, str] = {}
    for owl in FOLIO().classes:
        iri = getattr(owl, "iri", "") or ""
        if "folio.openlegalstandard.org" not in iri:
            continue
        label = getattr(owl, "label", None) or getattr(owl, "preferred_label", None) or ""
        if label:
            index[iri.rsplit("/", 1)[-1]] = str(label)
    out = Path(sys.argv[1])
    out.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(index)} concepts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
