# Proposed public embedding fixtures

> **Historical proposal (committed 2026-09-30).** The review checkpoint it describes has passed: U4 shipped as PR #52 (`faa87a4`, public embedding recall baseline). The "U4 remains paused" wording below describes the state at drafting time.

Status: awaiting owner review before U4 implementation or measurement. This proposal implements the fixture-review checkpoint in [the reliability plan](../plans/2026-09-19-0853-feat-ci-embedding-reliability-plan.md). It contains no model results.

Approve or edit the queries and acceptable concept sets below before freezing the fixture JSON. The recommended default is to accept any listed IRI, with equal credit; an alternative label is another name for its existing IRI, not another answer. These are retrieval annotations, not legal advice or jurisdiction-specific statements of law.

## Proposed cases

Every identifier below is a suffix of `https://folio.openlegalstandard.org/`. Each label/IRI pair was verified directly in the cached public ontology XML described below. Queries are newly authored for this proposal.

| ID | Kind | Query | Acceptable labels and IRI suffixes | Mapping rationale for review |
|---|---|---|---|---|
| E1 | Exact | `Summary Judgment` | Summary Judgment — `R8K6VFq9c39kbQouame6Onf` | Exact public label; baseline lexical retrieval control. |
| E2 | Exact | `Auction` | Auction — `R8kOvHwkY6TrQmB7RnYiWNO` | Exact public label, also independently present in the repository UAT fixture. |
| A1 | Ambiguous granularity | `assignment of a case for handling` | Assignment of Case — `RBJNvhPmodYtKUv6xWzghh9`; Assignment — `R6j0hnYnovLTRdqZYalg6B` | Both definitions cover designation of a case; recommend accepting the specific concept and its broader litigation event. Do not accept contractual assignment for this query. |
| A2 | Ambiguous near synonyms | `contract clause transferring rights to another party` | Assignment Clause — `RBBtDifX6Pckw5d02IiBWFF`; Assignment of Contract Clause — `R7cFRBLdfhruFx7wsEihpG2` | Both definitions describe a contract provision transferring rights/obligations. Recommend equal credit; Assignment Agreement describes a different artifact. |
| P1 | Paraphrase | `careless conduct injures someone` | Negligence — `R7nqxxlAfhYqqSA2UQ5UxpX` | Definition connects insufficient care to harm. |
| P2 | Paraphrase | `lawyer advice stays confidential` | Attorney-Client Privilege — `R9BdovRK3PPCHYuYSJXadkJ` | Definition protects communications with counsel. Owner should confirm that this short query sufficiently distinguishes privilege from broader confidentiality duties. |
| P3 | Paraphrase | `expired deadline bars suing` | Statute of Limitations — `RDKJ7nEqeDxNDRxI2Wqil1N` | Definition specifies a time limit on bringing claims. |
| P4 | Paraphrase | `previously resolved dispute barred anew` | Res Judicata — `R8OsGCPOsihiJLtbFkIbRfx` | Definition bars relitigation following final adjudication. Public alternative label Claim Preclusion denotes the same IRI. |
| P5 | Paraphrase | `promised duties went unperformed` | Breach of Contract — `RC2fjcpsEbrVvVymvt4oASx` | Definition concerns failure to fulfill binding obligations. Owner should confirm whether to add contractual context despite losing some lexical separation. |
| P6 | Paraphrase | `bargained reciprocal benefit` | Consideration — `RBKXEdlGEMzeuinxEdRGBF5` | Definition concerns value exchanged for a promise/performance. |
| N1 | Unrelated negative control | `purple nebula hums a lullaby` | Empty set | Whimsical nonlegal sentence; record all returned candidates separately. |
| N2 | Unrelated negative control | `my sourdough starter smells fruity` | Empty set | Everyday nonlegal sentence; record all returned candidates separately. |

P1–P6 have **zero shared tokens** against their proposed target's combined label, preferred label, all alternative labels, and definition in the inspected XML, using Unicode `re.findall(r"\w+", text.casefold())`, without stop-word removal or stemming. This is a literal token distinction, not a claim that the phrases have no morphological relationship or overlap with other corpus concepts. Recheck against the frozen snapshot and the actual indexed text during implementation. Alias checking matters: an earlier draft using “secret” overlapped a French alternative label and was replaced before any model execution.

The negative controls mean “no intended legal retrieval target,” not “the ontology lacks every everyday subject.” Review full-corpus candidates for ontology coverage before treating returned concepts as errors. Keep negatives out of recall denominators; report their returned candidates/scores without inventing a rejection threshold after observing outcomes.

## Provenance and validation

The repository's in-memory unit fixtures largely use synthetic identifiers. Only the Auction mapping was corroborated from `tests/uat/test_uat_reconciliation_integrator.py`; the remaining mappings come directly from the public FOLIO OWL cache, parsed read-only with Python's XML standard library. No private gold, campaign artifacts, cached embeddings, or U10 data were read.

- Inspected cache: `~/.folio/cache/github/0bb58b4e09fe66f1c1e573f074732cbafa3609a63f5e46cc4f54b908a40dd92bfa2bb01dbff67127ba80d6265b80c972ea0248b4b63077e7162d151ae4e3ce9b.owl`.
- SHA-256: `44657b4ed844f5f9c9c48869184606b4fc671471a8263d79d241de87809fa239`.
- XML contains 18,327 direct `owl:Class` elements. This is provenance, not the future index's deduplicated concept count.
- Verified immutable acquisition source: [`alea-institute/FOLIO`, commit `e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2`, `FOLIO.owl`](https://raw.githubusercontent.com/alea-institute/FOLIO/e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2/FOLIO.owl). A fresh download on 2026-09-19 produced the exact SHA-256 above, matching the inspected cache bytes. All proposed mappings therefore also apply to this immutable source without changes.
- Resolution evidence: the cache filename equals BLAKE2b of `alea-institute/FOLIO/main`, matching folio-python's cache-key algorithm. Cache metadata reports source-last-modified `2026-05-27T02:05:30Z`; the public GitHub commits API identifies the linked commit at that timestamp for `FOLIO.owl`. The independently downloaded content digest, rather than the timestamp alone, establishes the match.

For every row, validation is: resolve its full IRI in the selected pinned public ontology, verify its label and definition, check aliases, and have the owner approve the complete acceptable set. Revalidate all mappings and token intersections if the selected revision differs from the inspected bytes. Review possible additional acceptable concepts before running models; do not edit expected answers to favor observed rankings. Freeze query text, acceptable sets, snapshot revision/digest, tokenizer convention, and fixture digest before comparison.

All three eventual variants must use the **same complete pinned public ontology corpus**, not just these target concepts. Record corpus assembly rules and deduplicated concept count alongside model/package provenance. The ten positive queries are a small diagnostic sample; they cannot establish overall legal-domain quality or justify changing production defaults by themselves.

## Review decision

Recommended starting set: all ten positives and two negatives above, accepting both answers for A1/A2. The substantive owner judgments are target specificity for P2/P5 and whether the two broader/multiple-answer sets fit the intended user queries. Ontology acquisition pinning is an implementation prerequisite, not an owner taste question. U4 remains paused at this review checkpoint; no benchmark code, model calls, rankings, or timing measurements were produced for this proposal.
