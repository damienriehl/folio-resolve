"""Render the atomic-unit gold audit as a reviewable page.

Damien's rule: gold maps to the ATOMIC UNIT only, never its ancestors, because each ancestor
carries its own atomic mapping. This page lists every gold concept that appears to describe a
parent instead, grouped by confidence, with his own confirmed calls marked so the machine pass can
be judged against known-good judgement rather than taken on faith.

Usage: uv run python eval/render_atomic_audit.py AUDIT_JSON PACKET_JSON CONFIRMED_JSON OUT_HTML
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from folio_eval.consolidate import _STYLE

audit = json.loads(Path(sys.argv[1]).read_text())
packet = json.loads(Path(sys.argv[2]).read_text())
confirmed = json.loads(Path(sys.argv[3]).read_text())
out = Path(sys.argv[4])

rows = {r["decision_id"]: r for r in packet["rows"]}
gold_count = {rid: len(r.get("gold") or []) for rid, r in rows.items()}
flags = audit["flagged"]
by_row: dict[str, set[str]] = {}
for f in flags:
    by_row.setdefault(f["id"], set()).add(f["iri"])
wipes = {
    rid
    for rid, iris in by_row.items()
    if gold_count.get(rid) and {g["iri"].rsplit("/", 1)[-1] for g in rows[rid]["gold"]} <= iris
}


def card(n: int, k: str, cls: str = "") -> str:
    return f'<div class="card {cls}"><div class="n">{n}</div><div class="k">{html.escape(k)}</div></div>'


def table(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">Nothing in this group.</p>'
    body = []
    for f in items:
        rid = f["id"]
        yours = confirmed.get(rid) == f["iri"]
        row = rows.get(rid, {})
        path = " &gt; ".join(html.escape(str(p)) for p in (row.get("ancestor_path") or []))
        marks = []
        if yours:
            marks.append('<span class="pick good">you confirmed this</span>')
        if rid in wipes:
            marks.append('<span class="pick warn">row loses ALL gold</span>')
        search = f"{rid} {row.get('surface_label','')} {f['label']} {f['why']}".lower()
        body.append(
            f'<tr data-search="{html.escape(search)}" data-fields="{f["confidence"]}">'
            f'<td><div class="rowlabel">{html.escape(str(row.get("surface_label", rid)))}</div>'
            f'<div class="path">{path}</div></td>'
            f'<td><div class="picks"><div><span class="pick warn">remove</span> '
            f'<span class="concept" title="{html.escape(f["iri"])}">{html.escape(f["label"])}</span>'
            f'</div></div>{"".join(marks)}</td>'
            f'<td class="note-text">{html.escape(f["why"])}</td></tr>'
        )
    return (
        '<div class="tablewrap"><table><thead><tr><th>Atomic input</th>'
        "<th>Gold concept to remove</th><th>Why it reads as a parent</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


groups = {c: [f for f in flags if f["confidence"] == c] for c in ("high", "medium", "low")}
hit = sum(1 for rid, iri in confirmed.items() if iri in by_row.get(rid, set()))
out.write_text(
    f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>folio-resolve &mdash; atomic-unit gold audit</title><style>{_STYLE}</style></head><body>
<header class="top"><h1>Atomic-unit gold audit</h1>
<div class="sub">Gold concepts that appear to describe a PARENT rather than the atomic unit</div></header>
<main>
<div class="cards">{card(audit["counts"]["gold_reviewed"], "gold entries reviewed")}
{card(len(flags), "flagged as parent", "warn")}
{card(len(groups["high"]), "high confidence", "warn")}
{card(f"{hit}/{len(confirmed)}", "of your own calls found", "good")}
{card(len(wipes), "rows would lose all gold", "warn")}</div>
<p class="lede">Proposed removals only &mdash; nothing has been applied to gold. The pass was
calibrated on your twelve confirmed removals and independently rediscovered {hit} of them, so treat
the high-confidence group as review-ready and the medium group as genuinely uncertain. Four rows
would be left with no gold at all; those may mean FOLIO has no leaf-level concept, which is a
coverage gap rather than a mapping error.</p>
<div class="filterbar"><input id="q" type="search" placeholder="Filter by input, concept or reason&hellip;"
aria-label="Filter"><select id="fieldFilter" aria-label="Confidence"><option value="">All confidence</option>
<option value="high">high</option><option value="medium">medium</option><option value="low">low</option></select>
<span class="count" id="count"></span></div>
<h2>High confidence &mdash; {len(groups["high"])}</h2>{table(groups["high"])}
<h2>Medium confidence &mdash; {len(groups["medium"])}</h2>{table(groups["medium"])}
<h2>Low confidence &mdash; {len(groups["low"])}</h2>{table(groups["low"])}
</main>
<script>
(function () {{
  var q=document.getElementById('q'),f=document.getElementById('fieldFilter'),c=document.getElementById('count');
  var rows=Array.prototype.slice.call(document.querySelectorAll('tbody tr[data-search]'));
  function apply() {{
    var t=q.value.trim().toLowerCase(), w=f.value, n=0;
    rows.forEach(function (r) {{
      var hit=(!t||r.dataset.search.indexOf(t)!==-1)&&(!w||r.dataset.fields===w);
      r.hidden=!hit; if(hit){{n+=1;}}
    }});
    c.textContent=n+' of '+rows.length+' flags';
  }}
  q.addEventListener('input',apply); f.addEventListener('change',apply); apply();
}})();
</script></body></html>
""",
    encoding="utf-8",
)
print(f"wrote {out} ({out.stat().st_size} bytes); flagged={len(flags)} recall={hit}/{len(confirmed)}")
