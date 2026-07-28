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

import html
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .audit import Packet, PacketRow

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
  --code: #f2efe6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14150f; --fg: #eceadf; --muted: #a09c8c; --line: #34362b;
    --card: #1c1e16; --accent: #d9a066; --good: #7fbf95; --warn: #e08b8b;
    --code: #23261c;
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
       border: 1px solid var(--line); border-radius: 999px; padding: .1rem .55rem; }
.tag.reason { color: var(--accent); border-color: var(--accent); }
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
            score = f'score {_esc(entry.get("score"))}'
            if probability is not None:
                score += f' · p={_esc(probability)}'
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
            f'{_iri_list(row.pipeline, kind="pipeline")}</div>'
            "</div>"
        )
    if row.notes_text:
        body.append(f'<div class="notes">{_esc(row.notes_text)}</div>')
    if row.extra.get("occurrences"):
        body.append(
            f'<p class="note">appears in {_esc(row.extra["occurrences"])} gold cell(s)</p>'
        )
    body.append(f'<p class="note">{_esc(row.suggested_action)}</p>')

    return (
        f'<article class="row" data-section="{_esc(row.section)}" '
        f'data-decision-id="{_esc(row.decision_id)}">'
        f'<header><h3>{_esc(row.surface_label)}</h3><div>{"".join(header_bits)}</div></header>'
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
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
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
              letter-spacing: .06em; color: var(--muted); }
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
"""
)

_SCRIPT_V2 = """
function verdicts(row, selector) {
  const out = {};
  row.querySelectorAll(selector + ' li[data-iri]').forEach(function (li) {
    const picked = li.querySelector('input[type=radio]:checked');
    if (picked) { out[li.getAttribute('data-iri')] = picked.value; }
  });
  return out;
}
function collect() {
  const decisions = {};
  document.querySelectorAll('.row[data-decision-id]').forEach(function (row) {
    const id = row.getAttribute('data-decision-id');
    const entry = {};
    const gold = verdicts(row, '.block.gold');
    if (Object.keys(gold).length) { entry.gold = gold; }
    const pipeline = verdicts(row, '.block.pipeline');
    if (Object.keys(pipeline).length) { entry.pipeline = pipeline; }
    const pairing = row.querySelector('input[data-kind=pairing]:checked');
    if (pairing) { entry.pairing = pairing.value; }
    const rowNote = row.querySelector('textarea.row-note');
    if (rowNote && rowNote.value.trim()) { entry.note = rowNote.value.trim(); }
    const goldNote = row.querySelector('textarea.gold-note');
    if (goldNote && goldNote.value.trim()) { entry.gold_note = goldNote.value.trim(); }
    const pipeNote = row.querySelector('textarea.pipeline-note');
    if (pipeNote && pipeNote.value.trim()) { entry.pipeline_note = pipeNote.value.trim(); }
    if (Object.keys(entry).length) { decisions[id] = entry; }
  });
  return decisions;
}
function refresh() {
  const decisions = collect();
  document.getElementById('out').value = JSON.stringify(decisions, null, 2);
  document.getElementById('count').textContent = Object.keys(decisions).length + ' rows decided';
}
document.addEventListener('change', function (event) {
  if (event.target.matches('input[data-kind=pairing]')) {
    const row = event.target.closest('.row');
    row.querySelectorAll('.pairing .opt').forEach(function (opt) {
      opt.classList.toggle('picked', opt.getAttribute('data-choice') === event.target.value);
    });
  }
  refresh();
});
document.addEventListener('input', function (event) {
  if (event.target.matches('textarea.note')) { refresh(); }
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
    hint.textContent = 'Clipboard blocked \\u2014 the text is selected above, press \\u2318C / Ctrl+C.';
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


def _mapping(value: object) -> Mapping[str, object]:
    """A nested JSON object read defensively: anything that is not a mapping reads as empty."""
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _grade_list(
    entries: Sequence[Mapping[str, object]],
    *,
    decision_id: str,
    kind: str,
    prefill: Mapping[str, object],
) -> str:
    """One radio pair per concept: the granular grading Damien asked for."""
    if not entries:
        return '<p class="note">none</p>'
    choices = (
        (("keep", "Keep gold"), ("remove", "Remove from gold"))
        if kind == "gold"
        else (("elevate", "Elevate to gold"), ("not_gold", "Not gold"))
    )
    items: list[str] = []
    for position, entry in enumerate(entries):
        iri = str(entry.get("iri", ""))
        name = f"{kind}|{decision_id}|{position}"
        bits = [f'<strong>{_esc(entry.get("label", ""))}</strong> <code>{_esc(_short(iri))}</code>']
        if kind != "gold":
            score = entry.get("score")
            if score is not None:
                probability = entry.get("probability")
                tag = f"score {_esc(score)}"
                if probability is not None:
                    tag += f" · p={_esc(probability)}"
                bits.append(f'<span class="tag">{tag}</span>')
            if entry.get("extraction_path"):
                bits.append(f'<span class="tag">{_esc(entry["extraction_path"])}</span>')
            if entry.get("already_gold"):
                bits.append('<span class="tag good">already gold</span>')
        elif entry.get("column"):
            bits.append(f'<span class="tag">{_esc(entry["column"])}</span>')
        line = " ".join(bits)
        if entry.get("definition"):
            line += f'<span class="def">{_esc(entry["definition"])}</span>'
        picked = str(prefill.get(iri, "")) if isinstance(prefill, Mapping) else ""
        radios = "".join(
            f'<label><input type="radio" name="{_esc(name)}" value="{value}"'
            f'{" checked" if picked == value else ""}> {label}</label>'
            for value, label in choices
        )
        items.append(
            f'<li data-iri="{_esc(iri)}"><div>{line}</div>'
            f'<div class="verdict">{radios}</div></li>'
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
        chips.append(f'<span>L{_esc(instance.get("level", ""))} {_esc(path)}</span>')
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
            body.append(
                f'<tr><th class="rownum">{_esc(record.get("row", ""))}</th>{rendered}</tr>'
            )
        if not body:
            continue
        tables.append(
            '<div class="tablewrap"><table class="sheetgrid"><thead><tr>'
            f'<th class="rownum">row</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
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
            f'<strong>{_esc(entry.get("label", ""))}</strong> '
            f'<code>{_esc(_short(str(entry.get("iri", ""))))}</code>'
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
        score = f'score {_esc(entry.get("score"))}'
        probability = entry.get("probability")
        if probability is not None:
            score += f" · p={_esc(probability)}"
        bits = [
            f'<span class="tag">#{_esc(entry.get("rank", ""))}</span>',
            f'<strong>{_esc(entry.get("label", ""))}</strong>',
            f'<code>{_esc(_short(str(entry.get("iri", ""))))}</code>',
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
        f'(KTD2 answer rule); showing {_esc(len(candidates))} of {_esc(total)} ranked candidates.'
        "</p>"
    )
    return lede + '<ul class="iris">' + "".join(items) + "</ul>"


def _reference_panels(row: PacketRow) -> str:
    """Panels 1 and 2, on every row of every section, labelled identically throughout."""
    if row.section == "pairing":
        context = _records(row.extra.get("input_context"))
        gold_body = "".join(
            f'<div class="perinput"><h5>L{_esc(entry.get("level", ""))} · '
            f'{_esc(entry.get("text", ""))}</h5>'
            f'{_gold_reference(_records(entry.get("gold")))}</div>'
            for entry in context
        )
        pipe_body = "".join(
            f'<div class="perinput"><h5>L{_esc(entry.get("level", ""))} · '
            f'{_esc(entry.get("text", ""))}</h5>'
            f'{_pipeline_reference(entry.get("pipeline"))}</div>'
            for entry in context
        )
    else:
        gold_raw = row.extra.get("gold_ref")
        gold_body = _gold_reference(
            _records(gold_raw) if isinstance(gold_raw, (list, tuple)) else list(row.gold)
        )
        pipe_body = _pipeline_reference(row.extra.get("pipeline_ref"))
    return (
        '<section class="panel ref-gold"><h4>Gold — curated in your workbook'
        '<span class="who">your spreadsheet, as this harness derives it</span></h4>'
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


def _pairing_readings(row: PacketRow) -> str:
    """Reading A / Reading B, with the one that survives Damien's principle pre-checked."""
    assignments = row.extra.get("assignments")
    prechecked = row.extra.get("precheck")
    violations: Mapping[str, object] = prechecked if isinstance(prechecked, Mapping) else {}
    choice = str(violations.get("choice", "heuristic"))
    out: list[str] = []
    blocks = row.extra.get("blocks")
    if isinstance(blocks, (list, tuple)) and blocks:
        rendered = " · ".join(
            f'{_esc(block.get("column", ""))}: '
            f'{_esc(", ".join(_strings(block.get("values"))))}'
            for block in _records(blocks)
        )
        out.append(f'<p class="blocks">outputs on this row — {rendered}</p>')
    if not isinstance(assignments, Mapping):
        return "".join(out)
    panels: list[str] = []
    for reading, title, key in (
        ("heuristic", "Reading A — heuristic (applied to gold today)", "heuristic_violations"),
        ("alternative", "Reading B — alternative", "alternative_violations"),
    ):
        lines: list[str] = []
        for entry in _records(assignments.get(reading)):
            labels = ", ".join(_strings(entry.get("labels")))
            lines.append(
                f'<li class="instance"><strong>L{_esc(entry.get("level", ""))} '
                f'{_esc(entry.get("input", ""))}</strong> → '
                + (_esc(labels) if labels else "<em>— nothing —</em>")
                + "</li>"
            )
        broken = [name.replace("_", " ") for name in _strings(violations.get(key))]
        flag = (
            '<p class="note"><span class="tag needseye">dis-preferred</span> '
            f'{_esc(", ".join(broken))}</p>'
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
    if not choice:
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
            f'L{_esc(instance.get("level", ""))} {_esc(path)} → '
            f'{_esc(labels or "— no mapping —")}</li>'
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
}


def _row_note(row: PacketRow) -> str:
    """The note field that now sits under every decision unit, pairing and consistency included."""
    return (
        f'<textarea class="note row-note rownote" rows="2" '
        f'name="note|{_esc(row.decision_id)}" '
        f'aria-label="note on this decision" '
        f'placeholder="note on this decision (optional)"></textarea>'
    )


def _proposed_panel(row: PacketRow) -> str:
    prefill_raw = row.extra.get("prefill")
    prefill: Mapping[str, object] = prefill_raw if isinstance(prefill_raw, Mapping) else {}
    gold_prefill = _mapping(prefill.get("gold"))
    pipe_prefill = _mapping(prefill.get("pipeline"))
    body: list[str] = []
    if row.section == "pairing":
        body.append(_pairing_readings(row))
    else:
        if row.section == "consistency":
            body.append(_consistency_block(row))
        body.append(
            '<div class="block gold"><h4>Gold — keep or remove</h4>'
            + _grade_list(row.gold, decision_id=row.decision_id, kind="gold", prefill=gold_prefill)
            + f'<textarea class="note gold-note" rows="2" '
            f'name="gold-note|{_esc(row.decision_id)}" '
            f'aria-label="note on this cell&rsquo;s gold" '
            f'placeholder="note on this cell&rsquo;s gold (optional)"></textarea></div>'
        )
        pipeline_title = (
            "FOLIO proposals — elevate the right one"
            if row.section == "resolution"
            else "Pipeline candidates — elevate or reject"
        )
        body.append(
            f'<div class="block pipeline"><h4>{_esc(pipeline_title)}</h4>'
            + _grade_list(
                row.pipeline, decision_id=row.decision_id, kind="pipeline", prefill=pipe_prefill
            )
            + f'<textarea class="note pipeline-note" rows="2" '
            f'name="pipeline-note|{_esc(row.decision_id)}" '
            f'aria-label="note on the pipeline&rsquo;s answer" '
            f'placeholder="note on the pipeline&rsquo;s answer (optional)"></textarea></div>'
        )
    if prefill.get("note"):
        body.append(f'<p class="prefilled">Pre-filled: {_esc(prefill["note"])}</p>')
    title = PROPOSED_TITLES.get(row.section, "Proposed — this sheet&rsquo;s question")
    return (
        f'<section class="panel proposed"><h4>{title}'
        '<span class="who">what this gate is asking you to decide</span></h4>'
        + "".join(body)
        + _row_note(row)
        + f'<p class="note">{_esc(row.suggested_action)}</p>'
        + "</section>"
    )


def _render_row_v2(row: PacketRow) -> str:
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

    body: list[str] = [_paths(row)]
    if row.notes_text:
        body.append(f'<div class="notes">{_esc(row.notes_text)}</div>')
    if row.extra.get("occurrences"):
        body.append(f'<p class="note">appears in {_esc(row.extra["occurrences"])} gold cell(s)</p>')
    body.append(_source_panel(row))
    body.append(_reference_panels(row))
    body.append(_proposed_panel(row))
    body.append(f'<p class="note mono">{_esc(row.decision_id)}</p>')

    return (
        f'<article class="row lvl {level_class}" data-section="{_esc(row.section)}" '
        f'data-decision-id="{_esc(row.decision_id)}">'
        f'<header><h3>{_esc(row.surface_label)}</h3><div>{"".join(header_bits)}</div></header>'
        + "".join(body)
        + "</article>"
    )


def _pairing_banner(packet: Packet) -> str:
    """Section A's standing instruction: the rule, what it pre-checked, and the pipeline's place.

    The rule itself is stated here, in committed code. Damien's own worked ruling is *not* — it
    names a practice area that is a firm surface string (KTD1), so it rides in on the packet's
    ``pairing_note``, which the runner reads from a gitignored file.
    """
    counts = packet.counts
    picked = (
        f'{counts.get("pairing_precheck_heuristic", 0)} pre-checked on Reading A (heuristic), '
        f'{counts.get("pairing_precheck_alternative", 0)} on Reading B (alternative), '
        f'{counts.get("pairing_needs_your_eye", 0)} badged &ldquo;needs your eye&rdquo;'
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
    """The v2 decision sheet: one self-contained page, per-concept grading throughout."""
    meta = dict(packet.meta)
    metrics = meta.get("metrics")
    sections: list[str] = []
    for name in ("pairing", "consistency", "suspect", "resolution", "new_gold"):
        title, lede = SECTION_TITLES_V2[name]
        rows = packet.section(name)
        chunk = [f'<section data-section="{name}"><h2>{_esc(title)}</h2>']
        chunk.append(f'<p class="lede">{_esc(lede)}</p>')
        if name == "pairing":
            chunk.append(_pairing_banner(packet))
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
        group = object()
        for row in rows:
            if row.stratum and row.stratum != group:
                group = row.stratum
                chunk.append(f'<p class="groupbar">{_esc(row.stratum)}</p>')
            chunk.append(_render_row_v2(row))
        chunk.append("</section>")
        sections.append("".join(chunk))

    counts = json.dumps(dict(packet.counts), indent=2, sort_keys=True)
    header_meta = json.dumps(
        {key: value for key, value in meta.items() if key != "metrics"}, indent=2, sort_keys=True
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>folio-resolve — audit gate (gold v2, per-cell)</title>"
        f"<style>{_STYLE_V2}</style></head><body><main>"
        "<h1>Audit gate — gold v2 (per-cell)</h1>"
        '<p class="lede">Every input label cell is its own question now: Level 1, Level 2 and '
        "Level 3 cells each carry their own mapping, and nothing inherits. Grade each concept on "
        "its own line — gold keeps or goes, pipeline candidates rise or do not. Nothing changes "
        "gold until these decisions are folded in.</p>"
        + (_metrics_table(metrics) if isinstance(metrics, Mapping) else "")
        + f'<pre class="meta">{_esc(header_meta)}</pre>'
        f'<pre class="meta">{_esc(counts)}</pre>'
        f"{''.join(sections)}"
        '<div class="actions">'
        '<button id="copy" type="button">Copy decisions</button> '
        '<span class="tag" id="count">0 rows decided</span>'
        '<textarea id="out" readonly aria-label="assembled decisions JSON"></textarea>'
        '<p id="hint">Paste this back into the chat. If the copy button is blocked, the text '
        "above is already selected — press ⌘C / Ctrl+C.</p>"
        "</div></main>"
        f"<script>{_SCRIPT_V2}</script></body></html>\n"
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


def write_packet_v2(packet: Packet, out_dir: Path) -> dict[str, Path]:
    """Write the v2 ``packet.json`` + ``sheet.html`` into a gitignored packet directory."""
    packet_path = out_dir / "packet.json"
    sheet_path = out_dir / "sheet.html"
    _atomic_write_text(
        packet_path,
        json.dumps(packet.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(sheet_path, render_sheet_v2(packet))
    return {"packet": packet_path, "sheet": sheet_path}


def write_packet(packet: Packet, out_dir: Path) -> dict[str, Path]:
    """Write ``packet.json`` + ``sheet.html`` into a gitignored packet directory."""
    packet_path = out_dir / "packet.json"
    sheet_path = out_dir / "sheet.html"
    _atomic_write_text(
        packet_path, json.dumps(packet.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_text(sheet_path, render_sheet(packet))
    return {"packet": packet_path, "sheet": sheet_path}
