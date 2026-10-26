# fr-2 build receipt

Item: `fr-2` (expansion). Branch: `drawdown/fr-2`.
Plan: `docs/plans/2026-10-26-1051-feat-fr-2-campaign-readiness-plan.md`.

## Delivered

The offline readiness command summarizes explicitly supplied operator attestations
for 11 U10 checks or 17 retrieval checks. It retains missing, adverse, stale and
conflicting binding, responsible-role and receipt states. Deterministic JSON and
Markdown share one normalized summary and fixed follow-up descriptions.

Strict schema validation rejects unknown content and duplicate keys/checks. Every
summary discloses that underlying verification was not performed and execution
and adoption authorization were not granted. The command reads the supplied
snapshot without discovering artifacts, calling producers or writing campaign
state. Usage, finite vocabularies, complete inventories and fabricated examples
are documented in `eval/README.md`.

The starting tree was clean at `abf14ce`, with the plan already committed. Source
checks confirmed the four current retrieval arms. The chief campaign plan is not
available in this clone; its cited sections were not independently read. The
supplied controller dispatch and standing rules governed this build. Chief-source
reconciliation remains with the controller, as recorded in the plan.

## Verification

| Check | Result |
|---|---|
| New readiness suite | 74 passed |
| Existing generated split-integrity suite | 17 passed; unchanged |
| Combined selected suite, Python 3.11.14 | 91 passed |
| Combined selected suite, Python 3.12.12 | 91 passed |
| Isolated CLI, both formats on Python 3.11 and 3.12 | Passed |
| README partial and favorable examples; inventory agreement | Passed |
| Real broken stdout/stderr pipes and closed streams | Exit 1 preserved |
| Isolated copied launcher and package | No bytecode, new files or input changes |
| Instrumented CLI I/O | Only supplied snapshot read; forbidden discovery/process/network/lock operations not attempted |
| Changed-file whitespace check | `git diff --check` passed |

The selected command was `python -m pytest tests/test_eval_campaign_readiness.py
tests/test_eval_splits.py -q`, using each cached interpreter, bytecode suppression,
the repository source path and the locally available pytest package path. Final
runs used the normal repository `conftest.py`; no tests or fixtures were disabled
or modified to obtain those results. Both runs emitted one warning about the
unavailable pytest-asyncio plugin's `asyncio_mode` setting. No asynchronous tests
are in these selected suites.

Tests preceded behavior: the initial reducer suite failed because the module was
absent; 23 added CLI/renderer tests then failed before their implementation.
The output-error regressions reproduced closed-stream exceptions and broken-stderr
exit 120 before the fix. All existing tests were retained. No protected evaluation
inputs were opened to develop or run these checks.

## Review and local commits

ce-code-review receipt: Codex review `fr-2-codex-review-verdict.json`, verdict `merge`.
Prompt: `docs/plans/evidence/fr-2-codex-review-prompt.md`.
Verdict: `docs/plans/evidence/fr-2-codex-review-verdict.json`.

| Commit | Result |
|---|---|
| `abf14ce` | Existing committed fr-2 plan |
| `cfb913e` | Fixed inventories, reducer and validation tests |
| `a541285` | JSON/Markdown CLI, controlled diagnostics and I/O tests |
| `57f50fa` | Schema guidance and executable fabricated examples |
| `9f14cd6` | Reviewed output-failure repair and launcher regressions |

Simplification preserved behavior and all proof tests. No additional solutions
note is needed: the plan, implementation and regressions retain the relevant
constraints. Browser verification found no affected routes; no frontend change
or screenshot applies.

## Controller prerequisites

Offline `uv sync --frozen --extra dev` could not obtain the pinned development
packages from the available cache. Ruff and mypy were unavailable; neither check
is claimed as passing. Run the configured lint and library type checks, and also
type-check `eval/folio_eval/campaign_readiness.py` and
`eval/check_campaign_readiness.py` explicitly.

Broader core CI and the protected-input publication scan remain required at the
controller's gate. The campaign-report suite was not collected because its import
reads evaluation inputs. Fixture tests and finite output vocabularies do not
substitute for a real manifest-backed publication scan.

No remote is configured. This build makes local commits only; the controller owns
push, PR creation and campaign merge checks. No provider run, spend, credential
access, schema migration, data rewrite, release, new service or owner decision
was required. U10 R17 and retrieval R11 decisions remain untouched.

Rollback: revert the fr-2 implementation and documentation commits. The command
persists no campaign state and requires no migration or checkpoint repair.
