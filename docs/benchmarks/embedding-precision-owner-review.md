# Owner review of public embedding candidates

Owner: Damien Riehl. Recorded 2026-09-20 in the active Codex conversation.

The owner classified the numbered blinded 55-pair pool after collection. Numbers below bind to the exact query/IRI pairs in the table, not ranking positions.

## Decisions

> So for example 2 is "direct match", 3 (child), 4 (parent), 5 (relationship [edge]). So I'll write 2, 3 c, 4 p, 5 r.

> 8, 9 r, 10 r, 11 r, 12, 17, 24 r, 25 r, 26 r, 27, 28, 30 r, 33 r, 34 r, 35 c, 36 c, 37, 38 c, 39 c, 40 c, 41, 42 c, 43, 50, 53 r.

The follow-up asked whether to report strict precision (direct matches only) and expanded precision (direct + children + parents + relationships), preserving the categories separately; and whether the 26 unlisted candidates were irrelevant or unjudged. The owner answered:

> 1: yes, your recommendation. 2: Make those unjudged. Too ambiguous

This authorizes both scoring interpretations and leaves every unlisted pair unjudged. Ambiguity is the reason for missing labels, not an automatically assigned irrelevant label. These are owner classifications; no graph-edge verification is claimed. The classification of candidate 17 as a direct match is retained even though the original P3 intended target remains Statute of Limitations. Intended-target hits and owner relevance measure different things.

## Exact pair mapping

| Number | Query | Concept | Owner classification | IRI |
| --- | --- | --- | --- | --- |
| 1 | E1: Summary Judgment | Abstract of Judgment | unjudged | https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s |
| 2 | E1: Summary Judgment | Summary Judgment | direct_match | https://folio.openlegalstandard.org/R8K6VFq9c39kbQouame6Onf |
| 3 | E1: Summary Judgment | Summary Judgment in Lieu of Complaint | child | https://folio.openlegalstandard.org/R9RRyCC6w6V3oGeHdAUkdAo |
| 4 | E1: Summary Judgment | Judgment | parent | https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm |
| 5 | E1: Summary Judgment | Motion for Summary Judgment | relationship | https://folio.openlegalstandard.org/RGcqiLEe0IK8lPRt5mFC0D |
| 6 | E1: Summary Judgment | JJ10 Applications relating to originating process or Statement of Case or for default or summary Judgment (UK J-CODE) | unjudged | https://folio.openlegalstandard.org/RODsAVADydNHhEWk3l57k1 |
| 7 | E2: Auction | Antitrust - Bid-Rigging Claims | unjudged | https://folio.openlegalstandard.org/R73DJPQnmrqO90OL7OBDZJU |
| 8 | E2: Auction | Auction | direct_match | https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO |
| 9 | E2: Auction | Auction Rate Dividend Payment | relationship | https://folio.openlegalstandard.org/RCxcU4AQo2mykjqSdB8EGpW |
| 10 | E2: Auction | Auctioneer | relationship | https://folio.openlegalstandard.org/RDLIpK2R8t5eS7LDT636F0h |
| 11 | E2: Auction | Bid | relationship | https://folio.openlegalstandard.org/RiQHdRDZfaAIOlPy9vfwB8 |
| 12 | P1: careless conduct injures someone | Negligence | direct_match | https://folio.openlegalstandard.org/R7nqxxlAfhYqqSA2UQ5UxpX |
| 13 | P1: careless conduct injures someone | Intentional Infliction of Emotional Distress | unjudged | https://folio.openlegalstandard.org/R84N0KXivn5IJaQn8xVwPDg |
| 14 | P1: careless conduct injures someone | Willful Misconduct | unjudged | https://folio.openlegalstandard.org/RCuhDmyUHjn92exJ8dx1zO1 |
| 15 | P1: careless conduct injures someone | Negligent Infliction of Emotional Distress on a Bystander | unjudged | https://folio.openlegalstandard.org/RDuyHZDRBvSF1vlAJM9Jpix |
| 16 | P1: careless conduct injures someone | Reckless Involuntary Manslaughter | unjudged | https://folio.openlegalstandard.org/RDwISw0syjjwmS6UpyiShve |
| 17 | P3: expired deadline bars suing | Motion to File Claim After Claims Bar Date | direct_match | https://folio.openlegalstandard.org/R9v8rZaf1tIB7O0km8nPh9h |
| 18 | P3: expired deadline bars suing | Bar Date Motion | unjudged | https://folio.openlegalstandard.org/RBaJDWY4eliIvdRb35ib3Bw |
| 19 | P4: previously resolved dispute barred anew | Alternative Dispute Resolution Clause | unjudged | https://folio.openlegalstandard.org/R7JZv6z3TDA2deV497R7DTW |
| 20 | P4: previously resolved dispute barred anew | Alternative Dispute Resolution Practice | unjudged | https://folio.openlegalstandard.org/R85H4hDZWq92xwwBFP7WCtM |
| 21 | P4: previously resolved dispute barred anew | Ratification | unjudged | https://folio.openlegalstandard.org/RBDRtzbiNquelJuhuvpkCWl |
| 22 | P4: previously resolved dispute barred anew | Dispute Resolution Clause | unjudged | https://folio.openlegalstandard.org/RCUXIdFpIxrgm2u6Xlk9ywc |
| 23 | P4: previously resolved dispute barred anew | Dispute Events | unjudged | https://folio.openlegalstandard.org/RDFhtJuTWcY7UZGV9F5yOoA |
| 24 | S1: Bid | Antitrust - Bid-Rigging Claims | relationship | https://folio.openlegalstandard.org/R73DJPQnmrqO90OL7OBDZJU |
| 25 | S1: Bid | Auction | relationship | https://folio.openlegalstandard.org/R8kOvHwkY6TrQmB7RnYiWNO |
| 26 | S1: Bid | Response to Request for Proposal | relationship | https://folio.openlegalstandard.org/RCn4N8uYzSo6xnYwLKQUFrk |
| 27 | S1: Bid | Bid | direct_match | https://folio.openlegalstandard.org/RiQHdRDZfaAIOlPy9vfwB8 |
| 28 | S2: Seller | Seller | direct_match | https://folio.openlegalstandard.org/R7kkf8NvitG8hubZUnTwevG |
| 29 | S2: Seller | Real Property Defect Concealed by Seller | unjudged | https://folio.openlegalstandard.org/R8t3vR9aRVG8h1UgWT0EuYc |
| 30 | S2: Seller | Seller's Jurisdiction | relationship | https://folio.openlegalstandard.org/R9TfodsJviJIL6CSEeHNgSx |
| 31 | S2: Seller | Sale of Goods Breached by Seller | unjudged | https://folio.openlegalstandard.org/RBmELIUkctGFVB61kQPJA1Q |
| 32 | S2: Seller | Insolvent Seller Having Sold Goods | unjudged | https://folio.openlegalstandard.org/RC0ObOWmvtDlrgacZgpfFsp |
| 33 | S3: Judgment | Abstract of Judgment | relationship | https://folio.openlegalstandard.org/R2iMgtqApgam5KhZ1XpJ0s |
| 34 | S3: Judgment | Judgment Lien | relationship | https://folio.openlegalstandard.org/R7mLi0c5rJV5GYGGuEi5dmD |
| 35 | S3: Judgment | Judgment of Arbitration | child | https://folio.openlegalstandard.org/R81opfsWnlEQvW2jFPWFOxo |
| 36 | S3: Judgment | Administrative Judgment | child | https://folio.openlegalstandard.org/R82iOqJMuYOjOGxNmuj1pDp |
| 37 | S3: Judgment | Judgment | direct_match | https://folio.openlegalstandard.org/R9jpIjzc10qdgl78jXOZtfm |
| 38 | S4: Negligence | Utility Negligence | child | https://folio.openlegalstandard.org/R79BT6PokjPAorkSylcA3Nk |
| 39 | S4: Negligence | Railroad Negligence | child | https://folio.openlegalstandard.org/R7KZwhAGHfOZiJ3JU4whCT4 |
| 40 | S4: Negligence | Hospice Negligence | child | https://folio.openlegalstandard.org/R7fGGaO3fruO5xNWT14tbky |
| 41 | S4: Negligence | Negligence | direct_match | https://folio.openlegalstandard.org/R7nqxxlAfhYqqSA2UQ5UxpX |
| 42 | S4: Negligence | Orthopedic Negligence | child | https://folio.openlegalstandard.org/R7rjJ9T1UTHGGYYAERaSUCd |
| 43 | G1: Denmark | Denmark | direct_match | https://folio.openlegalstandard.org/R1ABd0796Ff01FF7573A211f |
| 44 | G1: Denmark | Netherlands | unjudged | https://folio.openlegalstandard.org/RB931a58378ccE7Cd487Cb99 |
| 45 | G1: Denmark | Sweden | unjudged | https://folio.openlegalstandard.org/RBB0bdf1675535B219491726 |
| 46 | G1: Denmark | Iceland | unjudged | https://folio.openlegalstandard.org/RDFA941C24067F77d72D2ae5 |
| 47 | G1: Denmark | Norway | unjudged | https://folio.openlegalstandard.org/RFFF8fa35Deb8899da3CD365 |
| 48 | G2: Riga | Surigao del Norte | unjudged | https://folio.openlegalstandard.org/R2242976CD932F64a462Ce1e |
| 49 | G2: Riga | Latvia | unjudged | https://folio.openlegalstandard.org/R22C1a9E9De3f8DDc1fDFf70 |
| 50 | G2: Riga | Riga | direct_match | https://folio.openlegalstandard.org/R2311533686ac96916eE33cd |
| 51 | G2: Riga | Surigao del Sur | unjudged | https://folio.openlegalstandard.org/R716e8d1F03e4CE4a6d8Efbb |
| 52 | G2: Riga | Lithuania | unjudged | https://folio.openlegalstandard.org/R8385d9F26f0cDB5ce7777d8 |
| 53 | G2: Riga | Riga Stock Exchange | relationship | https://folio.openlegalstandard.org/R8jzivkKfIDQYH3smEkpVDa |
| 54 | G2: Riga | Estonia | unjudged | https://folio.openlegalstandard.org/R9CFcf2FE0e5934F5860E230 |
| 55 | G2: Riga | Water Supply and Irrigation Systems | unjudged | https://folio.openlegalstandard.org/gruc1t94SUKQT6HTOqLpEw |

## Binding

Collection file SHA-256: `a3f257034657a26657141f97c9a6e883a98e692556bf608d14309f7e0d726bb5`.

The judgment sheet retains the collection, pool, and rubric digests. Its approval receipt binds the complete categorical judgment payload; the report records the approval digest required by the offline scoring command. The original collection and original collector are unchanged.
