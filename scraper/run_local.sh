#!/usr/bin/env bash
#
# run_local.sh — local full serial harvest runner for theavalanches-forum-archive.
#
# Runs the FULL serial Wayback harvest on this Mac (residential IP) by invoking the
# existing scraper/harvest.py (--num-shards 1 --shard 0). Resumable: harvest.py skips
# already-saved files, so this is safe to Ctrl-C and re-run.
#
# While running it provides observability for an external monitor:
#   - logs/harvest_local.log      append-only, timestamped tee of harvest.py stdout
#   - logs/harvest_status.json    machine-readable heartbeat (updated every 30s)
# and periodically commits+pushes progress to origin so pages survive remotely.
#
# Usage:  scraper/run_local.sh [delay_seconds]      (delay defaults to 2.0)
#
set -u

# --- paths (repo root = parent of this script's dir) ------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE" || exit 1
LOGDIR="$HERE/logs"
LOG="$LOGDIR/harvest_local.log"
STATUS="$LOGDIR/harvest_status.json"
TOTAL=8134
DELAY="${1:-2.0}"
INTERVAL=30          # heartbeat cadence (seconds)
COMMIT_EVERY=10      # commit every COMMIT_EVERY*INTERVAL = ~5 min
STARTED_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
FINALIZED=""

mkdir -p "$LOGDIR"
[ -f "$LOGDIR/.gitkeep" ] || : > "$LOGDIR/.gitkeep"

count_files() { find archive -name '*.html' -type f 2>/dev/null | wc -l | tr -d ' '; }

# --- start harvest.py in the background, timestamped tee -> tailable log -----
{
  printf '%s ==== run_local.sh start (delay=%s) ====\n' "$STARTED_ISO" "$DELAY"
} >> "$LOG"

python3 scraper/harvest.py --num-shards 1 --shard 0 --delay "$DELAY" \
  > >(while IFS= read -r line; do
        printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line" | tee -a "$LOG"
      done) 2>&1 &
HARVEST_PID=$!

# --- heartbeat -------------------------------------------------------------
write_heartbeat() {  # state running files
  local state="$1" running="$2" files="$3" now last_line
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  last_line="$(tail -n1 "$LOG" 2>/dev/null | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\000-\037')"
  cat > "$STATUS.tmp" <<EOF
{
  "pid": $HARVEST_PID,
  "running": $running,
  "started_at": "$STARTED_ISO",
  "last_update": "$now",
  "files_on_disk": $files,
  "targets_total": $TOTAL,
  "last_log_line": "$last_line",
  "state": "$state"
}
EOF
  mv "$STATUS.tmp" "$STATUS"
}

# --- commit + push (bot identity, never force) -----------------------------
commit_push() {
  git add archive logs 2>/dev/null
  if git diff --cached --quiet 2>/dev/null; then return; fi   # nothing changed
  git -c user.name="harvest-bot" \
      -c user.email="harvest-bot@users.noreply.github.com" \
      commit -q -m "harvest progress: $(count_files)/$TOTAL files @ $(date -u +%Y-%m-%dT%H:%M:%SZ)" || return
  local n=0
  while [ "$n" -lt 3 ]; do
    if git pull --rebase --autostash -q && git push -q; then return; fi
    n=$((n + 1)); sleep 5
  done
  echo "warn: push failed after retries (will retry next cycle)"
}

# --- finalize (run once) ---------------------------------------------------
finalize() {  # state
  [ -n "$FINALIZED" ] && return; FINALIZED=1
  local fstate="$1" cur elapsed
  if kill -0 "$HARVEST_PID" 2>/dev/null; then
    kill "$HARVEST_PID" 2>/dev/null; wait "$HARVEST_PID" 2>/dev/null
  fi
  cur="$(count_files)"
  write_heartbeat "$fstate" false "$cur"
  commit_push
  elapsed=$(( $(date +%s) - START_EPOCH ))
  printf 'harvest %s: got %s/%s files, elapsed %02d:%02d:%02d\n' \
    "$fstate" "$cur" "$TOTAL" \
    $((elapsed / 3600)) $(((elapsed % 3600) / 60)) $((elapsed % 60))
}

on_exit() { echo; echo "interrupted, finalizing..."; finalize exited; exit 130; }
trap on_exit INT TERM

# --- monitor loop (foreground until harvest.py exits) ----------------------
echo "run_local.sh: harvest pid $HARVEST_PID, delay ${DELAY}s, log $LOG"
last="$(count_files)"; last_change="$START_EPOCH"; progressed=0; i=0
while kill -0 "$HARVEST_PID" 2>/dev/null; do
  cur="$(count_files)"; now="$(date +%s)"
  if [ "$cur" -gt "$last" ]; then last="$cur"; last_change="$now"; progressed=1; fi
  if [ $((now - last_change)) -gt 300 ]; then state="stalled"
  elif [ "$progressed" -eq 1 ]; then state="progressing"
  else state="starting"; fi
  write_heartbeat "$state" true "$cur"
  i=$((i + 1))
  [ $((i % COMMIT_EVERY)) -eq 0 ] && commit_push
  sleep "$INTERVAL"
done

# harvest.py exited on its own: reap it and report done/exited
wait "$HARVEST_PID" 2>/dev/null; HCODE=$?
if [ "$HCODE" -eq 0 ]; then finalize done; else finalize exited; fi
