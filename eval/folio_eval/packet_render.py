"""Render the audit-gate packet to a machine file and a self-contained decision sheet (U5, KTD9).

Two artefacts land in gitignored ``eval/data/reports/audit_packet_v1/``:

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


def write_packet(packet: Packet, out_dir: Path) -> dict[str, Path]:
    """Write ``packet.json`` + ``sheet.html`` into a gitignored packet directory."""
    packet_path = out_dir / "packet.json"
    sheet_path = out_dir / "sheet.html"
    _atomic_write_text(
        packet_path, json.dumps(packet.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_text(sheet_path, render_sheet(packet))
    return {"packet": packet_path, "sheet": sheet_path}
