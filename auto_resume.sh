#!/bin/bash
# Auto-resume script — runs at rate limit reset time via launchd
# Replays any tasks in resume_queue.jsonl

QUEUE_FILE="$HOME/.openclaw/mission_control/resume_queue.jsonl"
LOG="$HOME/.openclaw/mission_control/resume.log"
RESUME_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.rate-limit-resume.plist"

echo "[$(date)] Auto-resume triggered" >> "$LOG"

# Verify rate limit has actually reset with a quick test
TEST_RESULT=$(claude --permission-mode bypassPermissions --print "ok" 2>&1)
if echo "$TEST_RESULT" | grep -qi "rate limit\|credit\|exceeded\|529\|overloaded\|quota"; then
    echo "[$(date)] Rate limit still active, will retry in 30 min" >> "$LOG"
    # Reschedule 30 min later
    NEXT=$(date -v+30M +%H:%M)
    osascript -e "tell application \"Messages\" to send \"⏳ Rate limit still active at reset time. Will retry at $NEXT.\" to buddy \"+19842602526\"" 2>/dev/null
    # Reschedule launchd plist
    NEXT_H=$(date -v+30M +%H)
    NEXT_M=$(date -v+30M +%M)
    python3 -c "
import plistlib, os
path = os.path.expanduser('~/Library/LaunchAgents/ai.openclaw.rate-limit-resume.plist')
with open(path, 'rb') as f:
    p = plistlib.load(f)
p['StartCalendarInterval'] = [{'Hour': $NEXT_H, 'Minute': $NEXT_M}]
with open(path, 'wb') as f:
    plistlib.dump(p, f)
"
    launchctl unload "$RESUME_PLIST" 2>/dev/null || true
    launchctl load "$RESUME_PLIST" 2>/dev/null || true
    exit 0
fi

# Rate limit cleared — update Mission Control
curl -s -X POST http://localhost:4242/update \
    -H "Content-Type: application/json" \
    -d "{\"agent\": {\"status\": \"idle\", \"current_action\": \"Rate limit reset — ready\"}, \"activity_log\": [{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"level\": \"info\", \"message\": \"Rate limit reset — resuming queued tasks\"}]}" \
    2>/dev/null || true

# Send resume notification
QUEUE_COUNT=0
if [ -f "$QUEUE_FILE" ] && [ -s "$QUEUE_FILE" ]; then
    QUEUE_COUNT=$(wc -l < "$QUEUE_FILE" | tr -d ' ')
fi

osascript -e "tell application \"Messages\" to send \"✅ Claude usage reset. Resuming $QUEUE_COUNT queued task(s) now.\" to buddy \"+19842602526\"" 2>/dev/null

# Remove warned flag so monitoring resets
rm -f /tmp/.rate_limit_warned_90

# Unload the one-shot plist — job done
launchctl unload "$RESUME_PLIST" 2>/dev/null || true
rm -f "$RESUME_PLIST"

# Process resume queue
if [ -f "$QUEUE_FILE" ] && [ -s "$QUEUE_FILE" ]; then
    echo "[$(date)] Processing $QUEUE_COUNT queued tasks" >> "$LOG"
    
    while IFS= read -r line; do
        WORKDIR=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cwd','.'))" 2>/dev/null || echo ".")
        TASK_DESC=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('task_description','Resume interrupted task'))" 2>/dev/null || echo "Resume interrupted task")
        
        echo "[$(date)] Resuming task in $WORKDIR: $TASK_DESC" >> "$LOG"
        
        cd "$WORKDIR" 2>/dev/null || cd ~
        claude --permission-mode bypassPermissions --print "$TASK_DESC

When completely finished, run: openclaw system event --text \"Resumed task complete: $TASK_DESC\" --mode now" >> "$LOG" 2>&1
        
    done < "$QUEUE_FILE"
    
    # Clear the queue
    rm -f "$QUEUE_FILE"
    echo "[$(date)] All queued tasks processed" >> "$LOG"
else
    echo "[$(date)] No queued tasks to resume" >> "$LOG"
fi
