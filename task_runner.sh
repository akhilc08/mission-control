#!/bin/bash
# task_runner.sh — Run Claude Code tasks with rate-limit pause/resume
# Usage: task_runner.sh <workdir> <task_id> <prompt_file>

set -euo pipefail

WORKDIR="${1:?Usage: task_runner.sh <workdir> <task_id> <prompt_file>}"
TASK_ID="${2:?Missing task_id}"
PROMPT_FILE="${3:?Missing prompt_file}"
MC_DIR="$HOME/.openclaw/mission_control"
RESUME_QUEUE="$MC_DIR/resume_queue.jsonl"
MC_URL="http://localhost:4242"

log() { echo "[task_runner] $(date -u +%FT%TZ) $*"; }

# Ensure resume queue file exists
touch "$RESUME_QUEUE"

# Mark task as in_progress
curl -s -X POST "$MC_URL/update" \
  -H "Content-Type: application/json" \
  -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"in_progress\",\"started_at\":\"$(date -u +%FT%TZ)\"}}}" >/dev/null 2>&1 || true

log "Running task $TASK_ID in $WORKDIR"

# Run Claude Code, capture output and exit code
OUTPUT_FILE=$(mktemp)
EXIT_CODE=0
cd "$WORKDIR"
claude --permission-mode bypassPermissions --print < "$PROMPT_FILE" > "$OUTPUT_FILE" 2>&1 || EXIT_CODE=$?

OUTPUT=$(cat "$OUTPUT_FILE")
rm -f "$OUTPUT_FILE"

if [ $EXIT_CODE -eq 0 ]; then
  # Success — mark task complete
  log "Task $TASK_ID completed successfully"
  COMPLETED_AT=$(date -u +%FT%TZ)
  curl -s -X POST "$MC_URL/update" \
    -H "Content-Type: application/json" \
    -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"completed\",\"completed_at\":\"$COMPLETED_AT\",\"output\":\"Done\"}}}" >/dev/null 2>&1 || true
  exit 0
fi

# Check if it's a rate limit error
RATE_LIMITED=false
for pattern in "rate limit" "rate_limit" "credit" "exceeded" "529" "overloaded" "ResourceExhausted" "too many requests" "Too Many Requests"; do
  if echo "$OUTPUT" | grep -qi "$pattern" 2>/dev/null; then
    RATE_LIMITED=true
    break
  fi
done

if [ "$RATE_LIMITED" = true ]; then
  log "Task $TASK_ID paused: rate limit hit, will resume automatically"

  # Check if task is already in the queue and get current attempts
  EXISTING=$(python3 -c "
import json, sys
queue_file = '$RESUME_QUEUE'
try:
    with open(queue_file) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            entry = json.loads(line)
            if entry.get('id') == '$TASK_ID':
                print(entry.get('attempts', 0))
                sys.exit(0)
except: pass
print(-1)
")

  if [ "$EXISTING" = "-1" ]; then
    ATTEMPTS=1
  else
    ATTEMPTS=$((EXISTING + 1))
    # Remove old entry
    python3 -c "
import json
queue_file = '$RESUME_QUEUE'
lines = []
with open(queue_file) as f:
    for line in f:
        line = line.strip()
        if not line: continue
        entry = json.loads(line)
        if entry.get('id') != '$TASK_ID':
            lines.append(line)
with open(queue_file, 'w') as f:
    for line in lines:
        f.write(line + '\n')
"
  fi

  # Add to resume queue
  PAUSED_AT=$(date -u +%FT%TZ)
  python3 -c "
import json
entry = {
    'id': '$TASK_ID',
    'workdir': '$WORKDIR',
    'prompt_file': '$PROMPT_FILE',
    'paused_at': '$PAUSED_AT',
    'attempts': $ATTEMPTS
}
with open('$RESUME_QUEUE', 'a') as f:
    f.write(json.dumps(entry) + '\n')
"

  # Update state: mark task as paused and update paused_tasks count
  PAUSED_COUNT=$(wc -l < "$RESUME_QUEUE" | tr -d ' ')
  curl -s -X POST "$MC_URL/update" \
    -H "Content-Type: application/json" \
    -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"paused\"}},\"agent\":{\"paused_tasks\":$PAUSED_COUNT}}" >/dev/null 2>&1 || true

  exit 2  # Special exit code for "paused"
else
  # Other error — mark task as failed
  log "Task $TASK_ID failed (exit $EXIT_CODE)"
  TRUNCATED_OUTPUT=$(echo "$OUTPUT" | head -c 500)
  curl -s -X POST "$MC_URL/update" \
    -H "Content-Type: application/json" \
    -d "{\"tasks\":{\"$TASK_ID\":{\"status\":\"failed\",\"completed_at\":\"$(date -u +%FT%TZ)\",\"output\":\"Error (exit $EXIT_CODE)\"}}}" >/dev/null 2>&1 || true
  exit 1
fi
