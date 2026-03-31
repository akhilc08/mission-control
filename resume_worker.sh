#!/bin/bash
# resume_worker.sh — Process the resume queue for paused tasks
# Reads resume_queue.jsonl, retries each paused task via Claude Code

set -uo pipefail

MC_DIR="$HOME/.openclaw/mission_control"
RESUME_QUEUE="$MC_DIR/resume_queue.jsonl"
MC_URL="http://localhost:4242"
MAX_ATTEMPTS=3

log() { echo "[resume_worker] $(date -u +%FT%TZ) $*"; }

if [ ! -f "$RESUME_QUEUE" ] || [ ! -s "$RESUME_QUEUE" ]; then
  log "No paused tasks to resume"
  exit 0
fi

TOTAL=$(wc -l < "$RESUME_QUEUE" | tr -d ' ')
log "Processing $TOTAL paused task(s)"

# Read all entries into a temp file, then clear the queue
WORK_FILE=$(mktemp)
cp "$RESUME_QUEUE" "$WORK_FILE"
> "$RESUME_QUEUE"

# Process each entry
while IFS= read -r line; do
  [ -z "$line" ] && continue

  # Parse entry
  TASK_ID=$(python3 -c "import json; print(json.loads('$line'.replace(\"'\", \"\"))['id'])" 2>/dev/null) || continue
  WORKDIR=$(python3 -c "import json,sys; e=json.load(sys.stdin); print(e['workdir'])" <<< "$line") || continue
  PROMPT_FILE=$(python3 -c "import json,sys; e=json.load(sys.stdin); print(e['prompt_file'])" <<< "$line") || continue
  ATTEMPTS=$(python3 -c "import json,sys; e=json.load(sys.stdin); print(e.get('attempts',0))" <<< "$line") || continue

  log "Retrying task $TASK_ID (attempt $((ATTEMPTS + 1))/$MAX_ATTEMPTS)"

  # Check prompt file still exists
  if [ ! -f "$PROMPT_FILE" ]; then
    log "Prompt file missing for $TASK_ID, marking failed"
    curl -s -X POST "$MC_URL/update" \
      -H "Content-Type: application/json" \
      -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"failed\",\"completed_at\":\"$(date -u +%FT%TZ)\",\"output\":\"Prompt file missing\"}}}" >/dev/null 2>&1 || true
    continue
  fi

  # Mark as in_progress
  curl -s -X POST "$MC_URL/update" \
    -H "Content-Type: application/json" \
    -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"in_progress\"}}}" >/dev/null 2>&1 || true

  # Run Claude Code
  OUTPUT_FILE=$(mktemp)
  EXIT_CODE=0
  cd "$WORKDIR" 2>/dev/null || { log "Workdir missing for $TASK_ID"; continue; }
  claude --permission-mode bypassPermissions --print < "$PROMPT_FILE" > "$OUTPUT_FILE" 2>&1 || EXIT_CODE=$?

  OUTPUT=$(cat "$OUTPUT_FILE")
  rm -f "$OUTPUT_FILE"

  if [ $EXIT_CODE -eq 0 ]; then
    # Success
    log "Task $TASK_ID completed on retry"
    curl -s -X POST "$MC_URL/update" \
      -H "Content-Type: application/json" \
      -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"completed\",\"completed_at\":\"$(date -u +%FT%TZ)\",\"output\":\"Completed on retry\"}}}" >/dev/null 2>&1 || true
    continue
  fi

  # Check if rate limited again
  RATE_LIMITED=false
  for pattern in "rate limit" "rate_limit" "credit" "exceeded" "529" "overloaded" "ResourceExhausted" "too many requests" "Too Many Requests"; do
    if echo "$OUTPUT" | grep -qi "$pattern" 2>/dev/null; then
      RATE_LIMITED=true
      break
    fi
  done

  NEW_ATTEMPTS=$((ATTEMPTS + 1))

  if [ "$RATE_LIMITED" = true ]; then
    if [ "$NEW_ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
      log "Task $TASK_ID exceeded max attempts ($MAX_ATTEMPTS), marking failed"
      curl -s -X POST "$MC_URL/update" \
        -H "Content-Type: application/json" \
        -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"failed\",\"completed_at\":\"$(date -u +%FT%TZ)\",\"output\":\"Rate limit: max retries exceeded\"}}}" >/dev/null 2>&1 || true
    else
      log "Task $TASK_ID still rate limited, re-queuing (attempt $NEW_ATTEMPTS)"
      python3 -c "
import json
entry = {
    'id': '$TASK_ID',
    'workdir': '$WORKDIR',
    'prompt_file': '$PROMPT_FILE',
    'paused_at': '$(date -u +%FT%TZ)',
    'attempts': $NEW_ATTEMPTS
}
with open('$RESUME_QUEUE', 'a') as f:
    f.write(json.dumps(entry) + '\n')
"
      curl -s -X POST "$MC_URL/update" \
        -H "Content-Type: application/json" \
        -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"paused\"}}}" >/dev/null 2>&1 || true
    fi
  else
    # Non-rate-limit failure
    log "Task $TASK_ID failed on retry (exit $EXIT_CODE)"
    curl -s -X POST "$MC_URL/update" \
      -H "Content-Type: application/json" \
      -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"failed\",\"completed_at\":\"$(date -u +%FT%TZ)\",\"output\":\"Error on retry (exit $EXIT_CODE)\"}}}" >/dev/null 2>&1 || true
  fi

done < "$WORK_FILE"

rm -f "$WORK_FILE"

# Update paused_tasks count
if [ -f "$RESUME_QUEUE" ] && [ -s "$RESUME_QUEUE" ]; then
  PAUSED_COUNT=$(wc -l < "$RESUME_QUEUE" | tr -d ' ')
else
  PAUSED_COUNT=0
fi
curl -s -X POST "$MC_URL/update" \
  -H "Content-Type: application/json" \
  -d "{\"agent\":{\"paused_tasks\":$PAUSED_COUNT}}" >/dev/null 2>&1 || true

log "Resume worker finished. $PAUSED_COUNT task(s) still paused."
