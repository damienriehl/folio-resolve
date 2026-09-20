# Public precision fixtures for owner review

These are proposed cases, not approved benchmark truth. They supplement the existing eight cases without editing their queries or answer sets. No model has been run against the new queries to select or revise them.

## Proposed initial additions

For positives, the named IRI is an intended target, not an exhaustive list of all relevant concepts. For negatives, “none” means no intended FOLIO target; it does not label every possible result irrelevant. Candidate relevance is reviewed separately after collection.

| ID | Group | Query | Proposed intended target | Reason for inclusion |
| --- | --- | --- | --- | --- |
| S1 | Short exact label | Bid | [Bid](https://folio.openlegalstandard.org/RiQHdRDZfaAIOlPy9vfwB8) | Short label admitted by the selective experiment; exact recognition must survive. |
| S2 | Short exact label | Seller | [Seller](https://folio.openlegalstandard.org/R7kkf8NvitG8hubZUnTwevG) | Separates direct mention from its admission for Auction. |
| S3 | Short exact label | Judgment | [Judgment](https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm) | Separates direct mention from the broader concept admitted for Summary Judgment. |
| S4 | Short exact label | Negligence | [Negligence](https://folio.openlegalstandard.org/R7nqxxlAfhYqqSA2UQ5UxpX) | Exact-label counterpart to the existing P1 paraphrase. |
| G1 | Geographic exact label | Denmark | [Denmark](https://folio.openlegalstandard.org/R1ABd0796Ff01FF7573A211f) | Geographic concepts can be correct for direct queries despite being distractions elsewhere. |
| G2 | Geographic exact label | Riga | [Riga](https://folio.openlegalstandard.org/R2311533686ac96916eE33cd) | Second observed geographic admission tested in an appropriate context. |
| N3 | Negative candidate | velvet comet hums softly | None intended | Newly authored nonsensical phrase; no deliberate legal or geographic referent. |
| N4 | Negative candidate | polka-dot moon tastes fizzy | None intended | A second nonsensical phrase, distinct from the existing negative controls. |

All six positive labels and IRIs were checked against the existing pinned corpus (revision `e36f3f8e7e7ecc8e081b78ababc3daca149fcaf2`, OWL SHA-256 `44657b4ed844f5f9c9c48869184606b4fc671471a8263d79d241de87809fa239`). The geographic records have no definitions in that corpus; no definitions or branch enrichment are invented. The existing synthetic guard's label Tax was not found as an exact corpus label and is excluded. Assignment ambiguity and short privilege/breach paraphrases remain excluded.

These cases deliberately follow observed failure modes. They are a diagnostic challenge set, not an independent holdout or representative sample. The two new nonsense queries remain weak negative controls. They cannot establish real-world precision on their own.

## Candidate relevance rubric for review

Assess each query/IRI pair for whether the concept directly expresses the query's requested meaning. Use the pinned label, definition, aliases, and parents as evidence. Do not infer a legal application merely because a query could occur in a legal document. A shared word or embedding similarity alone is insufficient.

Use three judgments: relevant, irrelevant, or uncertain. Missing judgments also remain unjudged. Intended targets are proposed relevant examples; the table does not automatically make other concepts irrelevant. Broader/narrower concepts and related roles may require a judgment specific to the query.

In particular, decide Judgment for Summary Judgment, Bid for Auction, and Ratification for the existing P4 query from the actual definitions and query meaning. Seller for Auction is rank six in the prior local result, outside the initial top-five pool; its relevance remains unjudged unless separately reviewed. Those relationships are not resolved by this proposal. Geography is not automatically irrelevant: Denmark for Denmark is an intended positive, while a geographic admission to a nonsense query needs its own judgment.

After case approval, pool the union of the original and selective local-model top-five results for the existing eight and accepted new cases. Present each unique query/IRI once, with ontology evidence and without arm names, scores, ranks, or extraction paths in the judgment view. Retain those fields separately for audit. Newly collected pairs start unjudged; do not prefill an assistant's relevance guesses as owner decisions.

## Approval requested

Approve S1–S4, G1–G2, and N3–N4 as the initial additions, and the rubric above, or identify cases to omit/change. Approval permits collection and intended-target scoring; it does not approve unseen candidate relevance judgments. A second, concrete review sheet will contain the pooled candidates before precision results are finalized.
