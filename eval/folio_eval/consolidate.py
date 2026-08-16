"""Consolidate every saved adjudication sitting into one reviewable sheet.

The workspace keeps its draft in ``localStorage`` under a key derived from the packet -- baseline
id, ontology hash, row count, content fingerprint. Republishing over a re-derived packet therefore
mints a *new* key and leaves the previous sitting unreferenced, so a reviewer accumulates several
disconnected sittings across gold versions without ever being told. This module reads a bundle of
those sittings and answers the only three questions that matter when picking the work back up:

* **What did I actually decide?** -- separated from the machine state the sheet emits for every
  row without a baseline, which inflates a raw decision count by an order of magnitude.
* **Where do two sittings disagree?** -- a genuine collision is the same row *and the same field*
  carrying different values. Two sittings that touched different fields of one row merge cleanly,
  and two that recorded the same value agree; neither is work to redo.
* **What no longer has a home?** -- authored work whose row is absent from the current packet,
  which is the only category that silently evaporates on the next fold.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DRAFT_PREFIX = "folio-eval-draft:"

#: Fields only a person can author. Everything else in a saved entry is machine state that
#: ``collect()`` emits for any row lacking a baseline.
AUTHORED_FIELDS = (
    "pairing",
    "note",
    "gold_note",
    "pipeline_note",
    "level_notes",
    "added_mappings",
    "mapping_options",
)

#: A verdict equal to its default is the sheet's opinion, not the reviewer's.
VERDICT_DEFAULTS = {"gold": "keep", "pipeline": "not_gold"}


def authored(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The part of one saved entry a human must have produced, or ``{}`` for pure machine state."""
    kept: dict[str, Any] = {name: entry[name] for name in AUTHORED_FIELDS if entry.get(name)}
    for kind, default in VERDICT_DEFAULTS.items():
        verdicts = entry.get(kind)
        if not isinstance(verdicts, Mapping):
            continue
        chosen = {iri: value for iri, value in verdicts.items() if value != default}
        if chosen:
            kept[kind] = chosen
    return kept


@dataclass(frozen=True, slots=True)
class Sitting:
    """One saved draft: its packet key, its gold baseline, and what the reviewer wrote in it."""

    packet_key: str
    decisions: Mapping[str, Mapping[str, Any]]

    @property
    def baseline(self) -> str:
        return self.packet_key.split("|")[0]

    @property
    def rows(self) -> str:
        parts = self.packet_key.split("|")
        return parts[2] if len(parts) > 2 else "?"

    @property
    def label(self) -> str:
        return f"{self.baseline} ({self.rows} rows)"


@dataclass
class Consolidated:
    """Every authored decision across sittings, keyed by row, with provenance and conflicts."""

    sittings: list[Sitting] = field(default_factory=list)
    #: decision_id -> field -> list of (sitting, value)
    by_row: dict[str, dict[str, list[tuple[Sitting, Any]]]] = field(default_factory=dict)
    collisions: list[tuple[str, str]] = field(default_factory=list)
    agreements: list[tuple[str, str]] = field(default_factory=list)

    def rows_for(self, present: set[str] | None, *, inside: bool) -> list[str]:
        """Row ids sorted for display, split by whether the current packet still carries them."""
        if present is None:
            return sorted(self.by_row)
        return sorted(rid for rid in self.by_row if (rid in present) is inside)


def load_sittings(bundle: Mapping[str, Any]) -> list[Sitting]:
    """Read the exported draft bundle, dropping sittings with nothing authored in them."""
    drafts = bundle.get("drafts")
    out: list[Sitting] = []
    for key, saved in (drafts or {}).items():
        if not isinstance(saved, Mapping):
            continue
        raw = saved.get("decisions", saved)
        if not isinstance(raw, Mapping):
            continue
        decisions = {
            str(rid): authored(entry)
            for rid, entry in raw.items()
            if isinstance(entry, Mapping) and authored(entry)
        }
        if decisions:
            out.append(Sitting(str(key).removeprefix(DRAFT_PREFIX), decisions))
    # Newest-looking last is unhelpful; order by authored volume so the richest sitting reads first.
    return sorted(out, key=lambda s: (-len(s.decisions), s.packet_key))


def consolidate(sittings: Sequence[Sitting]) -> Consolidated:
    """Merge sittings per row/field, recording where they genuinely disagree."""
    result = Consolidated(sittings=list(sittings))
    for sitting in sittings:
        for rid, entry in sitting.decisions.items():
            row = result.by_row.setdefault(rid, {})
            for name, value in entry.items():
                row.setdefault(name, []).append((sitting, value))
    for rid, fields in result.by_row.items():
        for name, claims in fields.items():
            if len(claims) < 2:
                continue
            rendered = {json.dumps(value, sort_keys=True) for _, value in claims}
            # Same row, same field, two different answers: the only thing needing adjudication.
            (result.collisions if len(rendered) > 1 else result.agreements).append((rid, name))
    return result


def label_index(packet: Mapping[str, Any]) -> dict[str, str]:
    """``iri -> rdfs:label`` for every concept the current packet mentions."""
    out: dict[str, str] = {}
    for row in packet.get("rows") or []:
        for bucket in ("gold", "pipeline"):
            for concept in row.get(bucket) or []:
                iri, name = str(concept.get("iri", "")), str(concept.get("label", ""))
                if iri and name:
                    out.setdefault(iri, name)
    return out


def row_index(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["decision_id"]): row for row in packet.get("rows") or []}


def _short(iri: str) -> str:
    return iri.rsplit("/", 1)[-1] if iri else ""


def _concept(iri: str, labels: Mapping[str, str]) -> str:
    """A concept as the simplified sheet shows it: the label, with the IRI as hover text."""
    name = labels.get(iri)
    esc = html.escape(iri)
    if name:
        return f'<span class="concept" title="{esc}">{html.escape(name)}</span>'
    return f'<span class="concept unresolved" title="{esc}"><code>{html.escape(_short(iri))}</code></span>'


def _render_pairing(
    value: Any, row: Mapping[str, Any] | None, labels: Mapping[str, str]
) -> str:
    """A pairing choice as the reading it selects, not as the bare word stored in the draft.

    132 of Damien's decisions are pairing choices, and the saved value is only a reading name
    ("heuristic"). Re-joining it to the packet's ``assignments`` shows what he actually chose --
    which input level maps to which concept -- so the sheet can be reviewed without opening the
    workspace and hunting for the row.
    """
    reading = str(value)
    head = f'<span class="pick good">{html.escape(reading)}</span>'
    assignments = (row or {}).get("extra", {}).get("assignments") if row else None
    chosen = assignments.get(reading) if isinstance(assignments, Mapping) else None
    if not isinstance(chosen, list) or not chosen:
        return f'<div class="picks"><div>{head}</div></div>'
    lines = []
    for level in chosen:
        if not isinstance(level, Mapping):
            continue
        tags = [tag for tag in (level.get("tags") or []) if isinstance(tag, Mapping)]
        concepts = (
            ", ".join(_concept(str(tag.get("iri", "")), labels) for tag in tags)
            if tags
            else '<span class="concept unresolved">&mdash; nothing &mdash;</span>'
        )
        lines.append(
            f'<div><span class="lvl">L{html.escape(str(level.get("level", "?")))}</span> '
            f'<span class="in">{html.escape(str(level.get("input", "")))}</span> '
            f'<span class="arrow">&rarr;</span> {concepts}</div>'
        )
    return f'<div class="picks"><div>{head}</div>{"".join(lines)}</div>'


def _render_value(
    name: str,
    value: Any,
    labels: Mapping[str, str],
    row: Mapping[str, Any] | None = None,
) -> str:
    if name == "pairing":
        return _render_pairing(value, row, labels)
    if name in VERDICT_DEFAULTS and isinstance(value, Mapping):
        verdict_class = {"keep": "good", "elevate": "good"}
        bits = []
        for iri, verdict in value.items():
            cls = verdict_class.get(str(verdict), "warn")
            bits.append(
                f'<span class="pick {cls}">{html.escape(str(verdict))}</span> '
                + _concept(str(iri), labels)
            )
        return '<div class="picks">' + "".join(f"<div>{bit}</div>" for bit in bits) + "</div>"
    if name == "added_mappings" and isinstance(value, list):
        bits = [
            '<span class="pick good">added</span> '
            + _concept(str(item.get("iri", "")), labels)
            + (
                ""
                if labels.get(str(item.get("iri", "")))
                else f' <span class="concept">{html.escape(str(item.get("label", "")))}</span>'
            )
            for item in value
            if isinstance(item, Mapping)
        ]
        return '<div class="picks">' + "".join(f"<div>{bit}</div>" for bit in bits) + "</div>"
    if isinstance(value, str):
        return f'<div class="note-text">{html.escape(value)}</div>'
    return f'<div class="note-text mono">{html.escape(json.dumps(value, sort_keys=True))}</div>'


_FILTER_JS = """
<script>
// The sheet is a read-only review surface, so filtering is the only behaviour it needs. Rows carry
// their own searchable text, which keeps this independent of how any cell happens to be rendered.
(function () {
  var q = document.getElementById('q');
  var field = document.getElementById('fieldFilter');
  var count = document.getElementById('count');
  var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr[data-search]'));
  function apply() {
    var text = q.value.trim().toLowerCase();
    var want = field.value;
    var shown = 0;
    rows.forEach(function (row) {
      var hit = (!text || row.dataset.search.indexOf(text) !== -1)
             && (!want || (' ' + row.dataset.fields + ' ').indexOf(' ' + want + ' ') !== -1);
      row.hidden = !hit;
      if (hit) { shown += 1; }
    });
    count.textContent = shown + ' of ' + rows.length + ' rows';
    document.querySelectorAll('.tablewrap').forEach(function (wrap) {
      var any = wrap.querySelector('tbody tr:not([hidden])');
      wrap.style.display = any ? '' : 'none';
    });
  }
  q.addEventListener('input', apply);
  field.addEventListener('change', apply);
  apply();
})();
</script>
"""


_STYLE = """
:root { --bg:#fbfaf7; --card:#fff; --ink:#1c1a17; --muted:#6b6560; --line:#e2ddd4;
        --good:#15803d; --warn:#c2410c; --accent:#b45309; --tag:#f3efe7; }
:root:not([data-theme=light]) { }
@media (prefers-color-scheme: dark) {
  :root { --bg:#17161a; --card:#201f24; --ink:#ece9e4; --muted:#9c968f; --line:#343139;
          --good:#4ade80; --warn:#fb923c; --accent:#fbbf24; --tag:#2a282f; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.55 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif; }
header.top { padding:1.3rem 1.6rem .9rem; border-bottom:1px solid var(--line); background:var(--card); }
h1 { margin:0 0 .2rem; font-size:1.32rem; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:.85rem; }
main { padding:1.2rem 1.6rem 3rem; max-width:74rem; margin:0 auto; }
h2 { font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
     margin:2rem 0 .6rem; padding-bottom:.3rem; border-bottom:1px solid var(--line); }
h2:first-of-type { margin-top:1rem; }
.cards { display:flex; flex-wrap:wrap; gap:.6rem; margin:.8rem 0 0; }
.card { flex:1 1 9rem; background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:.6rem .75rem; }
.card .n { font-size:1.5rem; font-weight:700; line-height:1.1; }
.card .k { font-size:.7rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
.card.warn .n { color:var(--warn); } .card.good .n { color:var(--good); }
table { width:100%; border-collapse:collapse; background:var(--card);
        border:1px solid var(--line); border-radius:8px; overflow:hidden; }
th { text-align:left; font-size:.68rem; text-transform:uppercase; letter-spacing:.06em;
     color:var(--muted); font-weight:700; padding:.5rem .7rem; border-bottom:1px solid var(--line); }
td { padding:.5rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; font-size:.86rem; }
tr:last-child td { border-bottom:0; }
.path { font-size:.74rem; color:var(--muted); }
.rowlabel { font-weight:700; }
.sit { display:inline-block; font:600 .64rem/1.5 ui-monospace,monospace; background:var(--tag);
       border:1px solid var(--line); border-radius:4px; padding:.05rem .3rem; color:var(--muted);
       white-space:nowrap; }
.pick { display:inline-block; font:700 .62rem/1.5 ui-monospace,monospace; border-radius:4px;
        padding:.05rem .3rem; border:1px solid currentColor; }
.pick.good { color:var(--good); } .pick.warn { color:var(--warn); }
.concept { font-weight:600; }
.concept.unresolved { font-weight:400; color:var(--muted); }
.picks div { margin:.1rem 0; }
.note-text { white-space:pre-wrap; }
.mono { font-family:ui-monospace,monospace; font-size:.76rem; }
.field { font:600 .66rem/1.5 ui-monospace,monospace; color:var(--muted); text-transform:uppercase; }
.collide { border-left:3px solid var(--warn); }
.empty { color:var(--muted); font-style:italic; padding:.8rem; background:var(--card);
         border:1px solid var(--line); border-radius:8px; }
.lede { background:var(--card); border:1px solid var(--line); border-left:3px solid var(--accent);
        border-radius:8px; padding:.7rem .9rem; margin:.9rem 0 0; font-size:.88rem; }
code { font-family:ui-monospace,monospace; font-size:.8em; }
.tablewrap { overflow-x:auto; }
.claim { padding:.35rem 0; border-top:1px dashed var(--line); }
.claim:first-child { border-top:0; padding-top:0; }
.claim.collide { border-left:3px solid var(--warn); padding-left:.5rem; }
.claim-head { display:flex; gap:.4rem; align-items:center; margin-bottom:.15rem; }
.lvl { font:700 .62rem/1.5 ui-monospace,monospace; background:var(--tag); border:1px solid var(--line);
       border-radius:4px; padding:.02rem .28rem; color:var(--muted); }
.in { font-style:italic; color:var(--muted); font-size:.82rem; }
.arrow { color:var(--muted); }
.filterbar { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:.5rem;
             align-items:center; background:var(--bg); padding:.6rem 0; border-bottom:1px solid var(--line); }
.filterbar input, .filterbar select { font:inherit; font-size:.85rem; padding:.35rem .5rem;
  border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--ink); }
.filterbar input { flex:1 1 18rem; }
.count { color:var(--muted); font-size:.8rem; }
tr[hidden] { display:none; }
"""


def _row_header(rid: str, rows: Mapping[str, Mapping[str, Any]]) -> str:
    row = rows.get(rid)
    if not row:
        return (
            f'<div class="rowlabel">{html.escape(rid)}</div>'
            '<div class="path">not present in the current packet</div>'
        )
    path = " > ".join(str(p) for p in (row.get("ancestor_path") or []))
    label = str(row.get("surface_label") or rid)
    section = str(row.get("section") or "")
    return (
        f'<div class="rowlabel">{html.escape(label)}</div>'
        f'<div class="path">{html.escape(section)}{" &middot; " if path else ""}{html.escape(path)}</div>'
    )


def _table(
    ids: Sequence[str],
    merged: Consolidated,
    rows: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, str],
    *,
    collisions: set[tuple[str, str]],
) -> str:
    """One table row per input row -- every field stacked in a cell so the filter can hide a whole
    decision at once. A rowspan layout reads fine but makes client-side filtering wrong: hiding a
    ``<tr>`` that owns a rowspan leaves its label cell orphaned over unrelated rows."""
    if not ids:
        return '<p class="empty">Nothing in this category.</p>'
    body = []
    for rid in ids:
        fields = merged.by_row[rid]
        row = rows.get(rid)
        blocks = []
        for name, claims in sorted(fields.items()):
            hot = " collide" if (rid, name) in collisions else ""
            for sitting, value in claims:
                blocks.append(
                    f'<div class="claim{hot}">'
                    f'<div class="claim-head"><span class="field">{html.escape(name)}</span>'
                    f'<span class="sit">{html.escape(sitting.label)}</span></div>'
                    f"{_render_value(name, value, labels, row)}</div>"
                )
        search = " ".join(
            [rid, str((row or {}).get("surface_label", "")), str((row or {}).get("section", ""))]
            + [str(part) for part in ((row or {}).get("ancestor_path") or [])]
            + sorted(fields)
        ).lower()
        body.append(
            f'<tr data-search="{html.escape(search)}" '
            f'data-fields="{html.escape(" ".join(sorted(fields)))}">'
            f"<td>{_row_header(rid, rows)}</td>"
            f'<td>{"".join(blocks)}</td></tr>'
        )
    return (
        '<div class="tablewrap"><table><thead><tr><th>Input row</th>'
        "<th>What you recorded</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def render_consolidated(
    merged: Consolidated,
    packet: Mapping[str, Any],
    *,
    generated: str,
) -> str:
    """One self-contained page covering every sitting, its conflicts, and its orphaned work."""
    rows = row_index(packet)
    labels = label_index(packet)
    present = set(rows)
    collisions = set(merged.collisions)
    applicable = merged.rows_for(present, inside=True)
    orphans = merged.rows_for(present, inside=False)
    authored_total = sum(len(s.decisions) for s in merged.sittings)

    sittings_rows = "".join(
        f"<tr><td><span class=\"sit\">{html.escape(s.label)}</span></td>"
        f"<td class=\"mono\">{html.escape(s.packet_key)}</td>"
        f"<td>{len(s.decisions)}</td></tr>"
        for s in merged.sittings
    )
    collision_ids = sorted({rid for rid, _ in merged.collisions})
    lede = (
        "Every sitting agrees wherever two of them touched the same field, so there is nothing to "
        "reconcile before carrying on."
        if not collision_ids
        else f"{len(collision_ids)} row(s) below carry two different answers for the same field. "
        "Those are the only ones needing a decision before you continue."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>folio-resolve &mdash; consolidated adjudication review</title>
<style>{_STYLE}</style></head><body>
<header class="top">
  <h1>Consolidated adjudication review</h1>
  <div class="sub">Every saved sitting, merged by input row &middot; generated {html.escape(generated)}</div>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="n">{len(merged.sittings)}</div><div class="k">sittings</div></div>
    <div class="card"><div class="n">{authored_total}</div><div class="k">decisions you wrote</div></div>
    <div class="card good"><div class="n">{len(applicable)}</div><div class="k">rows still in packet</div></div>
    <div class="card warn"><div class="n">{len(orphans)}</div><div class="k">orphaned rows</div></div>
    <div class="card {"warn" if collision_ids else "good"}"><div class="n">{len(collision_ids)}</div>
      <div class="k">true collisions</div></div>
  </div>
  <p class="lede">{html.escape(lede)}</p>

  <div class="filterbar">
    <input id="q" type="search" placeholder="Filter by input label, path, section or field&hellip;"
           aria-label="Filter decisions">
    <select id="fieldFilter" aria-label="Filter by field">
      <option value="">All fields</option>
      <option value="pairing">pairing</option>
      <option value="gold">gold</option>
      <option value="pipeline">pipeline</option>
      <option value="added_mappings">added_mappings</option>
      <option value="note">note</option>
    </select>
    <span class="count" id="count"></span>
  </div>

  <h2>Sittings found</h2>
  <div style="overflow-x:auto"><table><thead><tr><th>Gold baseline</th><th>Packet key</th>
  <th>Authored</th></tr></thead><tbody>{sittings_rows}</tbody></table></div>

  <h2>Collisions &mdash; same row, same field, different answers</h2>
  {_table(collision_ids, merged, rows, labels, collisions=collisions)}

  <h2>Applicable now &mdash; rows the current packet still carries</h2>
  {_table(applicable, merged, rows, labels, collisions=collisions)}

  <h2>Orphaned &mdash; authored work whose row is gone from the current packet</h2>
  {_table(orphans, merged, rows, labels, collisions=collisions)}
</main>{_FILTER_JS}</body></html>
"""
