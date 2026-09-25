#!/usr/bin/env bash
# Delete Parasight-AMA job data older than N days (default 30).
# The app never deletes uploads, temp files or outputs on its own.
# Intended to run daily from the parasight user's crontab, see DEPLOY.md.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/srv/parasight/data}"
KEEP_DAYS="${1:-30}"

# Temporaries (unzipped inputs, spooled uploads) are useless after a job: 2 days.
for d in "$DATA_DIR/temp" "$DATA_DIR/tmp"; do
    [ -d "$d" ] && find "$d" -mindepth 1 -maxdepth 1 -mtime +2 -exec rm -rf {} +
done

# Per-job upload/output folders (named by job UUID).
for d in "$DATA_DIR/uploads" "$DATA_DIR/outputs"; do
    [ -d "$d" ] && find "$d" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +
done
