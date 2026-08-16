"""Re-render sheet.html from a saved packet.json -- no pipeline re-run, no data change.

The full ``run_audit.py --mode packet-v2`` path re-derives the packet from live gold, which
changes the packet fingerprint and therefore the browser draft key -- stranding whatever sitting
the reviewer has open. When the only change is to the renderer (CSS/JS/markup), re-rendering the
SAME packet.json keeps that key byte-identical, so a published fix reaches the reviewer without
costing them their in-progress work.

Usage: uv run python eval/rerender_sheet.py PACKET_JSON OUT_HTML
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.audit import Packet, PacketRow, VariantStats
from folio_eval.packet_render import render_sheet_v2

packet_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
payload = json.loads(packet_path.read_text(encoding="utf-8"))

rows = tuple(
    PacketRow(
        decision_id=entry["decision_id"],
        section=entry["section"],
        item_id=entry["item_id"],
        firm=entry["firm"],
        stratum=entry["stratum"],
        stratum_id=entry["stratum_id"],
        ancestor_path=tuple(entry["ancestor_path"]),
        surface_label=entry["surface_label"],
        input_text=entry["input_text"],
        slice_name=entry["slice"],
        reason_class=entry["reason_class"],
        suggested_action=entry["suggested_action"],
        gold=tuple(entry["gold"]),
        pipeline=tuple(entry["pipeline"]),
        proposed_iris=tuple(entry["proposed_iris"]),
        notes_text=entry["notes_text"],
        confidence=entry["confidence"],
        label_frequency=entry["label_frequency"],
        sort_score=entry["sort_score"],
        extra=entry["extra"],
    )
    for entry in payload["rows"]
)
variants = tuple(VariantStats(**entry) for entry in payload["variants"] or ())
packet = Packet(
    rows=rows,
    variants=variants,
    replay=payload["replay"],
    split=None,
    counts=payload["counts"],
    overflow=payload["overflow"],
    meta=payload["meta"],
)
out_path.write_text(render_sheet_v2(packet), encoding="utf-8")
print(f"wrote {out_path} ({out_path.stat().st_size} bytes) from {len(rows)} rows")
