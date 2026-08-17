"""Build the consolidated adjudication review sheet from an exported draft bundle.

The bundle is produced in the browser, because the workspace has no server and every sitting lives
only in ``localStorage``. Paste this in the console on the published sheet to make one:

    (() => {
      const b = {exported_at: new Date().toISOString(), drafts: {}};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.indexOf('folio-eval-draft:') === 0) { b.drafts[k] = JSON.parse(localStorage.getItem(k)); }
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(b)], {type: 'application/json'}));
      a.download = 'folio-eval-all-drafts.json';
      document.body.appendChild(a); a.click(); a.remove();
    })()

Usage: uv run python eval/consolidate_answers.py BUNDLE [PACKET] [OUT]
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folio_eval.consolidate import (
    consolidate,
    load_sittings,
    render_consolidated,
)

DEFAULT_PACKET = Path("eval/data/reports/audit_packet_v2/packet.json")
DEFAULT_OUT = Path("eval/data/reports/audit_packet_v2/consolidated-review.html")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bundle_path = Path(argv[0])
    packet_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PACKET
    out_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    merged = consolidate(load_sittings(bundle))
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_consolidated(merged, packet, generated=generated), encoding="utf-8")

    present = set(str(row["decision_id"]) for row in packet.get("rows") or [])
    authored = sum(len(s.decisions) for s in merged.sittings)
    print(f"sittings:          {len(merged.sittings)}")
    print(f"authored rows:     {authored}")
    print(f"applicable now:    {len(merged.rows_for(present, inside=True))}")
    print(f"orphaned rows:     {len(merged.rows_for(present, inside=False))}")
    print(f"true collisions:   {len({rid for rid, _ in merged.collisions})}")
    print(f"agreeing repeats:  {len({rid for rid, _ in merged.agreements})}")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover - I/O entry point
    raise SystemExit(main(sys.argv[1:]))
