# Passage-generation assignment

Create one original passage for a fictional matter.

- Document kind: {doc_type}
- Jurisdiction: {jurisdiction}
- Scenario: {scenario}
- Target length: {length}
- Register: {register}

## Standard variant

Write realistic, internally coherent prose that could plausibly occur in the specified document
kind. Use facts, obligations, procedure, and ordinary professional phrasing appropriate to the
assignment. Do not produce a headings-only skeleton, checklist, metadata record, commentary, or
explanation. Do not ask for or mention any taxonomy, controlled vocabulary, identifiers, grading
labels, or hidden evaluation material. Invent every person, entity, docket, address, and matter;
never reuse real party names from actual cases.

Return only the passage. Save it as
`{doc_type}--{jurisdiction}--<assignment-id>.txt`, converting spaces and punctuation to lowercase
hyphens and preserving the dispatcher-provided assignment ID exactly.

## No-match variant

When the dispatcher marks the assignment `no-match`, instead write realistic prose about lawful,
routine, concept-free subject matter such as office scheduling, neutral document transmission, or
room logistics. Keep the same jurisdictional tone and length. Do not introduce disputes, remedies,
professional work categories, regulated conduct, named tribunals, transactions, industries, asset
classes, or geographic facts beyond the supplied jurisdiction. All standard rules above still
apply.
