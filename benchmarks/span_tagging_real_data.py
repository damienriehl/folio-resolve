"""Offline full-lexicon check; writes public receipts and retains a temporary lexicon."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from folio_resolve import Concept, InMemoryOntology, __version__
from folio_resolve.entity_ruler import _STOPWORDS
from folio_resolve.spans import SpanTagger, load_crosswalk, load_lexicon
from folio_resolve.spans.lexicon import PINNED_SHA256, OpenGlossLexicon
from folio_resolve.spans.tokenize import tokenize

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/benchmarks/span-tagging-real-data.json"
OWL = "http://www.w3.org/2002/07/owl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
SAMPLE = "The tenant acted in good faith."


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_folio(path: Path) -> list[Concept]:
    """Read local named classes only; no imports, inferred ancestry, or network.

    folio-python is optional and its constructor supports GitHub/HTTP sources,
    so this benchmark uses ElementTree. Merge duplicate class entries by IRI;
    prefer English, then untagged labels, with deterministic lexical ties.
    Preserve every remaining label as an alternative and join all definitions.
    """
    records: dict[str, dict[str, set[tuple[int, str]]]] = {}
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != f"{{{OWL}}}Class":
            continue
        iri = element.get(f"{{{RDF}}}about")
        if iri and not iri.startswith(OWL):
            record = records.setdefault(iri, {})
            for child in element:
                if child.tag not in {
                    f"{{{RDFS}}}label",
                    f"{{{SKOS}}}prefLabel",
                    f"{{{SKOS}}}altLabel",
                    f"{{{SKOS}}}definition",
                }:
                    continue
                text = "".join(child.itertext()).strip()
                if text:
                    language = child.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                    priority = (
                        0 if language.lower().split("-")[0] == "en" else 1 if not language else 2
                    )
                    record.setdefault(child.tag, set()).add((priority, text))
        element.clear()
    concepts = []
    for iri, record in sorted(records.items()):
        labels = [text for _, text in sorted(record.get(f"{{{RDFS}}}label", set()))]
        preferred = [text for _, text in sorted(record.get(f"{{{SKOS}}}prefLabel", set()))]
        alternatives = {text for _, text in record.get(f"{{{SKOS}}}altLabel", set())}
        alternatives.update(labels[1:])
        alternatives.update(preferred[1:])
        definitions = sorted({text for _, text in record.get(f"{{{SKOS}}}definition", set())})
        concepts.append(
            Concept(
                iri=iri,
                label=labels[0] if labels else preferred[0] if preferred else iri,
                preferred_label=preferred[0] if preferred else None,
                alternative_labels=tuple(sorted(alternatives)),
                definition="\n".join(definitions) or None,
            )
        )
    return concepts


def rss_bytes(who: int) -> int:
    # Linux reports KiB, macOS bytes.
    scale = 1 if sys.platform == "darwin" else 1024
    return int(resource.getrusage(who).ru_maxrss * scale)


def check_headword_self_matches(tagger: SpanTagger, lexicon: OpenGlossLexicon) -> dict:
    """Exercise every distinct surface through the tagger, explaining filtered cases."""
    headwords = sorted(
        {sense.headword for senses in lexicon.by_headword.values() for sense in senses}
    )
    unmatched: dict[str, list[str]] = {
        "function_word_only": [],
        "no_word_tokens": [],
        "unexpected": [],
    }
    started = time.perf_counter()
    for headword in headwords:
        result = tagger.tag(headword)
        if any(
            anchor.headword == headword and anchor.match_method == "exact"
            for span in result.spans
            for anchor in span.opengloss
        ):
            continue
        words = [token.key for token in tokenize(headword)]
        if not words:
            reason = "no_word_tokens"
        elif all(
            word in _STOPWORDS or not lexicon.is_content_word(word)
            for word in words
        ):
            reason = "function_word_only"
        else:
            reason = "unexpected"
        unmatched[reason].append(headword)
    unmatched_count = sum(len(words) for words in unmatched.values())
    return {
        "headword_count": len(headwords),
        "matched_count": len(headwords) - unmatched_count,
        "unmatched_count": unmatched_count,
        "unexpected_unmatched_count": len(unmatched["unexpected"]),
        "unmatched_by_reason": {reason: len(words) for reason, words in unmatched.items()},
        "unmatched_examples_by_reason": {reason: words[:20] for reason, words in unmatched.items()},
        "check_seconds": time.perf_counter() - started,
    }


def write_receipt(receipt: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lexicon = receipt["lexicon"]
    corpus = receipt["corpus"]
    budget = receipt["budget"]
    self_match = receipt["headword_self_match"]
    properties = "\n".join(
        f"| `{sense['id']}` | {sense['domain'] or '(none)'} |"
        for sense in receipt["property"]["senses"]
    )
    crosswalk = receipt["crosswalk"]
    crosswalk_text = "Crosswalk: not provided; no real crosswalk links were evaluated."
    if crosswalk != "not provided":
        predicates = "\n".join(
            f"| `{predicate}` | {count:,} | {crosswalk['corpus_links_by_predicate'][predicate]:,} |"
            for predicate, count in crosswalk["rows_by_predicate"].items()
        )
        crosswalk_text = f"""Crosswalk: **{crosswalk['row_count']:,} rows**;
**{crosswalk['corpus_links']:,} corpus links**.

- FOLIO commit: `{crosswalk['source_commit'] or '(not supplied)'}`.
- File SHA-256: `{crosswalk['sha256']}`.
- Mapping set: `{crosswalk['mapping_set_id']}`, version `{crosswalk['mapping_set_version']}`.

| Predicate | Mapping rows | Corpus links |
|---|---:|---:|
{predicates}"""
    text = f"""# OpenGloss + FOLIO span-tagging real-data check

## Protocol

Recorded {receipt["recorded_at"]}. Built the complete pinned parquet dataset with
the package's build CLI in a separate process, into a temporary directory outside
the repository; loaded it with `load_lexicon`. Read the local FOLIO.owl offline
with stdlib ElementTree into `InMemoryOntology` (folio-python is not installed;
its constructor supports GitHub/HTTP sources). Named classes are merged by IRI;
English then untagged labels are preferred, with lexical ties. All labels and
definitions are retained; no branch ancestry or OWL imports are inferred.

Tagged **all {corpus["text_count"]:,} nonempty concept definitions**, sorted by
IRI, from {corpus["concept_count"]:,} concepts. Timings include result assembly,
but exclude serialization. Results are counted and discarded per text.
Peak RSS uses `resource.getrusage`: the larger of runtime peak and builder peak
plus the parent's pre-build peak (a conservative allowance for their overlap).
The enclosing worker memory scope remains in force. Timings describe one run,
without emptying OS caches, and are not service-level guarantees.

## Results

| Measurement | Result |
|---|---:|
| Lexicon build | {lexicon["build_seconds"]:.3f} s |
| Compressed artifact | {lexicon["artifact_bytes"]:,} bytes |
| Lexicon rows | {lexicon["row_count"]:,} |
| Verified lexicon load | {lexicon["load_seconds"]:.3f} s |
| SpanTagger construction | {receipt["construction_seconds"]:.3f} s |
| Peak RSS | {receipt["peak_rss_bytes"] / 2**20:.1f} MiB |
| Corpus characters | {corpus["characters"]:,} |
| Tagging time | {corpus["tagging_seconds"]:.3f} s |
| Throughput | {corpus["characters_per_second"]:,.1f} characters/s |
| Mean spans per text | {corpus["spans_per_text"]:.3f} |
| Distinct OpenGloss headwords checked against own surface | {self_match["headword_count"]:,} |
| Headwords matching their own exact anchor | {self_match["matched_count"]:,} |
| Headwords without their own anchor | {self_match["unmatched_count"]:,} |
| Unexpected self-match failures | {self_match["unexpected_unmatched_count"]:,} |

Budget verdict: **{budget["verdict"]}**. Construction must be strictly below
60 s ({budget["construction_under_60_seconds"]}); peak RSS strictly below
3 GiB ({budget["peak_rss_under_3_gib"]}). Failed lines: {", ".join(budget["failed_lines"]) or "none"}.

## Headword self-match

Tagged every distinct OpenGloss headword's own text and checked for an exact
OpenGloss anchor carrying that same headword. This includes punctuation-bearing
headwords. The check took {self_match["check_seconds"]:.3f} s, separately from corpus throughput.
Of {self_match["unmatched_count"]:,} unmatched headwords,
{self_match["unmatched_by_reason"]["function_word_only"]:,} are intentionally excluded because every
token is a ruler stopword or a function word under the lexicon's POS evidence,
with the static function-word fallback used for absent single-token entries;
{self_match["unmatched_by_reason"]["no_word_tokens"]:,} have no word tokens.
Unexpected failures: **{self_match["unexpected_unmatched_count"]:,}**.
The JSON receipt records counts and up to twenty examples for each reason.
The lexicon's content senses override the static function-word fallback:
“will” retains both noun senses, including `will:noun:0` and `will:noun:1`:
**{receipt["will"]["passed"]}**.

## Phrase ranking and property observation

On “{SAMPLE}”, OpenGloss “good faith” ranks primary and both “good” and “faith”
rank fallback, naming the phrase: **{receipt["good_faith"]["passed"]}**.
Two serialized outputs for that text are byte-identical:
**{receipt["determinism"]["identical"]}**.

OpenGloss senses tagged on “property”:

| Sense id | Domain |
|---|---|
{properties}

These are the dataset's senses, with no disambiguation. The spike reported a
missing legal sense; domain labels alone cannot establish semantic coverage.
This check asserts presence and records the gap rather than repairing the data.

{crosswalk_text}

## Provenance

- Dataset: `{lexicon["dataset_id"]}` at `{lexicon["revision"]}`.
- Uncompressed lexicon content SHA-256: `{lexicon["content_sha256"]}`.
- FOLIO.owl SHA-256, passed as `folio_release`: `{receipt["folio_release"]}`.
- folio-resolve `{receipt["folio_resolve_version"]}`; Python `{receipt["python_version"]}`.
- [Machine-readable receipt](span-tagging-real-data.json) includes source hashes,
  corpus identity, result provenance, and the individual phrase ranks.

## Reproduce

Acquire the pinned parquet directory and local FOLIO checkout separately. The
script reads the symbolic locations defined in its `main` function, never fetches
data, and retains its compiled lexicon in a newly created system temporary
directory. It prints that artifact path for the opt-in tests.

```bash
uv run --offline --no-sync python benchmarks/span_tagging_real_data.py
FOLIO_RESOLVE_OPENGLOSS_LEXICON="$LEXICON_ARTIFACT" \\
  uv run --offline --no-sync pytest -q tests/spans
```

Set `LEXICON_ARTIFACT` to the printed temporary artifact path; its manifest sits
beside it. Set `FOLIO_OPENGLOSS_SSSOM` to the acquired SSSOM file and
`FOLIO_OPENGLOSS_SSSOM_COMMIT` to its FOLIO commit before the benchmark to
record the crosswalk's identity, predicate counts, and corpus links. The file
path is never recorded in either receipt. Building requires the
`opengloss` extra; loading and tagging use the core installation.
"""
    RECEIPT.with_suffix(".md").write_text(text, encoding="utf-8")


def main() -> int:
    parquet_dir = Path("~/.local/share/lexplicit/artifacts/opengloss/v2.4-c64e480").expanduser()
    owl_path = Path("~/Coding Projects/folio/FOLIO.owl").expanduser()
    temporary = Path(tempfile.mkdtemp(prefix="folio-span-real-data-"))
    artifact = temporary / "lexicon.jsonl.gz"
    manifest = temporary / "lexicon.manifest.json"
    parent_before_build = rss_bytes(resource.RUSAGE_SELF)
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "folio_resolve.spans.lexicon",
            "build",
            *(str(parquet_dir / Path(name).name) for name in sorted(PINNED_SHA256)),
            "--artifact",
            str(artifact),
            "--manifest",
            str(manifest),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    build_seconds = time.perf_counter() - started
    builder_peak = rss_bytes(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    lexicon = load_lexicon(artifact, manifest)
    load_seconds = time.perf_counter() - started
    folio_release = sha256(owl_path)
    concepts = load_folio(owl_path)
    ontology = InMemoryOntology(concepts)
    crosswalk_path = os.environ.get("FOLIO_OPENGLOSS_SSSOM")
    crosswalk_commit = os.environ.get("FOLIO_OPENGLOSS_SSSOM_COMMIT")
    crosswalk = None
    crosswalk_digest = None
    rows_by_predicate: Counter[str] = Counter()
    if crosswalk_path:
        crosswalk_digest = sha256(Path(crosswalk_path).expanduser())
        crosswalk = load_crosswalk(Path(crosswalk_path).expanduser())
        rows_by_predicate.update(
            row.predicate_id for rows in crosswalk.by_subject.values() for row in rows
        )
    started = time.perf_counter()
    tagger = SpanTagger(ontology, lexicon, crosswalk, folio_release=folio_release)
    construction_seconds = time.perf_counter() - started
    first = tagger.tag(SAMPLE)
    second = tagger.tag(SAMPLE)
    spans = {span.text: span for span in first.spans}
    phrase = spans.get("good faith")
    good_faith = bool(phrase and phrase.opengloss) and all(
        anchor.rank == "primary" and anchor.outranked_by is None for anchor in phrase.opengloss
    )
    for word in ("good", "faith"):
        span = spans.get(word)
        good_faith = (
            good_faith
            and bool(span and span.opengloss)
            and all(
                anchor.rank == "fallback" and anchor.outranked_by == phrase.id
                for anchor in span.opengloss
            )
        )
    property_senses = [
        {"id": anchor.id, "domain": anchor.domain}
        for span in tagger.tag("property").spans
        for anchor in span.opengloss
    ]
    will_nouns = sorted(
        anchor.id
        for span in tagger.tag("will").spans
        for anchor in span.opengloss
        if anchor.pos == "noun"
    )
    will_passed = {"will:noun:0", "will:noun:1"}.issubset(will_nouns)
    corpus = [(concept.iri, concept.definition) for concept in concepts if concept.definition]
    if not corpus:
        raise ValueError("FOLIO corpus has no definitions")
    characters = sum(len(text) for _, text in corpus)
    span_count = link_count = 0
    links_by_predicate: Counter[str] = Counter(dict.fromkeys(rows_by_predicate, 0))
    started = time.perf_counter()
    for _, text in corpus:
        result = tagger.tag(text)
        span_count += len(result.spans)
        link_count += len(result.links)
        links_by_predicate.update(link.predicate_id for link in result.links)
    tagging_seconds = time.perf_counter() - started
    headword_self_match = check_headword_self_matches(tagger, lexicon)
    runtime_peak = rss_bytes(resource.RUSAGE_SELF)
    peak = max(runtime_peak, builder_peak + parent_before_build)
    failed = []
    if construction_seconds >= 60:
        failed.append("index construction under 60 s")
    if peak >= 3 * 2**30:
        failed.append("peak RSS under 3 GiB")
    identical = first.to_json() == second.to_json()
    receipt = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "folio_resolve_version": __version__,
        "python_version": platform.python_version(),
        "folio_release": folio_release,
        "lexicon": {
            **lexicon.manifest.model_dump(),
            "artifact_bytes": artifact.stat().st_size,
            "build_seconds": build_seconds,
            "load_seconds": load_seconds,
        },
        "construction_seconds": construction_seconds,
        "headword_self_match": headword_self_match,
        "peak_rss_bytes": peak,
        "rss": {
            "builder_peak_bytes": builder_peak,
            "runtime_peak_bytes": runtime_peak,
            "parent_before_build_bytes": parent_before_build,
        },
        "corpus": {
            "selection": "all nonempty concept definitions, sorted by IRI",
            "concept_count": len(concepts),
            "text_count": len(corpus),
            "sha256": hashlib.sha256(json.dumps(corpus, ensure_ascii=False).encode()).hexdigest(),
            "characters": characters,
            "tagging_seconds": tagging_seconds,
            "characters_per_second": characters / tagging_seconds,
            "span_count": span_count,
            "spans_per_text": span_count / len(corpus),
        },
        "good_faith": {
            "passed": good_faith,
            "text": SAMPLE,
            "spans": [
                span.model_dump(mode="json")
                for span in first.spans
                if span.text in {"good faith", "good", "faith"}
            ],
        },
        "property": {"present": bool(property_senses), "senses": property_senses},
        "will": {"passed": will_passed, "noun_sense_ids": will_nouns},
        "determinism": {
            "identical": identical,
            "serialized_sha256": hashlib.sha256(first.to_json().encode()).hexdigest(),
        },
        "crosswalk": "not provided"
        if crosswalk is None
        else {
            "row_count": sum(len(rows) for rows in crosswalk.by_subject.values()),
            "rows_by_predicate": dict(sorted(rows_by_predicate.items())),
            "corpus_links": link_count,
            "corpus_links_by_predicate": dict(sorted(links_by_predicate.items())),
            "source_commit": crosswalk_commit,
            "sha256": crosswalk_digest,
            "mapping_set_id": crosswalk.metadata["mapping_set_id"],
            "mapping_set_version": crosswalk.metadata["mapping_set_version"],
            "metadata": dict(crosswalk.metadata),
        },
        "provenance": first.provenance.model_dump(mode="json", exclude_none=True),
        "budget": {
            "verdict": "pass" if not failed else "fail",
            "failed_lines": failed,
            "construction_under_60_seconds": construction_seconds < 60,
            "peak_rss_under_3_gib": peak < 3 * 2**30,
        },
    }
    write_receipt(receipt)
    print(
        f"Receipts: {RECEIPT.relative_to(ROOT)} and {RECEIPT.with_suffix('.md').relative_to(ROOT)}"
    )
    print(f"Retained test artifact: {artifact}")
    print(
        f"Budget: {receipt['budget']['verdict']}; phrase ranking: {good_faith}; deterministic: {identical}"
    )
    return (
        0
        if not failed
        and good_faith
        and identical
        and property_senses
        and will_passed
        and not headword_self_match["unexpected_unmatched_count"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
