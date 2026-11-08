# OpenGloss + FOLIO span-tagging real-data check

## Protocol

Recorded 2026-11-08T19:36:42.543344+00:00. Built the complete pinned parquet dataset with
the package's build CLI in a separate process, into a temporary directory outside
the repository; loaded it with `load_lexicon`. Read the local FOLIO.owl offline
with stdlib ElementTree into `InMemoryOntology` (folio-python is not installed;
its constructor supports GitHub/HTTP sources). Named classes are merged by IRI;
English then untagged labels are preferred, with lexical ties. All labels and
definitions are retained; no branch ancestry or OWL imports are inferred.

Tagged **all 15,403 nonempty concept definitions**, sorted by
IRI, from 18,327 concepts. Timings include result assembly,
but exclude serialization. Results are counted and discarded per text.
Peak RSS uses `resource.getrusage`: the larger of runtime peak and builder peak
plus the parent's pre-build peak (a conservative allowance for their overlap).
The enclosing worker memory scope remains in force. Timings describe one run,
without emptying OS caches, and are not service-level guarantees.

## Results

| Measurement | Result |
|---|---:|
| Lexicon build | 6.347 s |
| Compressed artifact | 16,321,447 bytes |
| Lexicon rows | 300,787 |
| Verified lexicon load | 3.772 s |
| SpanTagger construction | 4.742 s |
| Peak RSS | 737.5 MiB |
| Corpus characters | 3,015,408 |
| Tagging time | 17.550 s |
| Throughput | 171,818.1 characters/s |
| Mean spans per text | 19.686 |
| Distinct OpenGloss headwords checked against own surface | 160,724 |
| Headwords matching their own exact anchor | 159,934 |
| Headwords without their own anchor | 790 |
| Unexpected self-match failures | 0 |

Budget verdict: **pass**. Construction must be strictly below
60 s (True); peak RSS strictly below
3 GiB (True). Failed lines: none.

## Headword self-match

Tagged every distinct OpenGloss headword's own text and checked for an exact
OpenGloss anchor carrying that same headword. This includes punctuation-bearing
headwords. The check took 10.926 s, separately from corpus throughput.
Of 790 unmatched headwords,
790 are intentionally excluded because every
token is a committed function word, a ruler stopword, or has only function-word
parts of speech; 0 have no word tokens.
Unexpected failures: **0**.
The JSON receipt records counts and up to twenty examples for each reason.

## Phrase ranking and property observation

On “The tenant acted in good faith.”, OpenGloss “good faith” ranks primary and both “good” and “faith”
rank fallback, naming the phrase: **True**.
Two serialized outputs for that text are byte-identical:
**True**.

OpenGloss senses tagged on “property”:

| Sense id | Domain |
|---|---|
| `property:noun:0` | science.materials_science |
| `property:noun:1` | science.chemistry |
| `property:noun:2` | science.chemistry |

These are the dataset's senses, with no disambiguation. The spike reported a
missing legal sense; domain labels alone cannot establish semantic coverage.
This check asserts presence and records the gap rather than repairing the data.

Crosswalk: not provided; no real crosswalk links were evaluated.

## Provenance

- Dataset: `mjbommar/opengloss-v2.4-senses` at `c64e480015b8c84f9d08469d4dfd64a7a8fb89bd`.
- Uncompressed lexicon content SHA-256: `bd5dffc696d2157dd8a305caabf8da6941a7b9a2f564e080bdfff7f8aeecef02`.
- FOLIO.owl SHA-256, passed as `folio_release`: `c0f8ca58dad4227e89856d97a0344f45bec37289832837bd11c473dfdc7b44be`.
- folio-resolve `0.5.0`; Python `3.13.11`.
- [Machine-readable receipt](span-tagging-real-data.json) includes source hashes,
  corpus identity, result provenance, and the individual phrase ranks.

## Reproduce

Acquire the pinned parquet directory and local FOLIO checkout separately. The
script reads the symbolic locations defined in its `main` function, never fetches
data, and retains its compiled lexicon in a newly created system temporary
directory. It prints that artifact path for the opt-in tests.

```bash
uv run --offline --no-sync python benchmarks/span_tagging_real_data.py
FOLIO_RESOLVE_OPENGLOSS_LEXICON="$LEXICON_ARTIFACT" \
  uv run --offline --no-sync pytest -q tests/spans
```

Set `LEXICON_ARTIFACT` to the printed temporary artifact path; its manifest sits
beside it. Optionally set `FOLIO_OPENGLOSS_SSSOM` to a readable SSSOM file before
the benchmark to measure real crosswalk rows and links. Building requires the
`opengloss` extra; loading and tagging use the core installation.
