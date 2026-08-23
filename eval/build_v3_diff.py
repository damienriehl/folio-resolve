"""Render the v3-vs-v6 pairing diff in the one-line checkbox workspace format.

(identity -v3diff2: the retired readings-block cut minted -v3diff drafts, and recovery must not
offer those into this sheet.) First cut showed each discrepancy as the pairing readings block; Damien's verdict: unnecessarily
confusing. This version replicates the gold-evaluation ergonomics exactly -- one line per concept
with a single include/exclude checkbox, level chips, and the connector lines -- and expresses the
DIFF as adornments on the concepts themselves:

* a concept only his v3 ruling had carries a "v3 only" tag and arrives UNCHECKED;
* a concept only v6 holds carries a "v6 only" tag and arrives CHECKED;
* concepts both agree on are unadorned and checked.

Checked means "in gold". The baseline is the v6 state, so the export is a diff: leaving the sheet
as it opened exports nothing, checking a v3-only concept exports keep, unchecking a v6-only one
exports remove. Rows where v3 and v6 fully agree are excluded by construction, which is the
requested include-only-differences filter.

Usage: uv run python eval/build_v3_diff.py V3_JUDGMENT PACKET OUT_HTML [LABELS_JSON]

LABELS_JSON is an ``{short_iri: rdfs_label}`` map (see the loader below); when present it is
embedded so Add-concept needs only an IRI and the label fills itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from folio_eval.audit import Packet, PacketRow, VariantStats
from folio_eval.packet_render import render_sheet_v2

v3 = json.loads(Path(sys.argv[1]).read_text())
packet = json.loads(Path(sys.argv[2]).read_text())
out = Path(sys.argv[3])
label_index = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else None


def by_level(reading: object) -> dict[int, dict[str, str]]:
    """level -> {iri: label} for one reading."""
    out: dict[int, dict[str, str]] = {}
    for lv in cast(list[dict[str, Any]], reading or []):
        level = int(lv.get("level") or 0)
        for tag in lv.get("tags") or []:
            if tag.get("iri"):
                out.setdefault(level, {})[tag["iri"]] = tag.get("label", "")
    return out


rows = []
for raw in packet["rows"]:
    rid = raw["decision_id"]
    ruling = v3.get(rid)
    if raw["section"] != "pairing" or not ruling or not ruling.get("pairing"):
        continue
    assignments = (raw.get("extra") or {}).get("assignments") or {}
    chosen = by_level(assignments.get(ruling["pairing"]))
    applied = by_level(assignments.get("applied"))
    # Compare IRI sets per level, not labels: a label-string mismatch on the same concept is not
    # a difference anyone can rule on, and including it renders a row with nothing marked.
    if {lv: set(d) for lv, d in chosen.items()} == {lv: set(d) for lv, d in applied.items()}:
        continue  # v3 and v6 agree -- excluded by construction

    # Input hierarchy for the level pane: the paired inputs at their own levels.
    level_inputs: dict[int, str] = {}
    for reading in assignments.values():
        for lv in reading or []:
            level = int(lv.get("level") or 0)
            if lv.get("input"):
                level_inputs.setdefault(level, str(lv["input"]))
    ordered_levels = sorted(level_inputs)
    input_text = " > ".join(level_inputs[level] for level in ordered_levels)
    chip = {level: f"L{index}" for index, level in enumerate(ordered_levels, start=1)}

    def chips_of(
        source: dict[int, dict[str, str]], iri: str, chip: dict[int, str] = chip
    ) -> tuple[str, ...]:
        """The concept's level memberships under one version, in chip terms (L1, L2, ...)."""
        return tuple(
            chip[level] for level in sorted(source) if iri in source[level] and level in chip
        )

    gold = []
    level_map: dict[str, list[str]] = {}  # the v6 applied state -- what the chips show on open
    baseline_gold: dict[str, str] = {}
    labels: dict[str, str] = {}
    for source in (applied, chosen):
        for concepts in source.values():
            labels.update(concepts)
    for iri, label in labels.items():
        v6c, v3c = chips_of(applied, iri), chips_of(chosen, iri)
        if v6c and v3c and v6c == v3c:
            column, prefix = "", ""
            definition = "Both v3 and v6 agree on this concept."
        elif v6c and v3c:
            column = f"levels differ · v3: {' '.join(v3c)} · v6: {' '.join(v6c)}"
            prefix = "◆ "
            definition = (
                f"DIFFERENCE — same concept, different level. Your v3 ruling put it at "
                f"{', '.join(v3c)}; v6 holds it at {', '.join(v6c)}. The chips open at the "
                "v6 state; click chips to move it."
            )
        elif v6c:
            column, prefix = "v6 only · your v3 ruling dropped this", "◇ "
            definition = f"DIFFERENCE — {column}. Checked = keep in gold; unchecked = remove."
        else:
            column, prefix = "v3 only · not in current gold", "◆ "
            definition = f"DIFFERENCE — {column}. Check it to bring your v3 ruling into gold."
        gold.append(
            {"iri": iri, "label": prefix + label, "column": column, "definition": definition}
        )
        baseline_gold[iri] = "keep" if v6c else "remove"
        for chip_name in v6c:  # chips open at the v6 truth; edits export as level diffs
            level_map.setdefault(chip_name, []).append(iri)

    note = f' Your v3 note: "{ruling["note"]}"' if ruling.get("note") else ""
    diff_count = sum(1 for e in gold if e["column"])
    extra = {
        "system_level_mappings": level_map,
        "baseline": {"gold": baseline_gold, "pipeline": {}, "level_mappings": level_map},
    }
    rows.append(
        PacketRow(
            decision_id=rid,
            section="consistency",
            item_id=raw["item_id"],
            firm=raw["firm"],
            stratum=raw["stratum"],
            stratum_id=raw["stratum_id"],
            ancestor_path=tuple(raw["ancestor_path"]),
            surface_label=raw["surface_label"],
            input_text=input_text,
            slice_name=raw["slice"],
            reason_class="v3 differs from v6",
            suggested_action=(
                f"{diff_count} concept(s) marked ◆/◇ differ between your v3 ruling and current "
                "gold. Check to include, uncheck to exclude; leaving the sheet as it opened "
                "exports nothing." + note
            ),
            gold=tuple(gold),
            pipeline=(),
            proposed_iris=(),
            notes_text=None,
            confidence=0.0,
            label_frequency=0,
            sort_score=0.0 if ruling.get("note") else 1.0,
            extra=extra,
        )
    )

rows.sort(key=lambda r: (r.sort_score, r.surface_label))
meta = dict(packet["meta"])
for field in ("current_gold_id", "gold_id", "parent_gold_id"):
    if meta.get(field):
        meta[field] = f"{meta[field]}-v3diff2"
out.write_text(
    render_sheet_v2(
        Packet(
            rows=tuple(rows),
            variants=tuple(VariantStats(**v) for v in packet["variants"] or ()),
            replay=packet["replay"],
            split=None,
            counts={"v3_differs": len(rows)},
            overflow={},
            meta=meta,
        ),
        label_index=label_index,
    ),
    encoding="utf-8",
)
print(f"wrote {out} ({out.stat().st_size} bytes) rows={len(rows)}")
