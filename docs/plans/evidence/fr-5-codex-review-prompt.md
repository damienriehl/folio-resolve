# fr-5 Codex review prompt

Review the diff from the retained fr-2 baseline for mandate item fr-5. Confirm
that the repair addresses only reproduced Core lint failures, preserves the
readiness CLI contract and bytecode-suppression ordering, adds the planned
isolated JSON/Markdown/help launcher regressions, and neither weakens tests nor
touches protected evaluation decisions or inputs. Treat unavailable frozen Core
checks as incomplete, not passing, and report any blocking source-diff finding.
