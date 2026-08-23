"""Render the atomic-unit removal candidates in the ordinary review workspace.

Damien reviews in one place and one shape, so the candidates ship as a normal packet rendered by
``render_sheet_v2`` rather than as a bespoke page: same one-line rows, same checkbox, same export.

The baseline is deliberately left at the truth of current gold (every concept ``keep``). The
workspace exports a DIFF against its baseline, so leaving it honest means unchecking a flagged
concept emits a real ``remove`` -- whereas pre-setting the baseline to ``remove`` would render the
proposal but export nothing when Damien agrees with it, which is exactly backwards.

The flag therefore rides on the concept: a marker on the label so it is visible in the one-line
row, and the reason in the definition so it reads in the inspector when the concept is selected.

Usage: uv run python eval/build_atomic_candidates.py AUDIT PACKET OUT_HTML
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from folio_eval.audit import Packet, PacketRow, VariantStats
from folio_eval.packet_render import render_sheet_v2

audit = json.loads(Path(sys.argv[1]).read_text())
packet = json.loads(Path(sys.argv[2]).read_text())
out = Path(sys.argv[3])

flags: dict[str, dict[str, dict[str, Any]]] = {}
for f in audit["flagged"]:
    flags.setdefault(f["id"], {})[f["iri"]] = f

rows = []
for raw in packet["rows"]:
    rid = raw["decision_id"]
    hits = flags.get(rid)
    if not hits:
        continue
    gold = []
    marked = []
    for entry in raw.get("gold") or []:
        entry = dict(entry)
        hit = hits.get(entry["iri"].rsplit("/", 1)[-1])
        if hit:
            entry["label"] = "⚠ " + str(entry.get("label", ""))
            entry["definition"] = (
                f"FLAGGED {hit['confidence'].upper()} as parent-level: {hit['why']}. "
                "Uncheck to remove it from gold; leave it checked to keep it."
            )
            marked.append(hit)
        gold.append(entry)
    extra = dict(raw.get("extra") or {})
    # Truthful baseline: current gold is all-keep, so an unchecked box exports a real removal.
    extra["baseline"] = {
        "gold": {g["iri"]: "keep" for g in gold},
        "pipeline": {p["iri"]: "not_gold" for p in (raw.get("pipeline") or [])},
    }
    extra.pop("folded", None)
    n = len(marked)
    rows.append(
        PacketRow(
            decision_id=rid,
            section=raw["section"],
            item_id=raw["item_id"],
            firm=raw["firm"],
            stratum=raw["stratum"],
            stratum_id=raw["stratum_id"],
            ancestor_path=tuple(raw["ancestor_path"]),
            surface_label=raw["surface_label"],
            input_text=raw["input_text"],
            slice_name=raw["slice"],
            reason_class="atomic-unit audit",
            suggested_action=(
                f"{n} concept(s) marked ⚠ look like the PARENT rather than this atomic unit. "
                "Uncheck the ones you agree are contamination; leave the rest checked. "
                "Nothing here has been applied to gold."
            ),
            gold=tuple(gold),
            pipeline=(),
            proposed_iris=(),
            notes_text=raw.get("notes_text"),
            confidence=raw.get("confidence", 0.0),
            label_frequency=raw.get("label_frequency", 0),
            sort_score=-float(sum(h["confidence"] == "high" for h in marked)),
            extra=extra,
        )
    )

rows.sort(key=lambda r: r.sort_score)
# Give this sheet its own gold identity. The draft key's first segment is the baseline id, and
# the recovery feature offers any sitting sharing it -- so without this the candidates page would
# offer to import Damien's 245-decision main sitting over the top of a 53-row confirmation pass.
meta = dict(packet["meta"])
for field in ("current_gold_id", "gold_id", "parent_gold_id"):
    if meta.get(field):
        meta[field] = f"{meta[field]}-atomic"
out.write_text(
    render_sheet_v2(
        Packet(
            rows=tuple(rows),
            variants=tuple(VariantStats(**v) for v in packet["variants"] or ()),
            replay=packet["replay"],
            split=None,
            counts={"atomic_candidates": len(rows), "flagged_concepts": len(audit["flagged"])},
            overflow={},
            meta=meta,
        )
    ),
    encoding="utf-8",
)
print(f"wrote {out} ({out.stat().st_size} bytes) rows={len(rows)} flags={len(audit['flagged'])}")
