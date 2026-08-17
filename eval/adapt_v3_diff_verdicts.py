"""Translate v3-diff checkbox verdicts into the fold's native pairing contract.

The v3-diff sheet renders pairing rows as one-line concept checkboxes (Damien's requested format),
so its export speaks gold/level_mappings/added_mappings. The fold's pairing branch consumes
``pairing`` + ``edited_iris`` ({item_id: [iris]}), replacing the row's own contribution per item.
This adapter reconstructs the final per-level concept state from the export and re-keys it to the
items the pairing row names.

Final state per row: baseline (v6 applied) overridden by his gold verdicts, plus added concepts.
Level membership: his exported level_mappings when present (collect() exports the FULL chip state,
not a delta); otherwise the sheet-open state (v6), with one fallback -- a v3-only concept he
included without touching chips has no v6 chips, so it takes its v3 levels from the judgment file.

Usage: uv run python eval/adapt_v3_diff_verdicts.py VERDICTS V3_JUDGMENT PACKET OUT_JSON
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

verdicts = json.loads(Path(sys.argv[1]).read_text())
verdicts = verdicts.get("decisions", verdicts)
v3 = json.loads(Path(sys.argv[2]).read_text())
packet = json.loads(Path(sys.argv[3]).read_text())
out_path = Path(sys.argv[4])

rows = {r["decision_id"]: r for r in packet["rows"] if r["section"] == "pairing"}
adapted: dict[str, dict] = {}
stats = {"rows": 0, "items_edited": 0, "v3_level_fallbacks": 0, "skipped_unchanged": 0}

for rid, export in verdicts.items():
    row = rows.get(rid)
    if not row:
        continue
    assignments = (row.get("extra") or {}).get("assignments") or {}

    level_item: dict[int, str] = {}
    level_inputs: dict[int, str] = {}
    for reading in assignments.values():
        for lv in reading or []:
            level = int(lv.get("level") or 0)
            if lv.get("item_id"):
                level_item.setdefault(level, str(lv["item_id"]))
            if lv.get("input"):
                level_inputs.setdefault(level, str(lv["input"]))
    ordered = sorted(level_inputs)
    chip_to_level = {f"L{index}": level for index, level in enumerate(ordered, start=1)}

    def reading_levels(name: str, assignments: dict = assignments) -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for lv in assignments.get(name) or []:
            for tag in lv.get("tags") or []:
                if tag.get("iri"):
                    out.setdefault(tag["iri"], set()).add(int(lv.get("level") or 0))
        return out

    applied = reading_levels("applied")
    chosen = reading_levels(v3.get(rid, {}).get("pairing") or "applied")

    # final keep/remove per concept: v6 membership is the baseline, his verdicts override
    state: dict[str, str] = {iri: "keep" for iri in applied}
    for iri, verdict in (export.get("gold") or {}).items():
        state[iri] = verdict
    for entry in export.get("added_mappings") or []:
        if entry.get("iri"):
            state[entry["iri"]] = "keep"

    # final level membership per concept
    exported_lm = export.get("level_mappings")
    membership: dict[str, set[int]] = {}
    if exported_lm:
        for chip, iris in exported_lm.items():
            level = chip_to_level.get(chip)
            if level is None:
                continue
            for iri in iris:
                membership.setdefault(iri, set()).add(level)
    else:
        membership = {iri: set(levels) for iri, levels in applied.items()}
    for iri, verdict in state.items():
        if verdict == "keep" and not membership.get(iri):
            if chosen.get(iri):
                membership[iri] = set(chosen[iri])  # his v3 ruling's levels
                stats["v3_level_fallbacks"] += 1
            else:
                membership[iri] = set(ordered)  # nowhere stated: contribute to the whole pair

    edited: dict[str, set[str]] = {}
    for iri, verdict in state.items():
        if verdict != "keep":
            continue
        for level in membership.get(iri, set()):
            item = level_item.get(level)
            if item:
                edited.setdefault(item, set()).add(iri)
    for item in level_item.values():
        edited.setdefault(item, set())  # an item losing everything must still be named

    prev = {}
    for iri, levels in applied.items():
        for level in levels:
            item = level_item.get(level)
            if item:
                prev.setdefault(item, set()).add(iri)
    if {k: v for k, v in edited.items()} == {k: prev.get(k, set()) for k in edited}:
        stats["skipped_unchanged"] += 1
        continue

    decision: dict = {"edited_iris": {item: sorted(iris) for item, iris in edited.items()}}
    if export.get("gold_note"):
        decision["gold_note"] = export["gold_note"]
    adapted[rid] = decision
    stats["rows"] += 1
    stats["items_edited"] += len(edited)

out_path.write_text(json.dumps(adapted, indent=1, sort_keys=True), encoding="utf-8")
print(json.dumps(stats))
print(f"wrote {out_path}")
