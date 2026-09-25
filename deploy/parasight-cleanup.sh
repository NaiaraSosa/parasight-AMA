#!/usr/bin/env bash
# Delete Parasight-AMA jobs older than N days (default 30), plus leftovers.
# Intended to run daily from the parasight user's crontab, see DEPLOY.md.
#
#   DATA_DIR/uploads/<job>, DATA_DIR/outputs/<job>   jobs: deleted after N days
#   DATA_DIR/processing/<job>                       deleted by the app when a job ends;
#                                                   leftovers (service stopped mid-job)
#                                                   after 2 days
#   TMP_DIR (the service's TMPDIR)                  scratch files: after 2 days
#
# README.txt files are kept.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/srv/parasight/data}"
TMP_DIR="${TMP_DIR:-$(dirname "$DATA_DIR")/tmp}"
KEEP_DAYS="${1:-30}"

# Short-lived files: 2 days. data/temp and data/tmp are the pre-rename names.
for d in "$DATA_DIR/processing" "$TMP_DIR" "$DATA_DIR/temp" "$DATA_DIR/tmp"; do
    if [ -d "$d" ]; then find "$d" -mindepth 1 -maxdepth 1 ! -name README.txt -mtime +2 -exec rm -rf {} +; fi
done

# Per-job upload/output folders (named by job UUID).
for d in "$DATA_DIR/uploads" "$DATA_DIR/outputs"; do
    if [ -d "$d" ]; then find "$d" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +; fi
done
