"""Render the audit-gate packet to a machine file and a self-contained decision sheet (U5, KTD9).

Two renderers live here, one per gold derivation:

* :func:`render_sheet` — the gold-v1 gate: five sections, one Accept/Reject/Edit per row.
* :func:`render_sheet_v2` — the gold-v2 gate in Damien's format (2026-07-28): rows rendered in
  taxonomy order and indented by input level, and **per-concept granular grading** — every gold
  concept keeps or goes on its own line, every pipeline candidate rises or does not, with a note
  under each block. Sections A-E: shared-row pairing, duplicate consistency, suspects, the
  resolution batch, new-gold candidates.

Two artefacts land in the gitignored packet directory (``audit_packet_v1/``, ``audit_packet_v2/``):

* ``packet.json`` — the machine form. :func:`folio_eval.audit.fold_decisions` reads it back so a
  decision made on the sheet folds against exactly the packet it was rendered from.
* ``sheet.html`` — one page Damien reviews in a single sitting. No external stylesheet, no CDN
  script, no web font: it opens from a file:// URL on a plane. Accept/Reject/Edit radios plus a
  note per row, and a **Copy decisions** button that assembles the decisions file into a
  *visible* ``<textarea readonly>`` and selects it before trying the clipboard APIs — the copy
  control must never dead-end when ``navigator.clipboard`` is refused (the ⌘C / Ctrl+C fallback
  is named on screen).

Both files carry firm surface strings, so both stay gitignored and are delivered privately
(KTD1/KTD9). The committed side of the gate is ``eval/reports/gold_decisions.jsonl``, which
:func:`folio_eval.audit.append_decisions` leak-scans before writing.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from .audit import SECTIONS_V2, Packet, PacketRow, SittingManifest, pairing_applied_reading_name
from .intake import DEFAULT_DATA_DIR
from .leakcheck import Manifest, scan_text

SittingLane = Literal["firm", "synthetic"]

SECTION_TITLES: Mapping[str, tuple[str, str]] = {
    "cascade": (
        "1 · The cascade / denominator decision",
        "Does inherited (cascaded) gold belong in the scored denominator? This one answer moves "
        "every F1 number measured so far, so it comes first.",
    ),
    "split": (
        "2 · Ratify the frozen / tune split (KTD4)",
        "The holdout as actually drawn. Ratifying seals it until the final report.",
    ),
    "suspect": (
        "3 · Gold suspects",
        "Rows where the workbook and the pipeline disagree, or where the curator's own note "
        "flagged uncertainty. Gold does not move until you accept.",
    ),
    "resolution": (
        "4 · Unresolved gold labels (R2 resolution batch)",
        "Gold cells that no exact / alternative / lemma lookup could resolve, with best-effort "
        "FOLIO proposals. Rows with no plausible concept are marked as coverage gaps.",
    ),
    "new_gold": (
        "5 · New-gold candidates (capped at 25, KTD5)",
        "Blank rows where the pipeline is confident. Accepted rows enter gold tagged "
        "provenance=pipeline_suggested, and every later report carries a sensitivity score "
        "computed without them.",
    ),
}

_STYLE = """
:root {
  --bg: #fbfaf7; --fg: #16150f; --muted: #5d5a4e; --line: #ddd8c8;
  --card: #ffffff; --accent: #7a4a1e; --good: #1f6b3a; --warn: #8a2f2f;
  --code: #f2efe6; --tag-bg: #e9e2d3; --selected-bg: #dbeafe; --selected-fg: #1e3a8a;
}
:root[data-theme="dark"] {
  --bg: #14150f; --fg: #eceadf; --muted: #b5b09f; --line: #45483a;
  --card: #1c1e16; --accent: #e5ad73; --good: #8dcca2; --warn: #ee9a9a;
  --code: #292c22; --tag-bg: #34382c; --selected-bg: #1e3a5f; --selected-fg: #dbeafe;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    --bg: #14150f; --fg: #eceadf; --muted: #b5b09f; --line: #45483a;
    --card: #1c1e16; --accent: #e5ad73; --good: #8dcca2; --warn: #ee9a9a;
    --code: #292c22; --tag-bg: #34382c; --selected-bg: #1e3a5f; --selected-fg: #dbeafe;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 16rem; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", serif;
}
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.7rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .3rem; border-bottom: 2px solid var(--line);
     padding-bottom: .3rem; }
h3 { font-size: .98rem; margin: 0 0 .35rem; }
p.lede, p.note { color: var(--muted); margin: .2rem 0 1rem; }
.meta { font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted);
        background: var(--code); padding: .6rem .8rem; border-radius: 6px; overflow-x: auto; }
.row { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
       padding: .9rem 1rem; margin: .8rem 0; }
.row header { display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline;
              justify-content: space-between; }
.tag { font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted);
       border: 1px solid var(--line); border-radius: 999px; padding: .12rem .58rem;
       background: var(--tag-bg); font-weight: 600; }
.tag.reason { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: .9rem; margin: .6rem 0; }
@media (max-width: 46rem) { .cols { grid-template-columns: 1fr; } }
.col h4 { margin: 0 0 .25rem; font-size: .82rem; text-transform: uppercase;
          letter-spacing: .06em; color: var(--muted); }
ul.iris { list-style: none; margin: 0; padding: 0; }
ul.iris li { padding: .25rem 0; border-bottom: 1px dotted var(--line); }
ul.iris li:last-child { border-bottom: 0; }
code, .mono { font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
.def { color: var(--muted); font-size: .86rem; display: block; }
.notes { background: var(--code); border-left: 3px solid var(--accent); padding: .5rem .7rem;
         margin: .5rem 0; font-size: .9rem; white-space: pre-wrap; }
.choices { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; margin-top: .7rem;
           padding-top: .6rem; border-top: 1px solid var(--line); }
.choices label { display: inline-flex; align-items: center; gap: .35rem; cursor: pointer; }
input[type=text], textarea { width: 100%; background: var(--bg); color: var(--fg);
  border: 1px solid var(--line); border-radius: 5px; padding: .4rem .55rem; font-size: .9rem;
  font-family: inherit; }
.edit { display: none; margin-top: .5rem; }
.row.editing .edit { display: block; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
table.scroll-wrap { min-width: 34rem; }
.tablewrap { overflow-x: auto; }
th, td { text-align: right; padding: .35rem .6rem; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
thead th { color: var(--muted); font-size: .8rem; font-weight: 600; }
.actions { position: sticky; bottom: 0; background: var(--bg); border-top: 2px solid var(--line);
           padding: .9rem 0 .4rem; margin-top: 2rem; }
button { font: inherit; padding: .5rem 1rem; border-radius: 6px; border: 1px solid var(--accent);
         background: var(--accent); color: var(--bg); cursor: pointer; }
button.secondary { background: transparent; color: var(--accent); }
#out { height: 7rem; margin-top: .6rem; }
#hint { color: var(--muted); font-size: .85rem; margin: .4rem 0 0; }
.overflow { color: var(--warn); }
"""

_SCRIPT = """
function collect() {
  const decisions = {};
  document.querySelectorAll('.row[data-decision-id]').forEach(function (row) {
    const id = row.getAttribute('data-decision-id');
    const picked = row.querySelector('input[type=radio]:checked');
    if (!picked) { return; }
    const entry = { action: picked.value };
    const edited = row.querySelector('input.edited');
    if (picked.value === 'edit' && edited && edited.value.trim()) {
      entry.edited_iris = edited.value.split(/[\\s,]+/).filter(Boolean);
    }
    const note = row.querySelector('textarea.note');
    if (note && note.value.trim()) { entry.note = note.value.trim(); }
    decisions[id] = entry;
  });
  return decisions;
}
function refresh() {
  const out = document.getElementById('out');
  out.value = JSON.stringify(collect(), null, 2);
  const n = Object.keys(collect()).length;
  document.getElementById('count').textContent = n + ' decided';
}
document.addEventListener('change', function (event) {
  const row = event.target.closest('.row');
  if (row && event.target.type === 'radio') {
    row.classList.toggle('editing', event.target.value === 'edit');
  }
  refresh();
});
document.addEventListener('input', function (event) {
  if (event.target.matches('textarea.note, input.edited')) { refresh(); }
});
document.getElementById('copy').addEventListener('click', function () {
  const out = document.getElementById('out');
  refresh();
  out.focus();
  out.select();
  out.setSelectionRange(0, out.value.length);
  const hint = document.getElementById('hint');
  const done = function () { hint.textContent = 'Copied. Paste it back into the chat.'; };
  const manual = function () {
    hint.textContent = 'Clipboard blocked — the text is selected above, press \\u2318C / Ctrl+C.';
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(out.value).then(done, function () {
      try { document.execCommand('copy') ? done() : manual(); } catch (e) { manual(); }
    });
    return;
  }
  try { document.execCommand('copy') ? done() : manual(); } catch (e) { manual(); }
});
refresh();
"""


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _short(iri: str) -> str:
    return iri.rsplit("/", 1)[-1] if "/" in iri else iri


def _iri_list(entries: Sequence[Mapping[str, object]], *, kind: str) -> str:
    if not entries:
        return '<p class="note">none</p>'
    items: list[str] = []
    for entry in entries:
        label = _esc(entry.get("label", ""))
        iri = _esc(_short(str(entry.get("iri", ""))))
        bits: list[str] = [f"<strong>{label}</strong> <code>{iri}</code>"]
        if kind == "gold" and entry.get("origin"):
            bits.append(f'<span class="tag">{_esc(entry["origin"])}</span>')
        if kind != "gold" and entry.get("score") is not None:
            probability = entry.get("probability")
            score = f"score {_esc(entry.get('score'))}"
            if probability is not None:
                score += f" · p={_esc(probability)}"
            bits.append(f'<span class="tag">{score}</span>')
        if kind != "gold" and entry.get("method"):
            bits.append(f'<span class="tag">{_esc(entry["method"])}</span>')
        line = " ".join(bits)
        definition = entry.get("definition")
        if definition:
            line += f'<span class="def">{_esc(definition)}</span>'
        items.append(f"<li>{line}</li>")
    return '<ul class="iris">' + "".join(items) + "</ul>"


def _variant_table(packet: Packet) -> str:
    if not packet.variants:
        return ""
    head = (
        "<thead><tr><th>variant</th><th>scored items</th><th>gold IRIs</th>"
        "<th>mean set size</th><th>tune P</th><th>tune R</th><th>tune F1</th></tr></thead>"
    )

    def _fmt(scores: Mapping[str, object], key: str) -> str:
        value = scores.get(key)
        return f"{float(value):.4f}" if isinstance(value, (int, float)) else "-"

    body: list[str] = []
    for entry in packet.variants:
        scores = packet.replay.get(entry.variant, {})
        body.append(
            "<tr>"
            f"<td><code>{_esc(entry.variant)}</code></td>"
            f"<td>{entry.items_scored}</td>"
            f"<td>{entry.gold_iris}</td>"
            f"<td>{entry.mean_set_size:.3f}</td>"
            f"<td>{_fmt(scores, 'precision')}</td><td>{_fmt(scores, 'recall')}</td>"
            f"<td>{_fmt(scores, 'f1')}</td>"
            "</tr>"
        )
    return (
        '<div class="tablewrap"><table class="scroll-wrap">'
        + head
        + "<tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _split_table(packet: Packet) -> str:
    if packet.split is None:
        return ""
    facts = packet.split.to_json()
    rows = "".join(
        f"<tr><td><code>{_esc(key)}</code></td><td>{_esc(value)}</td></tr>"
        for key, value in facts.items()
    )
    return f'<div class="tablewrap"><table class="scroll-wrap"><tbody>{rows}</tbody></table></div>'


def _choices(row: PacketRow) -> str:
    name = _esc(row.decision_id)
    labels = {"accept": "Accept", "reject": "Reject", "edit": "Edit"}
    if row.section == "cascade":
        labels = {"accept": "Keep v1 as-is", "reject": "own_only", "edit": "Other (note)"}
    if row.section == "split":
        labels = {"accept": "Ratify", "reject": "Re-draw", "edit": "Change a constraint (note)"}
    buttons = "".join(
        f'<label><input type="radio" name="{name}" value="{key}"> {labels[key]}</label>'
        for key in ("accept", "reject", "edit")
    )
    return (
        f'<div class="choices">{buttons}'
        f'<span class="tag">{_esc(row.decision_id)}</span></div>'
        '<div class="edit"><input type="text" class="edited" '
        'placeholder="edited IRIs, space- or comma-separated"></div>'
        '<textarea class="note" rows="2" placeholder="note (optional)"></textarea>'
    )


def _render_row(row: PacketRow) -> str:
    path = " &gt; ".join(_esc(part) for part in row.ancestor_path)
    header_bits = [f'<span class="tag reason">{_esc(row.reason_class)}</span>']
    if row.slice_name:
        header_bits.append(f'<span class="tag">{_esc(row.slice_name)}</span>')
    if row.item_id:
        header_bits.append(f'<span class="tag">{_esc(row.item_id)}</span>')
    if row.label_frequency > 1:
        header_bits.append(f'<span class="tag">label x{row.label_frequency}</span>')

    body: list[str] = []
    if path:
        body.append(f'<p class="note mono">{path}</p>')
    if row.section == "cascade":
        body.append(_variant_table_placeholder())
    if row.section == "split":
        body.append("__SPLIT_TABLE__")
    if row.gold or row.pipeline:
        gold_title = "Proposals" if row.section == "resolution" else "Pipeline says"
        body.append(
            '<div class="cols">'
            f'<div class="col"><h4>Gold says</h4>{_iri_list(row.gold, kind="gold")}</div>'
            f'<div class="col"><h4>{gold_title}</h4>'
            f"{_iri_list(row.pipeline, kind='pipeline')}</div>"
            "</div>"
        )
    if row.notes_text:
        body.append(f'<div class="notes">{_esc(row.notes_text)}</div>')
    if row.extra.get("occurrences"):
        body.append(f'<p class="note">appears in {_esc(row.extra["occurrences"])} gold cell(s)</p>')
    body.append(f'<p class="note">{_esc(row.suggested_action)}</p>')

    return (
        f'<article class="row" data-section="{_esc(row.section)}" '
        f'data-decision-id="{_esc(row.decision_id)}">'
        f"<header><h3>{_esc(row.surface_label)}</h3><div>{''.join(header_bits)}</div></header>"
        + "".join(body)
        + _choices(row)
        + "</article>"
    )


def _variant_table_placeholder() -> str:
    return "__VARIANT_TABLE__"


def render_sheet(packet: Packet) -> str:
    """The whole decision sheet as one self-contained HTML document."""
    meta = dict(packet.meta)
    sections: list[str] = []
    for name in ("cascade", "split", "suspect", "resolution", "new_gold"):
        title, lede = SECTION_TITLES[name]
        rows = packet.section(name)
        chunk = [f'<section data-section="{name}"><h2>{_esc(title)}</h2>']
        chunk.append(f'<p class="lede">{_esc(lede)}</p>')
        if name == "suspect" and packet.overflow:
            spilled = ", ".join(
                f"{_esc(reason)}: {count}" for reason, count in sorted(packet.overflow.items())
            )
            chunk.append(
                f'<p class="overflow">Beyond the {_esc(meta.get("suspect_cap", 50))}-row cap, '
                f"held for the next batch — {spilled}.</p>"
            )
        if not rows:
            chunk.append('<p class="note">nothing queued in this section.</p>')
        chunk.extend(_render_row(row) for row in rows)
        chunk.append("</section>")
        sections.append("".join(chunk))

    body = "".join(sections)
    body = body.replace("__VARIANT_TABLE__", _variant_table(packet))
    body = body.replace("__SPLIT_TABLE__", _split_table(packet))

    counts = json.dumps(dict(packet.counts), indent=2, sort_keys=True)
    header_meta = json.dumps(meta, indent=2, sort_keys=True)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>folio-resolve — audit gate (gold v1)</title>"
        f"<style>{_STYLE}</style></head><body><main>"
        "<h1>Audit gate — gold v1</h1>"
        '<p class="lede">Five sections, one sitting. Nothing here changes gold until the '
        "decisions below are folded in.</p>"
        f'<pre class="meta">{_esc(header_meta)}</pre>'
        f'<pre class="meta">{_esc(counts)}</pre>'
        f"{body}"
        '<div class="actions">'
        '<button id="copy" type="button">Copy decisions</button> '
        '<span class="tag" id="count">0 decided</span>'
        '<textarea id="out" readonly aria-label="assembled decisions JSON"></textarea>'
        '<p id="hint">Paste this back into the chat. If the copy button is blocked, the text '
        "above is already selected — press ⌘C / Ctrl+C.</p>"
        "</div></main>"
        f"<script>{_SCRIPT}</script></body></html>\n"
    )


# --------------------------------------------------------------------------------------
# Gold v2 — Damien's format: hierarchy rows, per-concept grading, five sections
# --------------------------------------------------------------------------------------

SECTION_TITLES_V2: Mapping[str, tuple[str, str]] = {
    "pairing": (
        "A · Shared-row pairing adjudications",
        "Rows where two input cells share a set of SALI outputs and the counts do not line up. "
        "The heuristic reading is applied today; pick the other one where it is wrong.",
    ),
    "consistency": (
        "B · Duplicate-consistency adjudications",
        "One input cell text, answered differently in different places. Gold is the union today; "
        "keep what belongs to this input and remove the rest.",
    ),
    "suspect": (
        "C · Gold suspects — graded concept by concept",
        "Curator-flagged rows and rows whose gold shares no word with the cell text. Each gold "
        "concept keeps or goes on its own; each pipeline candidate is elevated or not.",
    ),
    "resolution": (
        "D · Unresolved gold labels (R2 resolution batch)",
        "Gold cells no exact / alternative / lemma lookup could resolve, with best-effort FOLIO "
        "proposals. Elevate the one the label meant; leave all of them if FOLIO has no concept.",
    ),
    "new_gold": (
        "E · New-gold candidates (capped at 25, KTD5)",
        "Input cells with no curated mapping at all. Elevated concepts enter gold tagged "
        "provenance=pipeline_suggested, and every later report carries a score excluding them.",
    ),
    "improvement": (
        "F · Proposed gold improvements (pilot) — machine-proposed, for your confirmation",
        "Your six corrections follow one pattern: map the cell atomically, and break the molecule "
        "into the atoms it names — the industry, the asset, the player, the practice. This is that "
        "pattern run over the rest of the same practice family, by direct FOLIO label search plus "
        "your own examples as anchors. Every line here is a machine guess, gold only if you "
        "accept it.",
    ),
}

_STYLE_V2 = (
    _STYLE
    + """
.lvl { border-left: 3px solid var(--line); }
.lvl-1 { margin-left: 0; border-left-color: var(--accent); }
.lvl-2 { margin-left: 1.1rem; }
.lvl-3 { margin-left: 2.2rem; }
.lvl-4, .lvl-5, .lvl-6 { margin-left: 3.3rem; }
.groupbar { margin: 1.6rem 0 .4rem; font: 600 .78rem/1.4 ui-monospace, SFMono-Regular, Menlo,
            monospace; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.paths { margin: .2rem 0 .5rem; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
         color: var(--muted); }
.paths span { display: inline-block; border: 1px solid var(--line); border-radius: 4px;
              padding: 0 .35rem; margin: .1rem .25rem .1rem 0; }
.block { border: 1px solid var(--line); border-radius: 7px; padding: .55rem .7rem; margin: .6rem 0; }
.block > h4 { margin: 0 0 .4rem; font-size: .78rem; text-transform: uppercase;
              letter-spacing: .06em; color: var(--muted); display: flex; flex-wrap: wrap;
              gap: .4rem; align-items: baseline; }
.block > h4 .who { font-size: .7rem; letter-spacing: .04em; text-transform: none;
                   font-style: italic; }
.block.gold { border-left: 3px solid var(--good); }
.block.pipeline { border-left: 3px solid var(--accent); }
ul.grade { list-style: none; margin: 0; padding: 0; }
ul.grade > li { display: grid; grid-template-columns: 1fr minmax(11rem, auto); gap: .4rem .8rem;
                align-items: start; padding: .35rem 0; border-bottom: 1px dotted var(--line); }
ul.grade > li:last-child { border-bottom: 0; }
@media (max-width: 42rem) { ul.grade > li { grid-template-columns: 1fr; } }
.verdict { display: flex; flex-wrap: wrap; gap: .1rem .7rem; justify-content: flex-end; }
.verdict label { display: inline-flex; align-items: center; gap: .3rem; cursor: pointer;
                 font-size: .84rem; white-space: nowrap; }
.verdict label { border: 1px solid var(--line); border-radius: 999px; padding: .18rem .48rem;
                 background: var(--bg); }
.verdict label:has(input:checked) { font-weight: 700; box-shadow: 0 0 0 1px currentColor; }
.verdict label:has(input[value="keep"]:checked),
.verdict label:has(input[value="elevate"]:checked) { color: var(--good); border-color: var(--good); }
.verdict label:has(input[value="remove"]:checked),
.verdict label:has(input[value="not_gold"]:checked) { color: var(--warn); border-color: var(--warn); }
.prefilled { color: var(--good); font-size: .8rem; margin: .2rem 0 0; }
.tag.good { color: var(--good); border-color: var(--good); }
.pairing { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin: .5rem 0; }
@media (max-width: 46rem) { .pairing { grid-template-columns: 1fr; } }
.pairing .opt { border: 1px solid var(--line); border-radius: 7px; padding: .55rem .7rem; }
.pairing .opt h4 { margin: 0 0 .3rem; font-size: .82rem; }
.pairing .opt.picked { border-color: var(--accent); }
.metrics td.better { color: var(--good); font-weight: 600; }
.instance { font-size: .84rem; padding: .2rem 0; }
.blocks { font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); }

/* --- the three labelled panels every row carries, and the source-row grid ---------- */
.panel { border: 1px solid var(--line); border-radius: 7px; padding: .55rem .7rem; margin: .6rem 0;
         background: var(--card); }
.panel > h4 { margin: 0 0 .35rem; font-size: .78rem; text-transform: uppercase;
              letter-spacing: .06em; color: var(--muted); display: flex; flex-wrap: wrap;
              gap: .4rem; align-items: baseline; }
.panel > h4 .who { font-size: .7rem; letter-spacing: .04em; text-transform: none;
                   font-style: italic; }
.panel.source { border-left: 3px solid var(--muted); }
.panel.ref-gold { border-left: 3px solid var(--good); }
.panel.ref-pipe { border-left: 3px solid var(--accent); }
.panel.proposed { border-left: 3px solid var(--warn); background: var(--code); }
.panel.proposed > h4 { color: var(--warn); }
.perinput { margin: .3rem 0 .5rem; }
.perinput:last-child { margin-bottom: 0; }
.perinput > h5 { margin: 0 0 .2rem; font-size: .82rem; font-weight: 600; }
table.sheetgrid { border-collapse: collapse; font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo,
                  monospace; min-width: 100%; }
table.sheetgrid th, table.sheetgrid td { border: 1px solid var(--line); padding: .2rem .4rem;
                                         text-align: left; vertical-align: top;
                                         min-width: 6rem; max-width: 15rem;
                                         overflow-wrap: anywhere; }
table.sheetgrid thead th { background: var(--code); color: var(--muted); font-weight: 600; }
table.sheetgrid th.rownum { background: var(--code); color: var(--muted); text-align: right;
                            white-space: nowrap; min-width: 0; width: 1%; }
table.sheetgrid tbody td.filled { color: var(--fg); }
ul.iris li.committed { border-left: 3px solid var(--good); padding-left: .4rem; }
ul.iris li.tail { border-left: 3px solid transparent; padding-left: .4rem; color: var(--muted); }
.needseye { color: var(--warn); border-color: var(--warn); font-weight: 600; }
.banner { border: 1px solid var(--accent); border-left-width: 3px; border-radius: 7px;
          padding: .55rem .75rem; margin: .6rem 0 1rem; font-size: .9rem; }
.banner strong { display: block; margin-bottom: .2rem; }
.rownote { margin-top: .5rem; }

/* --- a pipe cell is N tags, never one comma-joined string (Damien, 2026-07-28) ------- */
.cellblock { margin: .25rem 0; font-size: .84rem; }
.cellblock .col { font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
                  color: var(--muted); }
.taglist { display: inline-flex; flex-wrap: wrap; gap: .25rem; vertical-align: middle; }
.taglist .concept { display: inline-flex; align-items: baseline; gap: .3rem;
                    border: 1px solid var(--line); border-radius: 4px; padding: 0 .35rem; }
.taglist .concept code { font-size: .72rem; color: var(--muted); }
.taglist .concept.unresolved { border-style: dashed; color: var(--muted); }
.pipeflag { color: var(--accent); border-color: var(--accent); }

/* --- rows already folded into a later gold version: pre-filled, badged, fully re-answerable --- */
.row.applied .panel.proposed { border-left-color: var(--good); background: var(--card); }
.folded { border: 1px solid var(--good); border-left-width: 3px; border-radius: 7px;
          padding: .5rem .7rem; margin: .5rem 0; font-size: .88rem; }
.folded h5 { margin: 0 0 .25rem; font-size: .78rem; text-transform: uppercase;
             letter-spacing: .06em; color: var(--good); }
.folded .yournote { margin: .3rem 0 0; white-space: pre-wrap; }
.tag.done { color: var(--good); border-color: var(--good); font-weight: 600; }
.machine { color: var(--warn); border-color: var(--warn); }
.opt.applied-reading { border-color: var(--good); background: var(--code); margin-bottom: .6rem; }
.opt.applied-reading h4 { color: var(--good); display: flex; gap: .5rem; align-items: baseline; }
p.note.workbook { font-size: .78rem; opacity: .85; }

/* --- durable visual adjudication workspace ------------------------------------------ */
body.eval-workspace { padding: 0; overflow: hidden; font-family: Inter, ui-sans-serif, system-ui,
                      -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.eval-workspace main { max-width: none; height: 100vh; display: flex; flex-direction: column; }
.review-header { flex: 0 0 auto; border-bottom: 1px solid var(--line); background: var(--card); }
.review-titlebar { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
                   padding: .75rem 1rem .55rem; }
.review-titlebar h1 { font-size: 1.05rem; margin: 0; }
.review-titlebar .lede { margin: .1rem 0 0; font-size: .78rem; }
.review-progress { display: flex; align-items: center; gap: .55rem; white-space: nowrap; }
.progress-track { width: 9rem; height: .4rem; overflow: hidden; border-radius: 999px;
                  background: var(--line); }
.progress-fill { height: 100%; width: 0; background: var(--good); transition: width .18s ease; }
.review-toolbar { display: flex; align-items: center; gap: .5rem; padding: .5rem 1rem;
                  border-top: 1px solid var(--line); background: var(--bg); }
.review-toolbar input, .review-toolbar select { width: auto; min-width: 10rem; height: 2rem;
  border: 1px solid var(--line); border-radius: 6px; background: var(--card); color: var(--fg);
  padding: .25rem .55rem; font: inherit; font-size: .78rem; }
.review-toolbar input { flex: 1; max-width: 28rem; }
.review-toolbar button { padding: .34rem .65rem; font-size: .76rem; }
.review-toolbar .spacer { flex: 1; }
.draft-state { font-size: .74rem; color: var(--muted); }
.workspace-shell { --sidebar-width: 19rem; position: relative; display: grid;
                   grid-template-columns: var(--sidebar-width) .35rem minmax(0, 1fr);
                   min-height: 0; flex: 1; background: var(--bg); }
.review-sidebar { min-height: 0; overflow: auto; border-right: 1px solid var(--line);
                  background: var(--card); padding: .65rem; z-index: 2; }
.sidebar-label { margin: .1rem .35rem .55rem; color: var(--muted); font-size: .68rem;
                 font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.nav-group { margin: 0 0 .85rem; }
.nav-group-title { display: flex; justify-content: space-between; gap: .5rem; margin: .25rem .35rem;
                   color: var(--muted); font-size: .68rem; font-weight: 700; text-transform: uppercase; }
.review-item { width: 100%; display: grid; grid-template-columns: 1fr auto; gap: .25rem .45rem;
               margin: .12rem 0; padding: .46rem .55rem; border: 1px solid transparent;
               border-radius: 6px; background: transparent; color: var(--fg); text-align: left;
               line-height: 1.25; cursor: pointer; }
.review-item:hover { background: var(--code); }
.review-item.active { border-color: #60a5fa; background: var(--selected-bg); color: var(--selected-fg); }
.review-item[data-decided="true"] .item-state { color: var(--good); }
.review-item[data-needs-eye="true"] .item-state { color: var(--warn); }
.review-item[hidden] { display: none; }
.item-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              font-size: .8rem; font-weight: 600; }
.item-path { grid-column: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
             white-space: nowrap; color: var(--muted); font-size: .65rem; }
.item-state { grid-column: 2; grid-row: 1 / 3; align-self: center; font-size: .68rem; }
.review-item.level-2 { padding-left: 1rem; }
.review-item.level-3, .review-item.level-4 { padding-left: 1.45rem; }
.pane-resizer { position: relative; z-index: 8; cursor: col-resize; background: var(--line); }
.pane-resizer:hover, .pane-resizer.dragging { background: #60a5fa; }
#mapping-lines { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 4;
                 pointer-events: none; overflow: visible; }
.review-stage { position: relative; min-width: 0; min-height: 0; overflow: hidden; background: var(--card); }
.review-stage > .row { display: none; }
.review-stage > .row.active { --level-width: 18rem; --mapping-width: 29rem; display: grid;
                              grid-template-columns: var(--level-width) .35rem var(--mapping-width) .35rem minmax(24rem, 1fr);
                              grid-template-rows: auto minmax(0, 1fr); height: 100%; margin: 0;
                              padding: 0; border: 0; border-radius: 0; }
.review-stage > .row > header { grid-column: 1 / 6; padding: .7rem 1rem; border-bottom: 1px solid var(--line);
                               background: var(--bg); min-width: 0; }
.review-stage > .row > header h3 { min-width: 0; overflow: hidden; text-overflow: ellipsis;
                                  white-space: nowrap; font-size: .92rem; }
.level-pane, .mapping-pane, .detail-pane { min-width: 0; min-height: 0; overflow: auto; padding: .8rem; }
.level-pane { background: var(--bg); border-right: 1px solid var(--line); }
.level-filter-all { width: 100%; margin: 0 0 .55rem; }
.level-node { position: relative; margin: 0 0 .65rem; padding: .65rem; border: 1px solid var(--line);
              border-radius: 7px; background: var(--card); }
.level-filter-button { display: block; width: 100%; padding: 0; border: 0; background: transparent;
                       color: inherit; text-align: left; cursor: pointer; }
.level-node:has(.level-filter-button:hover), .level-node:has(.level-filter-button.level-filter-active) {
  border-color: #60a5fa; background: var(--selected-bg); }
.level-node strong { display: block; font-size: .82rem; }
.level-node .level-number { color: var(--muted); font-size: .66rem; font-weight: 700; }
.level-node textarea { margin-top: .45rem; min-height: 3.25rem; }
.mapping-pane { border-right: 1px solid var(--line); background: var(--card); }
.detail-pane { background: var(--bg); }
.pane-heading { margin: 0 0 .55rem; font-size: .68rem; font-weight: 700; letter-spacing: .08em;
                text-transform: uppercase; color: var(--muted); }
.mapping-pane .panel.proposed { margin: 0; background: transparent; border: 0; padding: 0; }
.mapping-pane .panel.proposed > h4 { display: none; }
.mapping-pane .block { margin: 0 0 .7rem; background: var(--card); }
/* One output per line: checkbox, rdfs:label, level chips. Nothing is disclosed behind a
   chevron, so the pane scans top-to-bottom and fits roughly three times as many rows. */
.mapping-pane ul.grade { border-top: 1px solid var(--line); }
/* The base grade list reserves a second column for the verdict; a one-line row has no second
   column, so it would sit on ~55% of the pane and truncate the label to nothing. */
.mapping-pane ul.grade > li { display: block; position: relative; border: 1px solid transparent;
                              border-bottom-color: var(--line); border-radius: 4px;
                              padding: 0 .3rem; cursor: pointer; }
.mapping-pane ul.grade > li[hidden] { display: none; }
.concept-row-head { display: flex; align-items: center; gap: .4rem; min-width: 0;
                    min-height: 1.7rem; }
.verdict-toggle { display: inline-flex; flex: 0 0 auto; align-items: center; cursor: pointer;
                  padding: .25rem .1rem; }
.verdict-toggle input { width: .95rem; height: .95rem; margin: 0; cursor: pointer;
                        accent-color: var(--good); }
.block.pipeline .verdict-toggle input { accent-color: #2563eb; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0);
           clip-path: inset(50%); white-space: nowrap; }
.concept-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
                 white-space: nowrap; font-size: .82rem; font-weight: 600; }
.concept-row-head > .tag { flex: 0 0 auto; }
/* Struck out only in the gold block, where unchecking really is a deletion. An un-elevated
   pipeline candidate is the resting state, so it merely recedes -- striking all of them would
   paint the whole block as rejected before the reviewer has decided anything. */
.block.gold ul.grade > li:has(input[data-verdict]:not(:checked)) .concept-label {
  color: var(--muted); font-weight: 500; text-decoration: line-through;
  text-decoration-thickness: 1px; }
.block.pipeline ul.grade > li:has(input[data-verdict]:not(:checked)) .concept-label {
  color: var(--muted); font-weight: 500; }
.mapping-pane ul.grade > li:hover, .mapping-pane ul.grade > li.concept-selected {
  border-color: #60a5fa; background: var(--selected-bg); color: var(--selected-fg); }
.remove-mapping { flex: 0 0 auto; padding: .1rem .3rem; border-radius: 4px; font-size: .68rem;
                  line-height: 1.2; opacity: .35; }
.mapping-pane ul.grade > li:hover .remove-mapping,
.mapping-pane ul.grade > li.concept-selected .remove-mapping,
.mapping-pane ul.grade > li.mapping-removed .remove-mapping { opacity: 1; }
.mapping-pane .pairing { grid-template-columns: 1fr; }
.mapping-pane .taglist .concept { cursor: pointer; }
.mapping-pane .taglist .concept:hover, .mapping-pane .taglist .concept.concept-selected {
  border-color: #60a5fa; background: var(--selected-bg); color: var(--selected-fg); }
.mapping-pane .row-note { margin-top: .8rem; }
/* Level chips sit on the same line as the label: the checkbox itself is the chip, so an
   assignment is one glance and one click instead of an expand-then-read. */
.level-choices { display: inline-flex; flex: 0 0 auto; align-items: center; gap: .12rem; }
.level-choice { position: relative; display: inline-flex; align-items: center; justify-content: center;
                min-width: 1.45rem; padding: .12rem .22rem; border: 1px solid var(--line);
                border-radius: 4px; background: var(--tag-bg); color: var(--muted);
                font: 700 .6rem/1.35 ui-monospace, monospace; cursor: pointer; }
.level-choice input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0;
                      opacity: 0; cursor: pointer; }
.level-choice:has(input:checked) { border-color: var(--good); background: var(--good);
                                   color: #fff; }
.block.pipeline .level-choice:has(input:checked) { border-color: #2563eb; background: #2563eb; }
.level-choice:has(input:focus-visible) { outline: 2px solid #60a5fa; outline-offset: 1px; }
.unassigned-choice:has(input:checked) { border-color: var(--muted); background: var(--muted); }
.mapping-state { color: var(--muted); font-size: .67rem; }
.mapping-removed { opacity: .5; }
.mapping-removed .concept-label { text-decoration: line-through; }
.mapping-removed .remove-mapping { opacity: 1; font-weight: 700; color: var(--good);
                                   border-color: var(--good); }
.add-concept { margin: .75rem 0; padding: .65rem; border: 1px dashed var(--line); border-radius: 7px; }
.add-concept-fields { display: grid; grid-template-columns: minmax(8rem, 1fr) minmax(12rem, 2fr) auto;
                      gap: .35rem; margin-top: .4rem; }
.add-concept input { min-width: 0; }
.mapping-pane .mark-reviewed { position: sticky; z-index: 5; bottom: .15rem; width: 100%;
  margin: .65rem 0 .2rem; padding: .7rem; border-color: var(--good); background: var(--good);
  color: white; font-weight: 750; box-shadow: 0 -4px 12px rgb(15 23 42 / .12); }
.mapping-pane .mark-reviewed:hover { filter: brightness(1.08); }
.concept-inspector { position: sticky; z-index: 3; top: 0; width: auto;
                     max-height: 9.5rem; overflow: auto; border: 1px solid #93c5fd;
                     border-left: 4px solid #3b82f6; border-radius: 7px; background: var(--card);
                     padding: .65rem .8rem; box-shadow: 0 4px 14px rgb(15 23 42 / .08); }
.concept-inspector h4 { margin: 0 0 .2rem; font-size: .9rem; }
.concept-inspector .inspector-meta { color: var(--muted); font: .68rem/1.4 ui-monospace, monospace; }
.concept-inspector .inspector-definition { margin: .45rem 0 0; font-size: .8rem; }
.detail-pane { padding-top: .8rem; }
.detail-pane .panel { margin-top: 0; }
.actions { position: static; margin: .8rem 0 0; padding: .75rem; border: 1px solid var(--line);
           border-radius: 7px; background: var(--card); }
#out { height: 5rem; }
.empty-review { display: none; padding: 3rem; color: var(--muted); text-align: center; }
.review-stage.empty .empty-review { display: block; }
@media (max-width: 78rem) {
  .workspace-shell { --sidebar-width: 16rem; }
}
@media (max-width: 58rem) {
  body.eval-workspace { overflow: auto; }
  .eval-workspace main { min-height: 100vh; height: auto; }
  .workspace-shell { grid-template-columns: 14rem minmax(0, 1fr); min-height: 44rem; }
  .workspace-shell > .pane-resizer, #mapping-lines { display: none; }
  .review-stage > .row.active { display: block; height: auto; }
  .concept-inspector { position: static; width: auto; margin: 0 0 .8rem; max-height: none; }
  .detail-pane { padding-top: .8rem; }
  .level-pane, .mapping-pane { border-right: 0; border-bottom: 1px solid var(--line); }
  .review-stage > .row.active > .pane-resizer { display: none; }
}
"""
)

_SCRIPT_V2 = """
// A concept's verdict is one checkbox: checked emits data-on ("keep"/"elevate"), unchecked emits
// data-off ("remove"/"not_gold"). The radio branch stays for the pairing block, which is still a
// one-of-many choice and cannot collapse to a single toggle.
function verdictValue(li) {
  const toggle = li.querySelector('input[data-verdict]');
  if (toggle) { return toggle.checked ? toggle.dataset.on : toggle.dataset.off; }
  const picked = li.querySelector('input[type=radio]:checked');
  return picked ? picked.value : '';
}
function setVerdictValue(li, value) {
  const toggle = li.querySelector('input[data-verdict]');
  if (toggle) { toggle.checked = value === toggle.dataset.on; return; }
  const radio = li.querySelector('input[type=radio][value="' + CSS.escape(value) + '"]');
  if (radio) { radio.checked = true; }
}
function verdictExcluded(li) {
  const value = verdictValue(li);
  return value === 'remove' || value === 'not_gold';
}
function verdicts(row, selector) {
  const out = {};
  row.querySelectorAll(selector + ' li[data-iri]').forEach(function (li) {
    const value = verdictValue(li);
    if (value) { out[li.getAttribute('data-iri')] = value; }
  });
  return out;
}
const baselineCache = new WeakMap();
// A row already folded into gold carries its live state as a data-baseline JSON attribute
// (gold/pipeline verdicts, the pairing reading, and the three note fields). No input is ever
// disabled -- every row stays fully answerable -- but a re-submission that leaves the row exactly
// at its baseline diffs to nothing here, so an untouched folded row never re-enters the
// Copy-decisions JSON and a genuine amendment is the only entry that survives (Damien, 2026-07-28:
// "let me add notes and change items even where you think things are settled").
function baselineOf(row) {
  if (baselineCache.has(row)) { return baselineCache.get(row); }
  const raw = row.getAttribute('data-baseline');
  if (!raw) { baselineCache.set(row, null); return null; }
  try {
    const baseline = JSON.parse(raw);
    baselineCache.set(row, baseline);
    return baseline;
  } catch (e) {
    baselineCache.set(row, null);
    return null;
  }
}
function diffMap(current, base) {
  const out = {};
  Object.keys(current).forEach(function (key) {
    if (!base || base[key] !== current[key]) { out[key] = current[key]; }
  });
  return out;
}
function sameJson(first, second) {
  return JSON.stringify(first || {}) === JSON.stringify(second || {});
}
function collect() {
  const decisions = {};
  document.querySelectorAll('.row[data-decision-id]').forEach(function (row) {
    const id = row.getAttribute('data-decision-id');
    const baseline = baselineOf(row);
    const entry = {};
    const gold = diffMap(verdicts(row, '.block.gold'), baseline ? baseline.gold : null);
    if (Object.keys(gold).length) { entry.gold = gold; }
    const pipeline = diffMap(verdicts(row, '.block.pipeline'), baseline ? baseline.pipeline : null);
    if (Object.keys(pipeline).length) { entry.pipeline = pipeline; }
    const pairingEl = row.querySelector('input[data-kind=pairing]:checked');
    const pairing = pairingEl ? pairingEl.value : null;
    if (pairing && (!baseline || pairing !== baseline.pairing)) { entry.pairing = pairing; }
    const rowNote = row.querySelector('textarea.row-note');
    const rowNoteVal = rowNote ? rowNote.value.trim() : '';
    if (rowNoteVal && (!baseline || rowNoteVal !== (baseline.note || ''))) {
      entry.note = rowNoteVal;
    }
    const goldNote = row.querySelector('textarea.gold-note');
    const goldNoteVal = goldNote ? goldNote.value.trim() : '';
    if (goldNoteVal && (!baseline || goldNoteVal !== (baseline.gold_note || ''))) {
      entry.gold_note = goldNoteVal;
    }
    const pipeNote = row.querySelector('textarea.pipeline-note');
    const pipeNoteVal = pipeNote ? pipeNote.value.trim() : '';
    if (pipeNoteVal && (!baseline || pipeNoteVal !== (baseline.pipeline_note || ''))) {
      entry.pipeline_note = pipeNoteVal;
    }
    const levelMappings = {};
    row.querySelectorAll('input[data-level-map]:checked').forEach(function (input) {
      const level = input.value;
      if (!levelMappings[level]) { levelMappings[level] = []; }
      levelMappings[level].push(input.dataset.iri);
    });
    if (Object.keys(levelMappings).length && (!baseline || !sameJson(levelMappings, baseline.level_mappings))) {
      entry.level_mappings = levelMappings;
    }
    const levelNotes = {};
    row.querySelectorAll('textarea.level-note').forEach(function (field) {
      const value = field.value.trim();
      const level = field.closest('[data-level-id]').dataset.levelId;
      if (value) { levelNotes[level] = value; }
    });
    if (Object.keys(levelNotes).length && (!baseline || !sameJson(levelNotes, baseline.level_notes))) {
      entry.level_notes = levelNotes;
    }
    const unassigned = Array.from(row.querySelectorAll('input[data-unassigned]:checked'))
      .map(function (input) { return input.dataset.iri; });
    const mappingOptions = unassigned.length ? {unassigned: unassigned} : {};
    if (unassigned.length && (!baseline || !sameJson(mappingOptions, baseline.mapping_options))) {
      entry.mapping_options = mappingOptions;
    }
    const addedMappings = Array.from(row.querySelectorAll('li[data-added="true"]')).map(function (li) {
      return {iri: li.dataset.iri, label: li.dataset.label};
    });
    if (addedMappings.length && (!baseline || !sameJson(addedMappings, baseline.added_mappings))) {
      entry.added_mappings = addedMappings;
    }
    const nav = document.querySelector('.review-item[data-target="' + CSS.escape(id) + '"]');
    if (nav && nav.dataset.reviewed === 'true') { entry.reviewed = true; }
    if (Object.keys(entry).length) { decisions[id] = entry; }
  });
  return decisions;
}
function refresh(decisions) {
  decisions = decisions || collect();
  document.getElementById('out').value = JSON.stringify(decisions, null, 2);
  document.getElementById('count').textContent = Object.keys(decisions).length + ' rows decided';
  return decisions;
}
"""

_SCRIPT_V2_WORKSPACE = (
    _SCRIPT_V2
    + """
const workspace = document.querySelector('[data-review-workspace="folio-eval-v1"]');
const shell = document.querySelector('.workspace-shell');
const stage = document.querySelector('.review-stage');
const rows = Array.from(document.querySelectorAll('.review-stage .row[data-decision-id]'));
const navItems = Array.from(document.querySelectorAll('.review-item[data-target]'));
const rowsById = new Map(rows.map(function (row) {
  return [row.getAttribute('data-decision-id'), row];
}));
const navByDecisionId = new Map(navItems.map(function (item) {
  return [item.getAttribute('data-target'), item];
}));
const draftKey = 'folio-eval-draft:' + workspace.getAttribute('data-packet-key');
let activeId = null;
let mappingFrame = 0;
let draftTimer = 0;
const themeKey = 'folio-eval-theme';

function setTheme(theme) {
  const selected = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = selected;
  const toggle = document.getElementById('theme-toggle');
  if (toggle) { toggle.textContent = selected === 'dark' ? 'Light mode' : 'Dark mode'; }
  try { localStorage.setItem(themeKey, selected); } catch (error) { /* preference is optional */ }
  scheduleMappingLines();
}
function restoreTheme() {
  let selected = 'light';
  try { selected = localStorage.getItem(themeKey) || 'light'; } catch (error) { /* stay light */ }
  setTheme(selected);
}

function rowById(id) { return rowsById.get(id); }
function navById(id) { return navByDecisionId.get(id); }
function setRadio(row, selector, value) {
  const input = row.querySelector(selector + ' input[value="' + CSS.escape(value) + '"]');
  if (input) { input.checked = true; }
}
function addConceptRow(row, label, iri) {
  const existing = row.querySelector('li[data-iri="' + CSS.escape(iri) + '"]');
  if (existing) { return existing; }
  let list = row.querySelector('.block.gold ul.grade');
  if (!list) {
    list = document.createElement('ul'); list.className = 'grade';
    const empty = row.querySelector('.block.gold p.note');
    if (empty) { empty.replaceWith(list); } else { row.querySelector('.block.gold').appendChild(list); }
  }
  const li = document.createElement('li');
  li.dataset.iri = iri; li.dataset.label = label; li.dataset.kind = 'gold'; li.dataset.added = 'true';
  const head = document.createElement('div'); head.className = 'concept-row-head';
  const toggleLabel = document.createElement('label'); toggleLabel.className = 'verdict-toggle';
  toggleLabel.title = 'Checked = Keep gold · unchecked = Remove from gold';
  const toggle = document.createElement('input'); toggle.type = 'checkbox';
  toggle.dataset.verdict = ''; toggle.dataset.on = 'keep'; toggle.dataset.off = 'remove';
  toggle.name = 'gold|' + row.dataset.decisionId + '|added|' + iri; toggle.checked = true;
  const toggleText = document.createElement('span'); toggleText.className = 'sr-only';
  toggleText.textContent = 'Keep gold';
  toggleLabel.append(toggle, toggleText); head.appendChild(toggleLabel);
  const title = document.createElement('span'); title.className = 'concept-label';
  title.textContent = label; title.title = label; head.appendChild(title);
  const choices = document.createElement('span'); choices.className = 'level-choices';
  choices.dataset.mappingState = 'New mapping · choose a level';
  choices.title = choices.dataset.mappingState;
  row.querySelectorAll('.level-node').forEach(function (node) {
    const choice = document.createElement('label'); choice.className = 'level-choice';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
    checkbox.dataset.levelMap = ''; checkbox.dataset.iri = iri; checkbox.value = node.dataset.levelId;
    const levelLabel = node.querySelector('strong')?.textContent || '';
    choice.title = node.dataset.levelId + ' · ' + levelLabel;
    const chip = document.createElement('span'); chip.textContent = node.dataset.levelId;
    choice.append(checkbox, chip); choices.appendChild(choice);
  });
  const unassignedChoice = document.createElement('label');
  unassignedChoice.className = 'level-choice unassigned-choice';
  unassignedChoice.title = 'Unassigned — maps to no input level';
  const unassigned = document.createElement('input'); unassigned.type = 'checkbox';
  unassigned.dataset.unassigned = ''; unassigned.dataset.iri = iri;
  const unassignedChip = document.createElement('span'); unassignedChip.textContent = '—';
  unassignedChoice.append(unassigned, unassignedChip); choices.appendChild(unassignedChoice);
  head.appendChild(choices);
  const remove = document.createElement('button'); remove.className = 'secondary remove-mapping';
  remove.type = 'button'; remove.textContent = '✕';
  remove.title = 'Remove this mapping'; remove.setAttribute('aria-label', 'Remove this mapping');
  head.appendChild(remove);
  li.appendChild(head); list.appendChild(li); return li;
}
function applyDecision(id, decision) {
  const row = rowById(id);
  if (!row || !decision || typeof decision !== 'object') { return; }
  if (Array.isArray(decision.added_mappings)) {
    decision.added_mappings.forEach(function (mapping) {
      if (mapping && mapping.iri && mapping.label) { addConceptRow(row, mapping.label, mapping.iri); }
    });
  }
  ['gold', 'pipeline'].forEach(function (kind) {
    const verdicts = decision[kind];
    if (!verdicts || typeof verdicts !== 'object') { return; }
    Object.keys(verdicts).forEach(function (iri) {
      const li = row.querySelector('.block.' + kind + ' li[data-iri="' + CSS.escape(iri) + '"]');
      if (li) { setVerdictValue(li, verdicts[iri]); }
    });
  });
  if (decision.pairing) { setRadio(row, '.pairing', decision.pairing); }
  [['note', '.row-note'], ['gold_note', '.gold-note'], ['pipeline_note', '.pipeline-note']]
    .forEach(function (pair) {
      const field = row.querySelector(pair[1]);
      if (field && typeof decision[pair[0]] === 'string') { field.value = decision[pair[0]]; }
    });
  if (decision.level_mappings && typeof decision.level_mappings === 'object') {
    row.querySelectorAll('input[data-level-map], input[data-unassigned]').forEach(function (field) {
      field.checked = false;
    });
    Object.keys(decision.level_mappings).forEach(function (level) {
      decision.level_mappings[level].forEach(function (iri) {
        const field = row.querySelector('input[data-level-map][value="' + CSS.escape(level)
          + '"][data-iri="' + CSS.escape(iri) + '"]');
        if (field) { field.checked = true; }
      });
    });
  }
  if (decision.level_notes && typeof decision.level_notes === 'object') {
    Object.keys(decision.level_notes).forEach(function (level) {
      const field = row.querySelector('[data-level-id="' + CSS.escape(level) + '"] .level-note');
      if (field) { field.value = decision.level_notes[level]; }
    });
  }
  if (decision.mapping_options && Array.isArray(decision.mapping_options.unassigned)) {
    decision.mapping_options.unassigned.forEach(function (iri) {
      const field = row.querySelector('input[data-unassigned][data-iri="' + CSS.escape(iri) + '"]');
      if (field) { field.checked = true; }
    });
  }
  row.querySelectorAll('.mapping-pane li[data-iri]').forEach(refreshMappingSummary);
}
function rowComplete(row) {
  // A checkbox verdict is answered by construction, so only the pairing radios can still be
  // outstanding; a row with neither kind of control has nothing to confirm.
  const radios = Array.from(row.querySelectorAll('input[type=radio][name]'));
  const toggles = Array.from(row.querySelectorAll('input[data-verdict]'));
  if (!radios.length && !toggles.length) { return false; }
  const groups = new Set(radios.map(function (input) { return input.name; }));
  const verdictsComplete = Array.from(groups).every(function (name) {
    return radios.some(function (input) { return input.name === name && input.checked; });
  });
  const mappingsComplete = Array.from(row.querySelectorAll('.mapping-pane li[data-iri]')).every(function (li) {
    return verdictExcluded(li) || li.classList.contains('mapping-removed')
      || Boolean(li.querySelector('input[data-level-map]:checked, input[data-unassigned]:checked'));
  });
  return verdictsComplete && mappingsComplete;
}
function restoreDraft() {
  try {
    const raw = localStorage.getItem(draftKey);
    if (!raw) { return; }
    const saved = JSON.parse(raw);
    const decisions = saved.decisions && typeof saved.decisions === 'object' ? saved.decisions : saved;
    Object.keys(decisions).forEach(function (id) { applyDecision(id, decisions[id]); });
    const reviewedIds = saved.version >= 3 && Array.isArray(saved.reviewedIds)
      ? saved.reviewedIds : [];
    reviewedIds.forEach(function (id) {
      const nav = navById(id);
      const row = rowById(id);
      if (nav && row && rowComplete(row)) { nav.dataset.reviewed = 'true'; }
    });
    document.getElementById('draft-state').textContent = 'Draft restored from this browser';
  } catch (error) {
    document.getElementById('draft-state').textContent = 'Draft could not be restored';
  }
}
function persistDraft(decisions) {
  try {
    const reviewedIds = navItems.filter(function (item) { return item.dataset.reviewed === 'true'; })
      .map(function (item) { return item.getAttribute('data-target'); });
    localStorage.setItem(draftKey, JSON.stringify({
      version: 3, decisions: decisions || collect(), reviewedIds: reviewedIds
    }));
    document.getElementById('draft-state').textContent = 'Draft saved in this browser';
  } catch (error) {
    document.getElementById('draft-state').textContent = 'Local draft unavailable — download often';
  }
}
function scheduleDraft(decisions) {
  window.clearTimeout(draftTimer);
  draftTimer = window.setTimeout(function () { persistDraft(decisions); }, 250);
}
function flushDraft(decisions) {
  window.clearTimeout(draftTimer);
  persistDraft(decisions || collect());
}
function updateProgress() {
  let decided = 0;
  navItems.forEach(function (item) {
    const isDecided = item.dataset.reviewed === 'true' || item.dataset.decided === 'true';
    item.dataset.currentlyDecided = String(isDecided);
    const state = item.querySelector('.item-state');
    const row = rowById(item.getAttribute('data-target'));
    const confirm = row ? row.querySelector('.mark-reviewed') : null;
    if (isDecided) {
      decided += 1;
      if (state) { state.textContent = '\u2713'; }
    }
    if (confirm) { confirm.textContent = isDecided ? 'Reviewed \u2713' : 'Mark reviewed & continue'; }
  });
  const total = navItems.length;
  document.getElementById('progress-count').textContent = decided + ' / ' + total + ' reviewed';
  document.getElementById('progress-fill').style.width = (total ? decided / total * 100 : 0) + '%';
}
function showConcept(li) {
  const activeRow = rowById(activeId);
  const inspector = activeRow ? activeRow.querySelector('.concept-inspector') : null;
  if (!inspector) { return; }
  document.querySelectorAll('.concept-selected').forEach(function (entry) {
    entry.classList.remove('concept-selected');
  });
  if (!li) {
    inspector.innerHTML = '<h4>Select a mapped concept</h4><p class="note">Its definition, source, and current decision will appear here.</p>';
    return;
  }
  li.classList.add('concept-selected');
  const verdict = verdictValue(li);
  inspector.innerHTML = '';
  const title = document.createElement('h4');
  title.textContent = li.dataset.label || 'Unnamed concept';
  const meta = document.createElement('div');
  meta.className = 'inspector-meta';
  const iri = li.dataset.iri || li.dataset.conceptIri || '';
  const source = li.dataset.kind === 'gold' ? 'Current gold'
    : (li.dataset.kind === 'pipeline' ? 'Pipeline candidate' : 'Pairing candidate');
  // Everything the one-line row cannot show without crowding out the label lands here instead.
  const bits = [source, iri];
  if (li.dataset.branch) { bits.push(li.dataset.branch); }
  if (li.dataset.column) { bits.push(li.dataset.column); }
  if (li.dataset.score) {
    bits.push('score ' + li.dataset.score
      + (li.dataset.probability ? ' · p=' + li.dataset.probability : ''));
  }
  if (li.dataset.path) { bits.push(li.dataset.path); }
  const levelState = li.querySelector('.level-choices');
  if (levelState && levelState.dataset.mappingState) { bits.push(levelState.dataset.mappingState); }
  if (verdict) { bits.push('decision: ' + verdict); }
  meta.textContent = bits.filter(Boolean).join(' · ');
  const definition = document.createElement('p');
  definition.className = 'inspector-definition';
  definition.textContent = li.dataset.definition || 'No definition was included in this audit packet.';
  inspector.append(title, meta, definition);
}
function drawMappingLines() {
  const svg = document.getElementById('mapping-lines');
  const row = rowById(activeId);
  if (!svg || !row || window.matchMedia('(max-width: 58rem)').matches) {
    if (svg) { svg.innerHTML = ''; }
    return;
  }
  const bounds = shell.getBoundingClientRect();
  let paths = '';
  row.querySelectorAll('.mapping-pane li[data-iri]:not([hidden]) input[data-level-map]:checked').forEach(function (input) {
    const startNode = row.querySelector('[data-level-id="' + CSS.escape(input.value) + '"]');
    const target = input.closest('li[data-iri]');
    if (!startNode || !target) { return; }
    const start = startNode.getBoundingClientRect();
    const end = target.getBoundingClientRect();
    const startX = start.right - bounds.left;
    const startY = start.top + start.height / 2 - bounds.top;
    const endX = end.left - bounds.left;
    const endY = end.top + end.height / 2 - bounds.top;
    const kind = target.dataset.kind;
    const color = kind === 'gold' ? '#15803d' : '#c2410c';
    const dx = endX - startX;
    paths += '<path d="M' + startX + ',' + startY + ' C' + (startX + dx * .42) + ',' + startY
      + ' ' + (startX + dx * .58) + ',' + endY + ' ' + endX + ',' + endY
      + '" stroke="' + color + '" stroke-width="1.5" fill="none" opacity=".45"/>';
  });
  svg.innerHTML = paths;
}
function initPaneResizers() {
  document.querySelectorAll('.pane-resizer').forEach(function (handle) {
    handle.addEventListener('pointerdown', function (event) {
      const row = handle.closest('.row.active');
      const startX = event.clientX;
      const sidebar = handle.parentElement === shell;
      const property = sidebar ? '--sidebar-width'
        : (handle.classList.contains('level-resizer') ? '--level-width' : '--mapping-width');
      const target = sidebar ? shell : row;
      const start = parseFloat(getComputedStyle(target).getPropertyValue(property)) || 280;
      handle.classList.add('dragging');
      handle.setPointerCapture(event.pointerId);
      const move = function (next) {
        target.style.setProperty(property, Math.max(180, start + next.clientX - startX) + 'px');
        scheduleMappingLines();
      };
      const done = function () {
        handle.classList.remove('dragging');
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', done);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', done);
    });
  });
}
function scheduleMappingLines() {
  if (mappingFrame) { return; }
  mappingFrame = requestAnimationFrame(function () {
    mappingFrame = 0;
    drawMappingLines();
  });
}
function currentLevelFilter(row) { return row && row.dataset.levelFilterState || 'all'; }
function applyLevelFilter(row, level) {
  if (!row) { return; }
  row.dataset.levelFilterState = level || 'all';
  const selected = currentLevelFilter(row);
  row.querySelectorAll('[data-level-filter]').forEach(function (control) {
    control.classList.toggle('level-filter-active', control.dataset.levelFilter === selected);
    control.setAttribute('aria-pressed', String(control.dataset.levelFilter === selected));
  });
  row.querySelectorAll('.mapping-pane li[data-iri]').forEach(function (li) {
    li.hidden = selected !== 'all'
      && !li.querySelector('input[data-level-map][value="' + CSS.escape(selected) + '"]:checked');
  });
  scheduleMappingLines();
}
// The chips now carry the assignment visually, so this only keeps the row's hover text honest.
function refreshMappingSummary(li) {
  if (!li) { return; }
  const choices = li.querySelector('.level-choices');
  if (!choices) { return; }
  choices.title = choices.dataset.mappingState || '';
}
function mappingState(li) {
  return {
    levels: Array.from(li.querySelectorAll('input[data-level-map]:checked')).map(function (input) {
      return input.value;
    }),
    unassigned: Boolean(li.querySelector('input[data-unassigned]:checked')),
    verdict: verdictValue(li)
  };
}
function restoreMappingState(li, state) {
  const levels = Array.isArray(state.levels) ? state.levels : [];
  li.querySelectorAll('input[data-level-map]').forEach(function (input) {
    input.checked = levels.includes(input.value);
  });
  const unassigned = li.querySelector('input[data-unassigned]');
  if (unassigned) { unassigned.checked = Boolean(state.unassigned); }
  if (state.verdict) { setVerdictValue(li, state.verdict); }
}
function setMappingRemoved(li, removed) {
  li.classList.toggle('mapping-removed', removed);
  const button = li.querySelector('.remove-mapping');
  if (button) {
    button.textContent = removed ? '↺' : '✕';
    button.title = removed ? 'Undo remove' : 'Remove this mapping';
    button.setAttribute('aria-label', button.title);
  }
  refreshMappingSummary(li);
}
function activate(id, options) {
  const row = rowById(id);
  const nav = navById(id);
  if (!row || !nav || nav.hidden) { return; }
  activeId = id;
  rows.forEach(function (entry) { entry.classList.toggle('active', entry === row); });
  navItems.forEach(function (entry) { entry.classList.toggle('active', entry === nav); });
  stage.classList.remove('empty');
  if (!options || options.scroll !== false) { nav.scrollIntoView({block: 'nearest'}); }
  const firstConcept = row.querySelector('.mapping-pane li[data-iri], .mapping-pane [data-concept-iri]');
  showConcept(firstConcept);
  applyLevelFilter(row, 'all');
}
function visibleItems() { return navItems.filter(function (item) { return !item.hidden; }); }
function move(delta) {
  const visible = visibleItems();
  if (!visible.length) { return; }
  const current = visible.findIndex(function (item) { return item.getAttribute('data-target') === activeId; });
  const next = Math.min(Math.max(current + delta, 0), visible.length - 1);
  activate(visible[next].getAttribute('data-target'));
}
function applyFilters() {
  const query = document.getElementById('review-search').value.trim().toLowerCase();
  const status = document.getElementById('status-filter').value;
  navItems.forEach(function (item) {
    const textMatch = !query || item.dataset.search.indexOf(query) !== -1;
    const decided = item.dataset.currentlyDecided === 'true';
    const statusMatch = status === 'all'
      || (status === 'needs-eye' && item.dataset.needsEye === 'true')
      || (status === 'decided' && decided)
      || (status === 'undecided' && !decided);
    item.hidden = !(textMatch && statusMatch);
  });
  document.querySelectorAll('.nav-group').forEach(function (group) {
    group.hidden = !group.querySelector('.review-item:not([hidden])');
  });
  const visible = visibleItems();
  if (!visible.length) {
    rows.forEach(function (row) { row.classList.remove('active'); });
    stage.classList.add('empty');
    document.getElementById('mapping-lines').innerHTML = '';
  } else if (!navById(activeId) || navById(activeId).hidden) {
    activate(visible[0].getAttribute('data-target'), {scroll: false});
  }
}
function downloadDecisions() {
  const decisions = refresh();
  flushDraft(decisions);
  const blob = new Blob([document.getElementById('out').value + '\\n'], {type: 'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = 'folio-eval-decisions.json';
  link.click();
  URL.revokeObjectURL(link.href);
}
function copyDecisions() {
  const out = document.getElementById('out');
  const decisions = refresh();
  flushDraft(decisions);
  out.focus();
  out.select();
  out.setSelectionRange(0, out.value.length);
  const hint = document.getElementById('hint');
  const done = function () { hint.textContent = 'Copied. Paste it back into the chat.'; };
  const manual = function () {
    hint.textContent = 'Clipboard blocked \\u2014 the text is selected above, press \\u2318C / Ctrl+C.';
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(out.value).then(done, function () {
      try { document.execCommand('copy') ? done() : manual(); } catch (error) { manual(); }
    });
    return;
  }
  try { document.execCommand('copy') ? done() : manual(); } catch (error) { manual(); }
}

document.querySelector('.review-sidebar').addEventListener('click', function (event) {
  const item = event.target.closest('.review-item[data-target]');
  if (item) { activate(item.getAttribute('data-target')); }
});
stage.addEventListener('click', function (event) {
  const levelFilter = event.target.closest('[data-level-filter]');
  if (levelFilter) {
    if (event.target.matches('textarea, input')) { return; }
    applyLevelFilter(levelFilter.closest('.row[data-decision-id]'), levelFilter.dataset.levelFilter);
    return;
  }
  const add = event.target.closest('.add-mapping');
  if (add) {
    const row = add.closest('.row[data-decision-id]');
    const panel = add.closest('.add-concept');
    const label = panel.querySelector('.add-label').value.trim();
    const iri = panel.querySelector('.add-iri').value.trim();
    if (!label || iri.indexOf('https://folio.openlegalstandard.org/') !== 0) {
      panel.querySelector('.add-iri').setCustomValidity('Enter a full FOLIO concept URL and label.');
      panel.querySelector('.add-iri').reportValidity();
      return;
    }
    panel.querySelector('.add-iri').setCustomValidity('');
    addConceptRow(row, label, iri);
    panel.querySelector('.add-label').value = ''; panel.querySelector('.add-iri').value = '';
    scheduleMappingLines();
    const decisions = refresh(); persistDraft(decisions); updateProgress(); applyFilters(); return;
  }
  const remove = event.target.closest('.remove-mapping');
  if (remove) {
    const li = remove.closest('li[data-iri]');
    if (li.classList.contains('mapping-removed')) {
      restoreMappingState(li, JSON.parse(li.dataset.previousState || '{}'));
      setMappingRemoved(li, false);
    } else {
      li.dataset.previousState = JSON.stringify(mappingState(li));
      li.querySelectorAll('input[data-level-map], input[data-unassigned]').forEach(function (input) {
        input.checked = false;
      });
      setVerdictValue(li, li.dataset.kind === 'gold' ? 'remove' : 'not_gold');
      setMappingRemoved(li, true);
    }
    const mappingRow = li.closest('.row[data-decision-id]');
    applyLevelFilter(mappingRow, currentLevelFilter(mappingRow));
    scheduleMappingLines();
    const decisions = refresh(); persistDraft(decisions); updateProgress(); applyFilters(); return;
  }
  const confirm = event.target.closest('.mark-reviewed');
  if (confirm) {
    const row = confirm.closest('.row[data-decision-id]');
    const nav = row ? navById(row.getAttribute('data-decision-id')) : null;
    if (row && nav && rowComplete(row)) {
      nav.dataset.reviewed = 'true';
      const decisions = refresh();
      persistDraft(decisions);
      updateProgress();
      applyFilters();
    } else {
      confirm.textContent = 'Answer every decision first';
    }
    return;
  }
  const concept = event.target.closest('li[data-iri], [data-concept-iri]');
  if (!concept || !concept.closest('.mapping-pane')) { return; }
  if (event.target.matches('input, label')) {
    requestAnimationFrame(function () { showConcept(concept); });
  } else {
    showConcept(concept);
  }
});
document.getElementById('review-search').addEventListener('input', applyFilters);
document.getElementById('status-filter').addEventListener('change', applyFilters);
document.getElementById('previous-row').addEventListener('click', function () { move(-1); });
document.getElementById('next-row').addEventListener('click', function () { move(1); });
document.getElementById('download').addEventListener('click', downloadDecisions);
document.getElementById('copy').addEventListener('click', copyDecisions);
document.getElementById('theme-toggle').addEventListener('click', function () {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});
document.addEventListener('change', function (event) {
  const row = event.target.closest('.row[data-decision-id]');
  const nav = row ? navById(row.getAttribute('data-decision-id')) : null;
  if (nav && row) { nav.dataset.reviewed = String(rowComplete(row)); }
  if (row && event.target.matches('input[data-kind=pairing]')) {
    row.querySelectorAll('.pairing .opt').forEach(function (opt) {
      opt.classList.toggle('picked', opt.getAttribute('data-choice') === event.target.value);
    });
  }
  if (event.target.matches('input[data-unassigned]') && event.target.checked) {
    event.target.closest('li[data-iri]').querySelectorAll('input[data-level-map]').forEach(function (input) {
      input.checked = false;
    });
  }
  if (event.target.matches('input[data-level-map]') && event.target.checked) {
    const unassigned = event.target.closest('li[data-iri]').querySelector('input[data-unassigned]');
    if (unassigned) { unassigned.checked = false; }
  }
  if (event.target.matches('input[data-level-map], input[data-unassigned]')) {
    refreshMappingSummary(event.target.closest('li[data-iri]'));
  }
  // Excluding a concept drops its level assignments — an output that is not in gold is not
  // mapped to a level either, and leaving stale chips checked would redraw phantom lines.
  if (event.target.matches('input[data-verdict], input[type=radio][value="remove"], input[type=radio][value="not_gold"]')) {
    const concept = event.target.closest('li[data-iri]');
    if (concept && verdictExcluded(concept)) {
      concept.querySelectorAll('input[data-level-map], input[data-unassigned]')
        .forEach(function (input) { input.checked = false; });
    }
    scheduleMappingLines();
  }
  const decisions = refresh();
  if (row && currentLevelFilter(row) !== 'all') {
    applyLevelFilter(row, currentLevelFilter(row));
  }
  persistDraft(decisions);
  updateProgress();
  applyFilters();
});
document.addEventListener('input', function (event) {
  if (event.target.matches('textarea.note')) {
    scheduleDraft();
  }
});
document.addEventListener('focusout', function (event) {
  if (event.target.matches('textarea.note')) { flushDraft(); }
});
document.addEventListener('keydown', function (event) {
  if (event.target.matches('input, textarea, select')) { return; }
  if (event.key === 'ArrowDown' || event.key.toLowerCase() === 'j') { event.preventDefault(); move(1); }
  if (event.key === 'ArrowUp' || event.key.toLowerCase() === 'k') { event.preventDefault(); move(-1); }
});
window.addEventListener('resize', scheduleMappingLines);
window.addEventListener('beforeunload', function () { flushDraft(); });
document.querySelector('.review-sidebar').addEventListener('scroll', scheduleMappingLines, {passive: true});
stage.addEventListener('scroll', scheduleMappingLines, {passive: true, capture: true});

restoreTheme();
restoreDraft();
document.querySelectorAll('.mapping-pane li[data-iri]').forEach(refreshMappingSummary);
initPaneResizers();
refresh();
updateProgress();
applyFilters();
const firstVisible = visibleItems()[0];
if (firstVisible) { activate(firstVisible.getAttribute('data-target'), {scroll: false}); }
"""
)


def _mapping(value: object) -> Mapping[str, object]:
    """A nested JSON object read defensively: anything that is not a mapping reads as empty."""
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _grade_list(
    entries: Sequence[Mapping[str, object]],
    *,
    row: PacketRow,
    decision_id: str,
    kind: str,
    prefill: Mapping[str, object],
    choices: Sequence[tuple[str, str]] | None = None,
) -> str:
    """One line per concept: a single include/exclude checkbox, the label, and its level chips.

    Damien, 2026-08-16: the pane is for scanning a mapping, so the ``rdfs:label`` has to be the
    thing you read, one output per line, with the verdict as one checkbox rather than a stacked
    radio pair. ``choices`` still names the pair ``(checked_value, label), (unchecked_value,
    label)``, so section F can ask "Accept as gold / Reject" while emitting the same
    ``elevate``/``not_gold`` verdicts :func:`folio_eval.audit.fold_granular_decisions` applies.
    Anything that does not fit the line (definition, IRI, score provenance) rides on ``data-*``
    and surfaces in the inspector, which costs no vertical space.
    """
    if not entries:
        return '<p class="note">none</p>'
    choices = choices or (
        (("keep", "Keep gold"), ("remove", "Remove from gold"))
        if kind == "gold"
        else (("elevate", "Elevate to gold"), ("not_gold", "Not gold"))
    )
    (on_value, on_label), (off_value, off_label) = choices
    items: list[str] = []
    for position, entry in enumerate(entries):
        iri = str(entry.get("iri", ""))
        name = f"{kind}|{decision_id}|{position}"
        label_text = str(entry.get("label", ""))
        tags: list[str] = []
        if kind != "gold":
            score = entry.get("score")
            if score is not None:
                tags.append(f'<span class="tag">{_esc(score)}</span>')
            if entry.get("already_gold"):
                tags.append('<span class="tag good" title="already in gold">&#10003;</span>')
        picked = str(prefill.get(iri, "")) if isinstance(prefill, Mapping) else ""
        if not picked:
            picked = "keep" if kind == "gold" else "not_gold"
        toggle = (
            f'<label class="verdict-toggle" title="Checked = {_esc(on_label)} &middot; '
            f'unchecked = {_esc(off_label)}">'
            f'<input type="checkbox" data-verdict name="{_esc(name)}" '
            f'data-on="{_esc(on_value)}" data-off="{_esc(off_value)}"'
            f"{' checked' if picked == on_value else ''}>"
            f'<span class="sr-only">{_esc(on_label)}</span></label>'
        )
        items.append(
            f'<li data-iri="{_esc(iri)}" data-kind="{_esc(kind)}" '
            f'data-label="{_esc(label_text)}" '
            f'data-definition="{_esc(entry.get("definition", ""))}" '
            f'data-score="{_esc(entry.get("score", ""))}" '
            f'data-probability="{_esc(entry.get("probability", ""))}" '
            f'data-path="{_esc(entry.get("extraction_path", ""))}" '
            f'data-column="{_esc(entry.get("column", ""))}" '
            f'data-branch="{_esc(entry.get("branch", ""))}"><div class="concept-row-head">'
            + toggle
            + f'<span class="concept-label" title="{_esc(label_text)}">{_esc(label_text)}</span>'
            + "".join(tags)
            + _level_assignment_controls(row, entry)
            + '<button class="secondary remove-mapping" type="button" title="Remove this mapping" '
            'aria-label="Remove this mapping">&#10005;</button>'
            + "</div></li>"
        )
    return '<ul class="grade">' + "".join(items) + "</ul>"


def _paths(row: PacketRow) -> str:
    instances = row.extra.get("instances")
    if not isinstance(instances, (list, tuple)) or not instances:
        return ""
    chips: list[str] = []
    for instance in list(instances)[:8]:
        if not isinstance(instance, Mapping):
            continue
        path = " \u203a ".join(_strings(instance.get("path"))) or "(root)"
        chips.append(f"<span>L{_esc(instance.get('level', ''))} {_esc(path)}</span>")
    more = len(instances) - len(chips)
    if more > 0:
        chips.append(f"<span>+{more} more</span>")
    return f'<p class="paths">{"".join(chips)}</p>'


def _records(value: object) -> list[Mapping[str, object]]:
    """A nested JSON array of objects, read defensively."""
    if not isinstance(value, (list, tuple)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _strings(value: object) -> list[str]:
    """A nested JSON array of scalars, read defensively as text."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(entry) for entry in value]


def _input_levels(row: PacketRow) -> tuple[str, ...]:
    """The visible source hierarchy, one adjudicable level per breadcrumb segment."""
    parts = tuple(part.strip() for part in row.input_text.split(">") if part.strip())
    if parts:
        return parts
    path = tuple((*row.ancestor_path, row.surface_label))
    return tuple(part for part in path if part)


def _level_pane(row: PacketRow) -> str:
    levels = _input_levels(row)
    folded = row.extra.get("folded")
    folded_map = folded if isinstance(folded, Mapping) else {}
    notes_raw = folded_map.get("level_notes")
    notes = notes_raw if isinstance(notes_raw, Mapping) else {}
    nodes = []
    for index, label in enumerate(levels, start=1):
        nodes.append(
            f'<section class="level-node" data-level-id="L{index}">'
            f'<button class="level-filter-button" type="button" data-level-filter="L{index}" '
            f'aria-label="Show mappings for L{index}"><span class="level-number">L{index}</span>'
            f"<strong>{_esc(label)}</strong></button>"
            f'<textarea class="note level-note" rows="2" name="level-note|{_esc(row.decision_id)}|L{index}" '
            f'aria-label="note on L{index} mapping" placeholder="note on this level&rsquo;s mappings (optional)">'
            f"{_esc(notes.get(f'L{index}', ''))}</textarea>"
            "</section>"
        )
    return (
        '<div class="level-pane"><p class="pane-heading">Input hierarchy · map each level</p>'
        '<button class="secondary level-filter-all level-filter-active" type="button" '
        'data-level-filter="all">See all levels</button>' + "".join(nodes) + "</div>"
    )


def _system_level_mappings(row: PacketRow) -> Mapping[str, object]:
    """Read the packet builder's atomized matcher/workbook baseline."""
    raw = row.extra.get("system_level_mappings")
    return raw if isinstance(raw, Mapping) else {}


def _explicit_level_mappings(row: PacketRow) -> Mapping[str, object] | None:
    """Return a human-folded mapping when one exists; even an empty object is authoritative."""
    if "level_mappings" in row.extra:
        raw = row.extra.get("level_mappings")
        return raw if isinstance(raw, Mapping) else {}
    folded = row.extra.get("folded")
    if isinstance(folded, Mapping) and "level_mappings" in folded:
        raw = folded.get("level_mappings")
        return raw if isinstance(raw, Mapping) else {}
    return None


def _review_baseline(row: PacketRow) -> Mapping[str, object]:
    """One canonical baseline for rendering controls and diff-only decision export."""
    raw = row.extra.get("baseline")
    if isinstance(raw, Mapping):
        return raw
    if row.section == "pairing":
        return {}
    gold = tuple(row.gold) or tuple(_records(row.extra.get("gold_ref")))
    pipeline = tuple(row.pipeline)
    if not pipeline:
        pipeline_ref = row.extra.get("pipeline_ref")
        if isinstance(pipeline_ref, Mapping):
            pipeline = tuple(_records(pipeline_ref.get("candidates")))
    return {
        "gold": {str(entry.get("iri", "")): "keep" for entry in gold},
        "pipeline": {str(entry.get("iri", "")): "not_gold" for entry in pipeline},
        "level_mappings": _system_level_mappings(row),
    }


def _level_assignment_controls(row: PacketRow, entry: Mapping[str, object]) -> str:
    """Expose the system's atomized input-level mapping, with human-editable assignments.

    These ride on the concept's single line as compact ``L1``/``L2``/``L3`` chips rather than
    behind a disclosure chevron (Damien, 2026-08-16) — the level a mapping sits at is part of
    scanning it, and a chevron per row both hid the label and cost a line to open.
    """
    levels = _input_levels(row)
    if not levels:
        return ""
    iri = str(entry.get("iri", ""))
    folded = row.extra.get("folded")
    folded_map = folded if isinstance(folded, Mapping) else {}
    explicit = _explicit_level_mappings(row)
    known = explicit if explicit is not None else _system_level_mappings(row)
    options_raw = folded_map.get("mapping_options")
    options = options_raw if isinstance(options_raw, Mapping) else {}
    unassigned = iri in _strings(options.get("unassigned"))
    labels = "".join(
        f'<label class="level-choice" title="L{index} &middot; {_esc(level_label)}">'
        f'<input type="checkbox" data-level-map '
        f'data-iri="{_esc(iri)}" value="L{index}"'
        f"{' checked' if iri in _strings(known.get(f'L{index}')) else ''}>"
        f"<span>L{index}</span></label>"
        for index, level_label in enumerate(levels, start=1)
    )
    assigned = [
        f"L{index}"
        for index in range(1, len(levels) + 1)
        if iri in _strings(known.get(f"L{index}"))
    ]
    has_known = bool(assigned)
    if explicit is not None:
        state = "Reviewed level mapping" if has_known or unassigned else "No reviewed mapping"
    else:
        state = "System level mapping" if has_known else "No system level mapping · choose"
    return (
        f'<span class="level-choices" data-mapping-state="{_esc(state)}" title="{_esc(state)}">'
        + labels
        + '<label class="level-choice unassigned-choice" title="Unassigned &mdash; maps to no '
        f'input level"><input type="checkbox" data-unassigned data-iri="{_esc(iri)}"'
        + (" checked" if unassigned else "")
        + "><span>&mdash;</span></label></span>"
    )


# --------------------------------------------------------------------------------------
# Panel 0 -- the original spreadsheet rows behind the question
# --------------------------------------------------------------------------------------


def _source_panel(row: PacketRow) -> str:
    """The workbook rows this adjudication came from, as a mini-grid with the sheet's headers."""
    grid = row.extra.get("source_grid")
    if not isinstance(grid, Mapping):
        return ""
    tables: list[str] = []
    for entry in _records(grid.get("grids")):
        headers = _strings(entry.get("headers"))
        head = "".join(f"<th>{_esc(name)}</th>" for name in headers)
        body: list[str] = []
        for record in _records(entry.get("rows")):
            cells = _strings(record.get("cells"))
            rendered = "".join(
                f'<td class="filled">{_esc(cell)}</td>' if cell.strip() else "<td></td>"
                for cell in cells
            )
            body.append(f'<tr><th class="rownum">{_esc(record.get("row", ""))}</th>{rendered}</tr>')
        if not body:
            continue
        tables.append(
            '<div class="tablewrap"><table class="sheetgrid"><thead><tr>'
            f'<th class="rownum">row</th>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>"
        )
    footnotes: list[str] = []
    unlocated = _strings(grid.get("unlocated"))
    if unlocated:
        footnotes.append(
            "could not be re-located in the derived sheet — row " + ", ".join(unlocated)
        )
    more = grid.get("more") or 0
    if more:
        footnotes.append(f"+{more} further source row(s) not shown")
    if grid.get("ambiguous"):
        footnotes.append("several sheets carry this row number; the text-matched one is shown")
    if not tables and not footnotes:
        return ""
    note = f'<p class="note">{_esc(" · ".join(footnotes))}</p>' if footnotes else ""
    return (
        '<section class="panel source"><h4>Original spreadsheet'
        '<span class="who">the row(s) this question came from</span></h4>'
        + "".join(tables)
        + note
        + "</section>"
    )


# --------------------------------------------------------------------------------------
# Panels 1 and 2 -- read-only: what the workbook says, and what the pipeline says
# --------------------------------------------------------------------------------------


def _gold_reference(entries: Sequence[Mapping[str, object]]) -> str:
    if not entries:
        return '<p class="note">none — this input cell carries no curated mapping.</p>'
    items: list[str] = []
    for entry in entries:
        bits = [
            f"<strong>{_esc(entry.get('label', ''))}</strong> "
            f"<code>{_esc(_short(str(entry.get('iri', ''))))}</code>"
        ]
        if entry.get("column"):
            bits.append(f'<span class="tag">{_esc(entry["column"])}</span>')
        line = " ".join(bits)
        if entry.get("definition"):
            line += f'<span class="def">{_esc(entry["definition"])}</span>'
        items.append(f"<li>{line}</li>")
    return '<ul class="iris">' + "".join(items) + "</ul>"


def _pipeline_reference(reference: object) -> str:
    """The ranked list folio-resolve produces today: committed answer set marked off from the tail."""
    if not isinstance(reference, Mapping):
        return '<p class="note">no cached prediction for this input cell.</p>'
    candidates = _records(reference.get("candidates"))
    if not candidates:
        return '<p class="note">the pipeline returned no candidate for this input cell.</p>'
    items: list[str] = []
    for entry in candidates:
        committed = bool(entry.get("committed"))
        score = f"score {_esc(entry.get('score'))}"
        probability = entry.get("probability")
        if probability is not None:
            score += f" · p={_esc(probability)}"
        bits = [
            f'<span class="tag">#{_esc(entry.get("rank", ""))}</span>',
            f"<strong>{_esc(entry.get('label', ''))}</strong>",
            f"<code>{_esc(_short(str(entry.get('iri', ''))))}</code>",
            f'<span class="tag">{score}</span>',
            '<span class="tag good">committed answer</span>'
            if committed
            else '<span class="tag">ranked tail</span>',
        ]
        if entry.get("already_gold"):
            bits.append('<span class="tag good">already gold</span>')
        items.append(f'<li class="{"committed" if committed else "tail"}">{" ".join(bits)}</li>')
    total = reference.get("ranked_total") or len(candidates)
    top_k = reference.get("top_k") or 0
    lede = (
        f'<p class="note">Committed answer set = the top {_esc(top_k)} of this ranked list '
        f"(KTD2 answer rule); showing {_esc(len(candidates))} of {_esc(total)} ranked candidates."
        "</p>"
    )
    return lede + '<ul class="iris">' + "".join(items) + "</ul>"


def _workbook_line(entries: Sequence[Mapping[str, object]]) -> str:
    """The smaller provenance line under the current-gold panel: what the curator originally wrote.

    Kept distinct from the panel above it on purpose (Damien, 2026-07-28): a folded row's current
    gold can differ from the workbook cell it started from, and collapsing the two into one list
    is exactly the falsehood that prompted this line to exist.
    """
    if not entries:
        return '<p class="note workbook">Workbook curation: none — this input cell carried no curated mapping.</p>'
    labels = "; ".join(
        f"{_esc(entry.get('label', ''))} <code>{_esc(_short(str(entry.get('iri', ''))))}</code>"
        for entry in entries
    )
    return f'<p class="note workbook">Workbook curation: {labels}</p>'


def _reference_panels(row: PacketRow, *, current_version: int = 0) -> str:
    """Panels 1 and 2, on every row of every section, labelled identically throughout.

    Panel 1 is the *current* gold state -- the latest gold version on disk, which already carries
    every decision folded so far -- never the pre-fold snapshot the packet's sections were built
    from. A smaller line underneath keeps the original workbook curation visible for provenance.
    """
    gold_title = (
        f"Gold — current (v{current_version}, includes your corrections)"
        if current_version
        else "Gold — current (includes your corrections)"
    )
    if row.section == "pairing":
        context = _records(row.extra.get("input_context"))
        gold_body = "".join(
            f'<div class="perinput"><h5>L{_esc(entry.get("level", ""))} · '
            f"{_esc(entry.get('text', ''))}</h5>"
            f"{_gold_reference(_records(entry.get('gold')))}"
            f"{_workbook_line(_records(entry.get('workbook_gold')))}</div>"
            for entry in context
        )
        pipe_body = "".join(
            f'<div class="perinput"><h5>L{_esc(entry.get("level", ""))} · '
            f"{_esc(entry.get('text', ''))}</h5>"
            f"{_pipeline_reference(entry.get('pipeline'))}</div>"
            for entry in context
        )
    else:
        gold_raw = row.extra.get("gold_ref")
        gold_body = _gold_reference(
            _records(gold_raw) if isinstance(gold_raw, (list, tuple)) else list(row.gold)
        )
        workbook_raw = row.extra.get("workbook_gold")
        gold_body += _workbook_line(
            _records(workbook_raw) if isinstance(workbook_raw, (list, tuple)) else []
        )
        pipe_body = _pipeline_reference(row.extra.get("pipeline_ref"))
    return (
        f'<section class="panel ref-gold"><h4>{_esc(gold_title)}'
        '<span class="who">the live gold this harness scores against right now</span></h4>'
        + gold_body
        + "</section>"
        '<section class="panel ref-pipe"><h4>Current pipeline — folio-resolve today'
        '<span class="who">what the matcher returns for this input, gold-blind</span></h4>'
        + pipe_body
        + "</section>"
    )


# --------------------------------------------------------------------------------------
# Panel 3 -- the question this section is asking
# --------------------------------------------------------------------------------------


def _tag_chips(entry: Mapping[str, object]) -> str:
    """One chip per concept, with its IRI. Never a comma-joined run of concept names.

    A pipe cell (``Islamic Law System | Finance and Lending Law``) is two tags. Joined with a
    comma it reads as one concept whose name happens to contain a comma — which is exactly how
    the Islamic-finance row looked to Damien when he asked for the pipe to be recognized.
    """
    tags = _records(entry.get("tags"))
    if not tags:
        tags = [{"label": label, "iri": ""} for label in _strings(entry.get("values"))]
    if not tags:
        tags = [{"label": label, "iri": ""} for label in _strings(entry.get("labels"))]
    if not tags:
        return "<em>— nothing —</em>"
    chips: list[str] = []
    for tag in tags:
        iri = str(tag.get("iri", ""))
        code = f" <code>{_esc(_short(iri))}</code>" if iri else ""
        css = "concept" if iri else "concept unresolved"
        data = (
            f' data-concept-iri="{_esc(iri)}" data-label="{_esc(tag.get("label", ""))}"'
            if iri
            else ""
        )
        chips.append(
            f'<span class="{css}"{data}><strong>{_esc(tag.get("label", ""))}</strong>{code}</span>'
        )
    return f'<span class="taglist">{"".join(chips)}</span>'


def _output_cells(blocks: Sequence[Mapping[str, object]]) -> str:
    """The row's output cells, one line each, tags rendered individually."""
    lines: list[str] = []
    for block in blocks:
        values = _strings(block.get("values"))
        pipe = (
            f'<span class="tag pipeflag">pipe cell → {len(values)} tags</span> '
            if block.get("from_pipe")
            else ""
        )
        lines.append(
            f'<div class="cellblock"><span class="col">{_esc(block.get("column", ""))}</span> '
            f"{pipe}{_tag_chips(block)}</div>"
        )
    return "".join(lines)


def _applied_reading_panel(assignments: Mapping[str, object]) -> str:
    """ "Your ruling, applied" — the row's live per-input gold, read straight off current gold.

    Shown on a folded pairing row instead of trusting either canned reading's stale pre-fold
    label: Damien's own edits (``edited_iris``) can leave the live state matching neither
    Heuristic nor Alternative verbatim (2026-07-28: the Borrower/Insurance-Finance row, edited to
    drop the cascaded concepts a child cell never implies its parent), so this is read from
    ``assignments["applied"]`` -- computed fresh from the current gold version every time the
    packet is built -- rather than re-derived or guessed at render time.
    """
    entries = _records(assignments.get("applied"))
    if not entries:
        return ""
    lines = "".join(
        f'<li class="instance"><strong>L{_esc(entry.get("level", ""))} '
        f"{_esc(entry.get('input', ''))}</strong> → " + _tag_chips(entry) + "</li>"
        for entry in entries
    )
    return (
        '<div class="opt applied-reading" data-choice="applied">'
        '<h4>Your ruling, applied<span class="tag done">live in gold now</span></h4>'
        f'<ul class="iris">{lines}</ul>'
        "</div>"
    )


def _pairing_readings(row: PacketRow) -> str:
    """Reading A / Reading B, with the one that survives Damien's principle pre-checked."""
    assignments = row.extra.get("assignments")
    prechecked = row.extra.get("precheck")
    violations: Mapping[str, object] = prechecked if isinstance(prechecked, Mapping) else {}
    folded = row.extra.get("folded")
    is_folded = isinstance(folded, Mapping)
    # A never-folded row is pre-checked by the rule-based precheck (heuristic wins every tie); a
    # folded row is pre-checked by whichever reading (if either) still equals what is live in
    # gold today -- an edited row can match neither, and the row must not claim one does.
    if is_folded and isinstance(assignments, Mapping):
        choice = str(pairing_applied_reading_name(assignments) or "")
    else:
        choice = str(violations.get("choice", "heuristic"))
    out: list[str] = []
    blocks = _records(row.extra.get("blocks"))
    if blocks:
        piped = sum(1 for block in blocks if block.get("from_pipe"))
        lede = f"outputs on this row — one line per output cell ({len(blocks)})"
        if piped:
            lede += (
                f"; {piped} of them is a pipe cell, i.e. one cell naming several tags"
                if piped == 1
                else f"; {piped} of them are pipe cells, i.e. one cell naming several tags"
            )
        out.append(f'<p class="blocks">{_esc(lede)}</p>{_output_cells(blocks)}')
    if not isinstance(assignments, Mapping):
        return "".join(out)
    if is_folded:
        out.append(_applied_reading_panel(assignments))
    panels: list[str] = []
    # The "(applied to gold today)" claim is only true before any fold -- once folded, "Your
    # ruling, applied" above is the truth and a candidate reading is labelled as a candidate,
    # never as what is live (that was the falsehood Damien caught, 2026-07-28).
    heuristic_title = (
        "Reading A — heuristic (candidate reading)"
        if is_folded
        else "Reading A — heuristic (applied to gold today)"
    )
    for reading, title, key in (
        ("heuristic", heuristic_title, "heuristic_violations"),
        ("alternative", "Reading B — alternative", "alternative_violations"),
    ):
        lines: list[str] = []
        for entry in _records(assignments.get(reading)):
            lines.append(
                f'<li class="instance"><strong>L{_esc(entry.get("level", ""))} '
                f"{_esc(entry.get('input', ''))}</strong> → " + _tag_chips(entry) + "</li>"
            )
        broken = [name.replace("_", " ") for name in _strings(violations.get(key))]
        flag = (
            '<p class="note"><span class="tag needseye">dis-preferred</span> '
            f"{_esc(', '.join(broken))}</p>"
            if broken
            else ""
        )
        picked = reading == choice
        panels.append(
            f'<div class="opt{" picked" if picked else ""}" data-choice="{reading}">'
            f"<h4>{_esc(title)}</h4>"
            f'<ul class="iris">{"".join(lines)}</ul>'
            f"{flag}"
            f'<label><input type="radio" data-kind="pairing" name="pair|{_esc(row.decision_id)}" '
            f'value="{reading}"{" checked" if picked else ""}> pick this reading</label>'
            "</div>"
        )
    out.append(f'<div class="pairing">{"".join(panels)}</div>')
    if not choice and is_folded:
        out.append(
            '<p class="note">Neither candidate reading matches what is live in gold today — see '
            "&ldquo;Your ruling, applied&rdquo; above. Pick one below only to change it.</p>"
        )
    elif not choice:
        out.append(
            '<p class="note"><span class="tag needseye">needs your eye</span> both readings break '
            "the rule, so neither is pre-checked — an untouched row leaves gold exactly as it "
            "is.</p>"
        )
    return "".join(out)


def _consistency_block(row: PacketRow) -> str:
    instances = row.extra.get("instances")
    if not isinstance(instances, (list, tuple)):
        return ""
    lines: list[str] = []
    for instance in _records(instances):
        path = " \u203a ".join(_strings(instance.get("path"))) or "(root)"
        labels = ", ".join(_strings(instance.get("gold_labels_raw")))
        lines.append(
            f'<li class="instance"><code>row {_esc(instance.get("row", ""))}</code> '
            f"L{_esc(instance.get('level', ''))} {_esc(path)} → "
            f"{_esc(labels or '— no mapping —')}</li>"
        )
    return (
        '<div class="block"><h4>How this cell was answered in each place</h4>'
        f'<ul class="iris">{"".join(lines)}</ul></div>'
    )


#: What Panel 3 is asking, per section. Panels 1 and 2 are always the same two questions
#: ("what does the workbook say", "what does folio-resolve say"), so only this one moves.
PROPOSED_TITLES: Mapping[str, str] = {
    "pairing": "Proposed — this sheet&rsquo;s question: which reading of the shared row is right?",
    "consistency": "Proposed — this sheet&rsquo;s question: which of the union&rsquo;s concepts "
    "belong to this cell?",
    "suspect": "Proposed — this sheet&rsquo;s question: does this cell&rsquo;s gold stand, and "
    "should any candidate rise?",
    "resolution": "Proposed — this sheet&rsquo;s question: which FOLIO concept did this label "
    "mean?",
    "new_gold": "Proposed — this sheet&rsquo;s question: should any candidate become gold for "
    "this blank cell?",
    "improvement": "Proposed — this sheet&rsquo;s question: does this cell name an atom its gold "
    "is missing?",
}


def _improvement_block(
    row: PacketRow, *, pipe_prefill: Mapping[str, object] | None = None, initial: str = ""
) -> str:
    """Section F's one question, per proposed atom: accept it as gold, or reject it.

    Gold itself is *not* re-graded here — a machine-generated pilot row must never be able to
    delete curated gold. It shows read-only above (Panel 1) and only the proposals are askable.
    ``pipe_prefill``/``initial`` carry a prior applied decision forward the same way every other
    section's pipeline block does, so a folded improvement row pre-fills instead of re-asking.
    """
    proposals = _records(row.extra.get("proposals"))
    by_iri = {str(entry.get("iri", "")): entry for entry in proposals}
    lines: list[str] = []
    for entry in row.pipeline:
        proposal = by_iri.get(str(entry.get("iri", "")), {})
        bits: list[str] = []
        if proposal.get("branch"):
            bits.append(f'<span class="tag">{_esc(proposal["branch"])}</span>')
        if proposal.get("method"):
            bits.append(
                f'<span class="tag">via {_esc(proposal["method"])}'
                + (
                    f" &ldquo;{_esc(proposal.get('query', ''))}&rdquo;"
                    if proposal.get("query")
                    else ""
                )
                + "</span>"
            )
        lines.append("".join(bits))
    return (
        '<div class="block pipeline"><h4>Proposed atom tags'
        '<span class="tag machine">machine-proposed</span>'
        '<span class="who">check to accept as gold</span></h4>'
        + _grade_list(
            row.pipeline,
            row=row,
            decision_id=row.decision_id,
            kind="pipeline",
            prefill=pipe_prefill or {},
            choices=(("elevate", "Accept as gold"), ("not_gold", "Reject")),
        )
        + _textarea(
            "note pipeline-note",
            "pipeline-note",
            row,
            "note on these proposals",
            "note on these proposals (optional)",
            initial=initial,
        )
        + "</div>"
    )


def _folded_panel(record: Mapping[str, object]) -> str:
    """A decision already folded into a later gold version: what you decided, and what it did.

    Still fully open below (Damien, 2026-07-28: "let me add notes and change items even where you
    think things are settled") — this panel is a record of what happened last time, not a lock.
    """
    bits: list[str] = []
    if record.get("summary"):
        bits.append(f'<p class="note">{_esc(record["summary"])}</p>')
    for label, key in (("Your note", "note"), ("On the gold", "gold_note")):
        if record.get(key):
            bits.append(f'<p class="yournote"><strong>{label}:</strong> {_esc(record[key])}</p>')
    if record.get("gold_version"):
        bits.append(
            f'<p class="note">applied to gold v{_esc(record["gold_version"])}'
            + (f" ({_esc(record['gold_id'])})" if record.get("gold_id") else "")
            + " — everything below is pre-filled to match; change anything to submit an "
            "amendment.</p>"
        )
    return (
        '<section class="folded"><h5>Decided — applied, still open</h5>'
        + "".join(bits)
        + "</section>"
    )


def _textarea(
    css_class: str,
    name_prefix: str,
    row: PacketRow,
    aria_label: str,
    placeholder: str,
    *,
    initial: str = "",
) -> str:
    """One note field, pre-filled with ``initial`` when this row was already decided.

    Every input on a previously-folded row stays enabled — nothing here is ``disabled`` (Damien,
    2026-07-28). Re-typing exactly ``initial`` is a no-op the sheet's diff-before-submit JS drops;
    typing anything else is an amendment.
    """
    return (
        f'<textarea class="{css_class}" rows="2" '
        f'name="{name_prefix}|{_esc(row.decision_id)}" '
        f'aria-label="{aria_label}" '
        f'placeholder="{placeholder}">{_esc(initial)}</textarea>'
    )


def _row_note(row: PacketRow, *, initial: str = "") -> str:
    """The note field that now sits under every decision unit, pairing and consistency included."""
    return _textarea(
        "note row-note rownote",
        "note",
        row,
        "note on this decision",
        "note on this decision (optional)",
        initial=initial,
    )


def _proposed_panel(row: PacketRow, *, baseline: Mapping[str, object]) -> str:
    prefill_raw = row.extra.get("prefill")
    prefill: Mapping[str, object] = prefill_raw if isinstance(prefill_raw, Mapping) else {}
    folded_raw = row.extra.get("folded")
    folded: Mapping[str, object] = folded_raw if isinstance(folded_raw, Mapping) else {}
    gold_entries = tuple(row.gold) or tuple(_records(row.extra.get("gold_ref")))
    pipeline_entries = tuple(row.pipeline)
    if not pipeline_entries:
        pipeline_ref = row.extra.get("pipeline_ref")
        if isinstance(pipeline_ref, Mapping):
            pipeline_entries = tuple(_records(pipeline_ref.get("candidates")))
    # A row already folded pre-fills from what is actually live in gold (the baseline), which
    # takes precedence over an older carried-forward ruling — it is newer and it is the truth.
    gold_prefill = _mapping(baseline.get("gold")) or _mapping(prefill.get("gold"))
    pipe_prefill = _mapping(baseline.get("pipeline")) or _mapping(prefill.get("pipeline"))
    body: list[str] = []
    if row.section == "pairing":
        body.append(_pairing_readings(row))
    elif row.section == "improvement":
        body.append(
            _improvement_block(
                row, pipe_prefill=pipe_prefill, initial=str(folded.get("pipeline_note", ""))
            )
        )
    else:
        if row.section == "consistency":
            body.append(_consistency_block(row))
        body.append(
            '<div class="block gold"><h4>Gold<span class="who">checked stays in gold '
            '&middot; uncheck to remove</span></h4>'
            + _grade_list(
                gold_entries,
                row=row,
                decision_id=row.decision_id,
                kind="gold",
                prefill=gold_prefill,
            )
            + _textarea(
                "note gold-note",
                "gold-note",
                row,
                "note on this cell&rsquo;s gold",
                "note on this cell&rsquo;s gold (optional)",
                initial=str(folded.get("gold_note", "")),
            )
            + "</div>"
        )
        pipeline_title = (
            "FOLIO proposals" if row.section == "resolution" else "Pipeline candidates"
        )
        body.append(
            f'<div class="block pipeline"><h4>{_esc(pipeline_title)}'
            '<span class="who">check to elevate into gold</span></h4>'
            + _grade_list(
                pipeline_entries,
                row=row,
                decision_id=row.decision_id,
                kind="pipeline",
                prefill=pipe_prefill,
            )
            + _textarea(
                "note pipeline-note",
                "pipeline-note",
                row,
                "note on the pipeline&rsquo;s answer",
                "note on the pipeline&rsquo;s answer (optional)",
                initial=str(folded.get("pipeline_note", "")),
            )
            + "</div>"
        )
    if prefill.get("note") and not folded:
        body.append(f'<p class="prefilled">Pre-filled: {_esc(prefill["note"])}</p>')
    title = PROPOSED_TITLES.get(row.section, "Proposed — this sheet&rsquo;s question")
    return (
        f'<section class="panel proposed"><h4>{title}'
        '<span class="who">what this gate is asking you to decide</span></h4>'
        + "".join(body)
        + _row_note(row, initial=str(folded.get("note", "")))
        + f'<p class="note">{_esc(row.suggested_action)}</p>'
        + "</section>"
    )


def _render_row_v2(row: PacketRow, *, current_version: int = 0) -> str:
    level_raw = row.extra.get("level")
    level = level_raw if isinstance(level_raw, int) else (1 if row.section == "pairing" else 3)
    level_class = f"lvl-{min(max(level, 1), 6)}"

    header_bits = [f'<span class="tag reason">{_esc(row.reason_class)}</span>']
    if row.extra.get("level"):
        header_bits.append(f'<span class="tag">L{_esc(row.extra["level"])}</span>')
    if row.slice_name:
        header_bits.append(f'<span class="tag">{_esc(row.slice_name)}</span>')
    if row.item_id:
        header_bits.append(f'<span class="tag">{_esc(row.item_id)}</span>')
    instances = row.extra.get("instances")
    if isinstance(instances, (list, tuple)) and len(instances) > 1:
        header_bits.append(f'<span class="tag">x{len(instances)} instances</span>')
    precheck = row.extra.get("precheck")
    if isinstance(precheck, Mapping) and precheck.get("needs_your_eye"):
        header_bits.append('<span class="tag needseye">needs your eye</span>')
    folded_raw = row.extra.get("folded")
    folded: Mapping[str, object] = folded_raw if isinstance(folded_raw, Mapping) else {}
    if folded:
        applied_version = folded.get("gold_version") or current_version
        header_bits.append(f'<span class="tag done">applied v{_esc(applied_version)}</span>')
    if row.extra.get("machine_proposed"):
        header_bits.append('<span class="tag machine">machine-proposed</span>')

    detail: list[str] = [_paths(row)]
    if row.notes_text:
        detail.append(f'<div class="notes">{_esc(row.notes_text)}</div>')
    if row.extra.get("occurrences"):
        detail.append(
            f'<p class="note">appears in {_esc(row.extra["occurrences"])} gold cell(s)</p>'
        )
    if folded:
        detail.append(_folded_panel(folded))
    detail.append(_source_panel(row))
    detail.append(_reference_panels(row, current_version=current_version))
    # Every input stays enabled, folded or not (Damien, 2026-07-28) — a folded row differs only in
    # arriving pre-filled (``_proposed_panel`` reads ``row.extra["baseline"]``) and in carrying the
    # ``data-baseline`` JSON below, which the sheet's own JS diffs a re-submission against so an
    # untouched row still folds to nothing.
    baseline_raw = _review_baseline(row)
    proposed = _proposed_panel(row, baseline=baseline_raw)
    add_concept = (
        '<section class="add-concept"><strong>Add FOLIO concept</strong>'
        '<p class="note">Paste a FOLIO URL/IRI and label, then assign it to one or more levels.</p>'
        '<div class="add-concept-fields"><input class="add-label" type="text" placeholder="Concept label" '
        'aria-label="FOLIO concept label"><input class="add-iri" type="url" '
        'placeholder="https://folio.openlegalstandard.org/…" aria-label="FOLIO concept URL or IRI">'
        '<button class="secondary add-mapping" type="button">Add</button></div></section>'
    )
    detail.append(f'<p class="note mono">{_esc(row.decision_id)}</p>')

    baseline_attr = (
        f' data-baseline="{_esc(json.dumps(baseline_raw, sort_keys=True))}"' if baseline_raw else ""
    )
    return (
        f'<article class="row lvl {level_class}{" applied" if folded else ""}" '
        f'data-section="{_esc(row.section)}"{baseline_attr} '
        f'data-decision-id="{_esc(row.decision_id)}">'
        f"<header><h3>{_esc(row.surface_label)}</h3><div>{''.join(header_bits)}</div></header>"
        + _level_pane(row)
        + '<div class="pane-resizer level-resizer" role="separator" aria-label="Resize input hierarchy pane"></div>'
        + '<div class="mapping-pane"><p class="pane-heading">Mapped outputs · decide and assign</p>'
        + proposed
        + add_concept
        + '<button class="secondary mark-reviewed" type="button">Mark reviewed &amp; continue</button>'
        + '</div><div class="pane-resizer mapping-resizer" role="separator" aria-label="Resize mapped outputs pane"></div>'
        + '<div class="detail-pane"><section class="concept-inspector" aria-live="polite">'
        + '<h4>Select a mapped concept</h4><p class="note">Its definition, source, and current decision will appear here.</p></section>'
        + '<p class="pane-heading">Input evidence and context</p>'
        + "".join(detail)
        + "</div>"
        + "</article>"
    )


def _review_navigation(packet: Packet) -> str:
    """The persistent input hierarchy for the visual 1:many review workspace."""
    groups: list[str] = []
    for section in SECTIONS_V2:
        rows = packet.section(section)
        if not rows:
            continue
        title = SECTION_TITLES_V2[section][0]
        items: list[str] = []
        for row in rows:
            level_raw = row.extra.get("level")
            level = level_raw if isinstance(level_raw, int) else 1
            path = " \u203a ".join(_input_levels(row))
            concept_iris = {str(entry.get("iri", "")) for entry in row.gold}
            concept_iris.update(
                str(entry.get("iri", "")) for entry in _records(row.extra.get("gold_ref"))
            )
            if row.section == "pairing":
                for block in _records(row.extra.get("blocks")):
                    concept_iris.update(
                        str(tag.get("iri", "")) for tag in _records(block.get("tags"))
                    )
            concept_count = len({iri for iri in concept_iris if iri})
            precheck = row.extra.get("precheck")
            needs_eye = isinstance(precheck, Mapping) and bool(precheck.get("needs_your_eye"))
            folded = isinstance(row.extra.get("folded"), Mapping)
            items.append(
                f'<button class="review-item level-{min(max(level, 1), 4)}" type="button" '
                f'data-target="{_esc(row.decision_id)}" data-section="{_esc(section)}" '
                f'data-needs-eye="{str(needs_eye).lower()}" '
                f'data-decided="{str(folded).lower()}" '
                f'data-search="{_esc((path + " " + row.reason_class).lower())}">'
                f'<span class="item-label">{_esc(row.surface_label)}</span>'
                f'<span class="item-path">{_esc(path)}</span>'
                f'<span class="item-state" aria-label="{concept_count} concepts">'
                f"{'✓' if folded else concept_count}</span></button>"
            )
        groups.append(
            '<section class="nav-group">'
            f'<div class="nav-group-title"><span>{_esc(title)}</span><span>{len(rows)}</span></div>'
            + "".join(items)
            + "</section>"
        )
    return "".join(groups)


def _pairing_banner(packet: Packet) -> str:
    """Section A's standing instruction: the rule, what it pre-checked, and the pipeline's place.

    The rule itself is stated here, in committed code. Damien's own worked ruling is *not* — it
    names a practice area that is a firm surface string (KTD1), so it rides in on the packet's
    ``pairing_note``, which the runner reads from a gitignored file.
    """
    counts = packet.counts
    picked = (
        f"{counts.get('pairing_precheck_heuristic', 0)} pre-checked on Reading A (heuristic), "
        f"{counts.get('pairing_precheck_alternative', 0)} on Reading B (alternative), "
        f"{counts.get('pairing_needs_your_eye', 0)} badged &ldquo;needs your eye&rdquo;"
    )
    ruling = str(packet.meta.get("pairing_note", "") or "")
    return (
        '<div class="banner"><strong>The rule this section pre-checks by</strong>'
        "A reading is dis-preferred when it leaves an input cell mapping to nothing, or when it "
        "lands the same output concept on one input cell twice. The reading that survives is "
        "pre-checked — the "
        "heuristic wins every tie, because it is the reading already applied to gold, so a row "
        "you never touch folds to no change. Where both readings break the rule, nothing is "
        "pre-checked and the row is badged."
        f'<p class="note">{picked}.</p>'
        + (f'<p class="prefilled">Your ruling: {_esc(ruling)}</p>' if ruling else "")
        + '<p class="note">This section interprets your workbook&rsquo;s own mapping — the '
        "pipeline is not involved in the question, shown for reference only.</p>"
        "</div>"
    )


def _improvement_banner(packet: Packet) -> str:
    """Section F's standing caveat: what generated these, and what accepting one does."""
    counts = packet.counts
    items = counts.get("improvement_items", 0)
    proposals = counts.get("improvement_proposals", 0)
    per_item = round(proposals / items, 1) if items else 0
    return (
        '<div class="banner"><strong>These are machine proposals, not gold</strong>'
        "Nothing in this section is in gold, and nothing enters gold unless you accept it. Each "
        "proposal came from the cell&rsquo;s own words — either a FOLIO label search over its noun "
        "phrases, or one of the trigger&rarr;atom pairs read off your six worked corrections "
        "(Ship, Bank, Lawyer, Receiver, Borrower, Public Company, Financing Practice&hellip;). "
        "Proposals are confined to the atom branches: Actor / Player, Asset Type, Industry and "
        "Market, Service, Legal Entity, Objectives, Area of Law."
        f'<p class="note">{_esc(items)} cells, {_esc(proposals)} proposals '
        f"({_esc(per_item)} per cell), capped at {_esc(packet.meta.get('improvement_cap', 40))} "
        "cells for this pilot. Accepted concepts land as provenance=damien_corrected; rejected "
        "ones are remembered and never proposed again unchanged.</p>"
        "</div>"
    )


def _overflow_banner(packet: Packet) -> str:
    """Make a capped suspect queue impossible to mistake for the complete audit."""
    if not packet.overflow:
        return ""
    spilled = ", ".join(
        f"{_esc(reason)}: {count}" for reason, count in sorted(packet.overflow.items())
    )
    return (
        '<p class="overflow"><strong>More suspect rows remain.</strong> Beyond the '
        f"{_esc(packet.meta.get('suspect_cap', 50))}-row cap, held for the next batch — "
        f"{spilled}.</p>"
    )


def _metrics_table(metrics: Mapping[str, object]) -> str:
    """v1 vs v2 on the numbers that moved: the whole reason gold was re-derived."""
    rows_wanted = (
        ("tune_items", "tune items"),
        ("tune_gold_iris", "tune gold IRIs"),
        ("tune_precision", "tune precision"),
        ("tune_recall", "tune recall"),
        ("tune_f1", "tune F1"),
        ("firm2_items", "Firm-2 items"),
        ("firm2_precision", "Firm-2 precision"),
        ("firm2_recall", "Firm-2 recall"),
        ("firm2_f1", "Firm-2 F1"),
        ("recall_at_1", "recall@1 (tune)"),
        ("recall_at_5", "recall@5 (tune)"),
        ("recall_at_10", "recall@10 (tune)"),
        ("items_total", "gold items total"),
        ("items_scored", "gold items scored"),
        ("frozen", "frozen slice"),
    )
    first = _mapping(metrics.get("v1"))
    second = _mapping(metrics.get("v2"))
    if not first and not second:
        return ""

    def _cell(source: Mapping[str, object], key: str) -> str:
        value = source.get(key)
        if isinstance(value, float):
            return f"{value:.4f}"
        return _esc(value) if value is not None else "—"

    body = "".join(
        f"<tr><td>{_esc(title)}</td><td>{_cell(first, key)}</td><td>{_cell(second, key)}</td></tr>"
        for key, title in rows_wanted
        if key in first or key in second
    )
    return (
        '<div class="tablewrap"><table class="scroll-wrap metrics">'
        "<thead><tr><th>measure</th><th>gold v1 (cascade)</th><th>gold v2 (per-cell)</th></tr>"
        f"</thead><tbody>{body}</tbody></table></div>"
    )


def render_sheet_v2(packet: Packet) -> str:
    """Render the durable three-pane 1:many adjudication workspace."""
    meta = dict(packet.meta)
    metrics = meta.get("metrics")
    current_version_raw = meta.get("current_gold_version") or meta.get("gold_version") or 0
    current_version = int(current_version_raw) if isinstance(current_version_raw, (int, str)) else 0
    articles = "".join(_render_row_v2(row, current_version=current_version) for row in packet.rows)
    guidance = _pairing_banner(packet) if packet.section("pairing") else ""
    if packet.section("improvement"):
        guidance += _improvement_banner(packet)
    guidance += _overflow_banner(packet)
    baseline_id = meta.get("current_gold_id") or meta.get("gold_id") or meta.get("parent_gold_id")
    packet_fingerprint = hashlib.sha256(
        json.dumps(
            [row.to_json() for row in packet.rows],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    packet_key = "|".join(
        (
            str(baseline_id or f"gold-v{current_version}"),
            str(meta.get("ontology_sha256") or "ontology-unknown"),
            str(len(packet.rows)),
            packet_fingerprint,
        )
    )
    section_markers = "".join(
        f'<span hidden data-section="{_esc(name)}"></span>' for name in SECTION_TITLES_V2
    )

    counts = json.dumps(dict(packet.counts), indent=2, sort_keys=True)
    header_meta = json.dumps(
        {key: value for key, value in meta.items() if key != "metrics"}, indent=2, sort_keys=True
    )
    return (
        '<!doctype html>\n<html lang="en" data-theme="light"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>folio-resolve — audit gate (gold v2, per-cell)</title>"
        f'<style>{_STYLE_V2}</style></head><body class="eval-workspace"><main '
        f'data-review-workspace="folio-eval-v1" data-packet-key="{_esc(packet_key)}">'
        '<header class="review-header"><div class="review-titlebar"><div>'
        "<h1>Gold evaluation workspace</h1>"
        '<p class="lede">Select an input, inspect its 1:many mappings, decide each concept, and add notes.</p>'
        '</div><div class="review-progress"><div class="progress-track" aria-hidden="true">'
        '<div class="progress-fill" id="progress-fill"></div></div>'
        '<strong id="progress-count">0 reviewed</strong></div></div>'
        '<div class="review-toolbar">'
        '<input id="review-search" type="search" placeholder="Search input labels or paths" '
        'aria-label="Search evaluation items">'
        '<select id="status-filter" aria-label="Filter evaluation items by status">'
        '<option value="undecided">Undecided</option><option value="needs-eye">Needs your eye</option>'
        '<option value="all">All items</option><option value="decided">Reviewed</option></select>'
        '<button class="secondary" id="previous-row" type="button">↑ Previous</button>'
        '<button class="secondary" id="next-row" type="button">↓ Next</button>'
        '<span class="spacer"></span><span class="draft-state" id="draft-state">Draft not yet saved</span>'
        '<button class="secondary" id="theme-toggle" type="button">Dark mode</button>'
        '<button class="secondary" id="download" type="button">Download JSON</button>'
        '<button id="copy" type="button">Copy decisions</button></div>'
        '<details class="actions"><summary>Decision JSON, audit metadata, and section guidance '
        '<span class="tag" id="count">0 rows decided</span></summary>'
        '<textarea id="out" readonly aria-label="assembled decisions JSON"></textarea>'
        '<p id="hint">Paste this back into the chat. If clipboard access is blocked, the text '
        "above is selected — press ⌘C / Ctrl+C.</p>"
        + guidance
        + (_metrics_table(metrics) if isinstance(metrics, Mapping) else "")
        + f'<pre class="meta">{_esc(header_meta)}</pre><pre class="meta">{_esc(counts)}</pre>'
        + "</details></header>"
        + '<div class="workspace-shell"><svg id="mapping-lines" aria-hidden="true"></svg>'
        + section_markers
        + '<aside class="review-sidebar"><p class="sidebar-label">Input items</p>'
        + '<nav aria-label="Evaluation items">'
        + _review_navigation(packet)
        + '</nav></aside><div class="pane-resizer sidebar-resizer" role="separator" '
        + 'aria-label="Resize input items pane"></div>'
        + '<section class="review-stage"><div class="empty-review">No evaluation items match this filter.</div>'
        + articles
        + "</section></div></main>"
        + f"<script>{_SCRIPT_V2_WORKSPACE}</script></body></html>\n"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def write_packet_v2(
    packet: Packet,
    out_dir: Path,
    *,
    lane: SittingLane,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
) -> dict[str, Path]:
    """Write the v2 ``packet.json`` + ``sheet.html`` into a gitignored packet directory."""
    target = validate_sitting_output(out_dir, lane=lane)
    packet_path = target / "packet.json"
    sheet_path = target / "sheet.html"
    packet_text = json.dumps(packet.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    sheet_text = render_sheet_v2(packet)
    if lane == "synthetic":
        if leak_manifest is None or salt is None:
            raise ValueError("synthetic packet writes require a leak manifest and salt")
        collisions = scan_text(packet_text, leak_manifest, salt) + scan_text(
            sheet_text, leak_manifest, salt
        )
        if collisions:
            raise ValueError(f"synthetic packet leak check failed: collisions={collisions}")
    _atomic_write_text(packet_path, packet_text)
    _atomic_write_text(sheet_path, sheet_text)
    return {"packet": packet_path, "sheet": sheet_path}


def validate_sitting_output(out_dir: Path, *, lane: SittingLane) -> Path:
    """Return the canonical safe output directory, or reject the unavailable/wrong lane."""
    if lane == "synthetic":
        return out_dir.resolve()
    if lane != "firm":
        raise ValueError("sitting lane must be 'firm' or 'synthetic'")
    reports_root = (DEFAULT_DATA_DIR / "reports").resolve()
    target = out_dir.resolve()
    if not target.is_relative_to(reports_root):
        raise ValueError(
            f"firm-lane sittings must be written under gitignored eval/data/reports/: {out_dir}"
        )
    return target


def write_sitting_v2(
    packet: Packet,
    manifest: SittingManifest,
    out_dir: Path,
    *,
    lane: SittingLane,
    leak_manifest: Manifest | None = None,
    salt: bytes | None = None,
    firm_sheet_empty: bool,
) -> dict[str, Path]:
    """Write one sitting, enforcing the U2/U7 firm-data lane boundary at the writer seam."""
    target = validate_sitting_output(out_dir, lane=lane)
    if lane == "synthetic":
        if not firm_sheet_empty:
            raise ValueError("synthetic sittings require the firm sheet to be empty")
        if any(row.firm != "synthetic" for row in packet.rows):
            raise ValueError("synthetic sittings may contain only synthetic rows")
        if len(packet.rows) > 25 or len(manifest.rows) > 25 or manifest.batch_size > 25:
            raise ValueError("synthetic sitting batches are capped at 25 rows")
        if leak_manifest is None or salt is None:
            raise ValueError("synthetic sittings require a leak manifest and salt")
    sheet_path = target / f"sitting_{manifest.number}.html"
    manifest_path = target / f"sitting_{manifest.number}.json"
    sheet_text = render_sheet_v2(packet)
    manifest_text = json.dumps(manifest.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if lane == "synthetic":
        assert leak_manifest is not None and salt is not None
        collisions = scan_text(sheet_text, leak_manifest, salt) + scan_text(
            manifest_text, leak_manifest, salt
        )
        if collisions:
            raise ValueError(f"synthetic sitting leak check failed: collisions={collisions}")
    _atomic_write_text(sheet_path, sheet_text)
    _atomic_write_text(manifest_path, manifest_text)
    return {"sheet": sheet_path, "manifest": manifest_path}


def write_packet(packet: Packet, out_dir: Path) -> dict[str, Path]:
    """Write ``packet.json`` + ``sheet.html`` into a gitignored packet directory."""
    packet_path = out_dir / "packet.json"
    sheet_path = out_dir / "sheet.html"
    _atomic_write_json(packet_path, packet.to_json())
    _atomic_write_text(sheet_path, render_sheet(packet))
    return {"packet": packet_path, "sheet": sheet_path}
