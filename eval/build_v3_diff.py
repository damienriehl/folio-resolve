"""Render the v3-vs-v6 pairing diff as a rulable workspace sheet.

Damien made 132 pairing rulings against gold v3. Two gold versions later, 62 of them are already
the live applied reading in v6 and need nothing. This sheet carries ONLY the rows where his v3
ruling differs from what v6 currently holds, so ruling on the discrepancy is one radio click per
row in the exact workspace he already uses. Rows that agree are excluded by construction -- the
requested "filter to only where v3 differs" is the sheet itself.

The baseline records the reading v6 currently applies (when it matches a named reading), so the
export is a DIFF: re-affirming v6 exports nothing, and choosing his v3 reading -- or any other --
exports a real pairing decision that folds into v7.

Usage: uv run python eval/build_v3_diff.py V3_JUDGMENT PACKET OUT_HTML
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from folio_eval.audit import Packet, PacketRow, VariantStats
from folio_eval.packet_render import render_sheet_v2

v3 = json.loads(Path(sys.argv[1]).read_text())
packet = json.loads(Path(sys.argv[2]).read_text())
out = Path(sys.argv[3])


def norm(reading: object) -> tuple:
    levels = []
    for lv in reading or []:  # type: ignore[union-attr]
        levels.append((lv.get("level"), tuple(sorted(lv.get("iris") or []))))
    return tuple(sorted(levels))


def describe(reading: object) -> str:
    bits = []
    for lv in sorted(reading or [], key=lambda x: (x.get("level") or 0)):  # type: ignore[union-attr]
        tags = [t.get("label", "") for t in (lv.get("tags") or [])]
        bits.append(f"L{lv.get('level')}: {', '.join(tags) if tags else '(nothing)'}")
    return " / ".join(bits) if bits else "(empty)"


rows = []
for raw in packet["rows"]:
    rid = raw["decision_id"]
    ruling = v3.get(rid)
    if raw["section"] != "pairing" or not ruling or not ruling.get("pairing"):
        continue
    extra = dict(raw.get("extra") or {})
    assignments = extra.get("assignments") or {}
    choice = ruling["pairing"]
    if norm(assignments.get(choice)) == norm(assignments.get("applied")):
        continue  # v3 and v6 agree; nothing to rule on
    # Which named reading is v6 actually holding? Baseline on that name keeps the export a diff.
    applied_name = next(
        (name for name in ("heuristic", "alternative")
         if norm(assignments.get(name)) == norm(assignments.get("applied"))),
        None,
    )
    extra["baseline"] = {"pairing": applied_name} if applied_name else {}
    extra.pop("folded", None)  # the discrepancy is an open question, not a settled ruling
    v6_desc = describe(assignments.get("applied"))
    v3_desc = describe(assignments.get(choice))
    note = f' Your v3 note: "{ruling["note"]}"' if ruling.get("note") else ""
    rows.append(
        PacketRow(
            decision_id=rid,
            section="pairing",
            item_id=raw["item_id"],
            firm=raw["firm"],
            stratum=raw["stratum"],
            stratum_id=raw["stratum_id"],
            ancestor_path=tuple(raw["ancestor_path"]),
            surface_label=raw["surface_label"],
            input_text=raw["input_text"],
            slice_name=raw["slice"],
            reason_class="v3 differs from v6",
            suggested_action=(
                f'In v3 you chose "{choice}" -> {v3_desc}. '
                f"Gold v6 currently holds -> {v6_desc}. "
                "Pick the reading that should stand; re-affirming v6 exports nothing."
                + note
            ),
            gold=tuple(raw.get("gold") or ()),
            pipeline=(),
            proposed_iris=(),
            notes_text=raw.get("notes_text"),
            confidence=raw.get("confidence", 0.0),
            label_frequency=raw.get("label_frequency", 0),
            sort_score=0.0 if ruling.get("note") else 1.0,  # noted rows first
            extra=extra,
        )
    )

rows.sort(key=lambda r: (r.sort_score, r.surface_label))
meta = dict(packet["meta"])
for field in ("current_gold_id", "gold_id", "parent_gold_id"):
    if meta.get(field):
        meta[field] = f"{meta[field]}-v3diff"  # own draft identity; see build_atomic_candidates
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
        )
    ),
    encoding="utf-8",
)
print(f"wrote {out} ({out.stat().st_size} bytes) differing_rows={len(rows)}")
