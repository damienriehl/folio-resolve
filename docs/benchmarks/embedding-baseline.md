# Public embedding retrieval baseline

## Approved scope

On 2026-09-19, the owner selected **only unambiguous cases initially** from the public fixture proposal. This baseline freezes E1, E2, P1, P3, P4, and P6 as six positive queries, plus N1 and N2 as negative controls. Assignment cases A1/A2 and the short privilege/breach paraphrases P2/P5 are excluded before measurement.

The expected answer sets are retrieval annotations. Six positives are a small diagnostic sample, not an estimate of general legal-domain retrieval quality. Negative controls have no intended target and are excluded from recall denominators; returned ontology concepts are reported without assuming every everyday subject is outside FOLIO.

## Protocol

Compare embeddings disabled, deterministic hashing, and the existing pinned local MiniLM model against the same complete public ontology snapshot. Use the library's real `MatchPipeline`, default score floor of 45, and `BruteForceIndex`; do not tune thresholds or expected answers after observing rankings. Each variant runs in a separate process. Measure construction separately from warmed query latency; report process peak resident memory through query execution, before provenance hashing, not just index storage.

The benchmark uses `InMemoryOntology` for all variants so corpus membership is identical. Its full lexical scan differs from the optional `FolioPythonProvider` search window. Latency therefore describes this benchmark configuration, not every production adapter. The index embeds label plus definition; aliases remain available to lexical retrieval.

Construction starts in a fresh process but uses files already on disk; “cold” does not mean an emptied OS page cache or include model/ontology downloads. Each query has one untimed warm-up call immediately before its three timed repetitions. Three repetitions across eight queries yield 24 timed calls per variant, so the reported p95 is descriptive and sensitive to this small workload. Runs are sequential on one host, with CPU thread limits recorded; timings are not service-level promises.

The normalized corpus retains named FOLIO classes by IRI, including concepts with colliding labels, and excludes OWL built-ins. Branch ancestry is not inferred by this lightweight XML adapter. Optional entity-ruler, multi-strategy recall, and LLM judge components are not configured.

## Results — 2026-09-19

Recall means the fraction of positive queries with **any acceptable target** among the first k final pipeline candidates. Negative controls do not enter the denominator. These are final results after the existing gates and score floor, not isolated model-neighbor recall.

| Variant | Recall@1 | Recall@5 | Build (s) | Warm p50 (s) | Warm p95 (s) | Peak RSS (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| disabled | 2/6 (33.3%) | 2/6 (33.3%) | 0.587 | 1.129 | 1.379 | 147.7 |
| hashing | 1/6 (16.7%) | 2/6 (33.3%) | 2.385 | 1.422 | 1.649 | 335.5 |
| local | 2/6 (33.3%) | 2/6 (33.3%) | 203.450 | 1.559 | 1.783 | 993.1 |

| Query / approved target | Disabled rank | Hashing rank | Local rank |
|---|---:|---:|---:|
| E1: Summary Judgment | 1 | 1 | 1 |
| E2: Auction | 1 | 3 | 1 |
| P1: Negligence | absent | absent | absent |
| P3: Statute of Limitations | absent | absent | absent |
| P4: Res Judicata | absent | absent | absent |
| P6: Consideration | absent | absent | absent |

All variants returned zero final candidates for both negative controls. This small observation does not establish a general false-positive rate.

Hashing placed Faranah and Marche (semantic scores 100.0) ahead of the exact Auction target (lexical score 99.0). The local model returned plausible but unapproved alternatives: Willful Misconduct for P1, Bar Date Motion for P3, and Alternative Dispute Resolution Practice for P4. P6 returned no final candidates. The approved answers and thresholds were not changed to accommodate these rankings.

The pipeline applies `ShortLabelGate` to semantic candidates too: single-content-word labels below its near-exact threshold are demoted to 40, below the default floor of 45. This benchmark does not record rejected neighbors, so it cannot attribute each miss to retrieval versus gating. In particular, absence here does not prove the model never retrieved the target.

Retain current production behavior. This sample supplies no end-to-end recall gain for the local model, and hashing reduces top-one accuracy. A narrowly scoped follow-up should capture pre-gate versus post-gate target ranks and profile lexical search versus cosine scoring on these same frozen cases before proposing threshold, model, FAISS, or cache changes. Any expansion of answer sets remains a separate review decision.

Raw ranked survivors, scores, extraction paths, gate fields, per-query timing samples, and provenance:

- [Embeddings disabled](embedding-baseline-disabled.json)
- [Hashing](embedding-baseline-hashing.json)
- [Pinned local model](embedding-baseline-local.json)

## Provenance

- Candidate corpus: **18,325** unique named FOLIO classes from 18,327 XML class entries; two OWL built-ins excluded. One unlabeled FOLIO class uses its IRI as fallback label. Labels that collide retain separate IRIs.
- Normalization: English, then untagged, then other-language text, with lexical tie-breaking; all alternative labels and named parents retained; definitions joined deterministically; no inferred branch ancestry.
- Ontology revision: `e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2`; raw SHA-256 `44657b4ed844f5f9c9c48869184606b4fc671471a8263d79d241de87809fa239`.
- Normalized corpus SHA-256: `a829784b946f3c7e87d55c4734d895a1ece589f78fb8618a2c05008c67c29c73`.
- [Frozen fixtures](../../benchmarks/fixtures/embedding_recall.json), canonical JSON SHA-256: `70603b759e56ad252129fc5c4de95c6418f436e06598e773e0ab79c71a759c72`. All three runs have identical fixture, corpus, benchmark-source, and library-source hashes. All four paraphrases pass the frozen literal token-disjointness check.
- Local model: `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, 384 dimensions; hashing uses 256 dimensions.
- Host: Intel Core 7 240H, 16 logical CPUs, Linux 7.0.0-31, Python 3.12.12. CPU Torch `2.13.0+cpu`, sentence-transformers `3.4.1`, transformers `4.57.6`; `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`.


## Reproduce

Run from a Git checkout on Linux. The runner reads only the public OWL file and pinned local model; it never loads private gold or campaign artifacts. Acquisition is separate from the offline measurement. The OWL digest is checked before any variant builds. For another machine, retain its new JSON outputs rather than replacing this recorded baseline without explanation.

```bash
uv venv --python 3.12 /tmp/folio-embedding-venv
uv pip install --python /tmp/folio-embedding-venv/bin/python \
  torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python /tmp/folio-embedding-venv/bin/python \
  -e '.[embedding]' sentence-transformers==3.4.1 transformers==4.57.6 \
  huggingface-hub==0.36.2 faiss-cpu==1.14.3
curl --fail --location \
  https://raw.githubusercontent.com/alea-institute/FOLIO/e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2/FOLIO.owl \
  --output /tmp/folio-public-benchmark.owl
HF_HUB_DISABLE_XET=1 /tmp/folio-embedding-venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="sentence-transformers/all-MiniLM-L6-v2",
    revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    cache_dir="/tmp/folio-model-cache",
    allow_patterns=["*.json", "*.txt", "*.safetensors"],
)
PY
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
for variant in disabled hashing local; do
  /tmp/folio-embedding-venv/bin/python -m benchmarks.embedding_recall \
    --owl /tmp/folio-public-benchmark.owl \
    --variant "$variant" --repeats 3 \
    --model-path /tmp/folio-model-cache/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
    --output "/tmp/embedding-baseline-$variant.json"
done
```

The recorded JSON files include the complete installed package inventory, model-file hashes, library source/data digest, benchmark source digest, canonical fixture digest, and normalized corpus digest. The Git revision identifies the base checkout; the benchmark itself was new uncommitted code during the first run, so its source hash is the exact implementation identifier. Optional packages listed in the shared environment do not imply they were imported by every variant. No FAISS backend is used.
