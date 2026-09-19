# Isolate campaign-report tests from local firm gold

The campaign-report tests create a synthetic surface manifest but discover local
gold from the developer checkout. Reproduce the resulting 22 freshness failures,
then isolate that discovery to temporary fixture gold matching the synthetic
manifest. Keep the real loader, freshness comparison, and collision scanner active.

Scope: `tests/test_eval_campaign_report.py` and this work's verification record.
No production changes, private-data changes, U10 triage, or campaign regeneration.

1. Record the existing failing campaign-report suite before modifying fixtures.
2. Supply matching temporary gold through a test fixture and redirect only the
   local-gold discovery path.
3. Verify newer gold and same-version content drift still fail through the real
   campaign-report scanner.
4. Run campaign-report and leakcheck tests, the full core suite, and changed-file
   lint. Retain a recorded Codex review, then ship the focused test fix.

Done when the campaign-report tests run independently of checkout-local gold,
freshness rejection remains covered, and the full core suite has no failures.
