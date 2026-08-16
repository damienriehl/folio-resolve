#!/usr/bin/env bash
# Publish the Gate 1b gold-evaluation workspace to the Cockpit board (stable URL:
# https://dashboard.damienriehl.com/folio-resolve-gold-evaluation.html).
#
# Owner-run (firm data; Anthropic-surface sessions only). Run from a checkout whose
# eval/data holds the LIVE gold + reports. Re-run after every fold so the page always
# renders the current gold version. The cockpit 15-minute sync serves the copy.
#
# Usage: eval/publish-gold-evaluation.sh [GOLD_JSONL] [SPLIT_MANIFEST] [BOARD_DIR]
set -euo pipefail
cd "$(dirname "$0")/.."
GOLD="${1:-$(ls eval/data/gold/gold_v*.jsonl | sort -V | tail -1)}"
SPLIT="${2:-$(ls eval/data/gold/split_manifest_v*.json | sort -V | tail -1)}"
BOARD="${3:-$HOME/Coding Projects/cockpit/briefs/board}"
uv run python eval/run_audit.py --mode packet-v2 --gold "$GOLD" --split-manifest "$SPLIT" \
  --clusters eval/data/reports/clusters_v2.jsonl --lane firm
SHEET="eval/data/reports/audit_packet_v2/sheet.html"
[ -s "$SHEET" ] || { echo "sheet missing: $SHEET" >&2; exit 1; }
cp "$SHEET" "$BOARD/folio-resolve-gold-evaluation.html"
echo "published: $BOARD/folio-resolve-gold-evaluation.html (gold=$GOLD split=$SPLIT)"
