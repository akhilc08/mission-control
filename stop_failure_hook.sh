#!/bin/bash
# StopFailure hook — fires when Claude Code ends due to API/rate limit error
# Called by Claude Code hooks system with error info in environment

QUEUE_FILE="$HOME/.openclaw/mission_control/resume_queue.jsonl"
mkdir -p "$(dirname "$QUEUE_FILE")"

# Read hook event from stdin
INPUT=$(cat)

# Check if it's a rate limit / credit error
IS_RATE_LIMIT=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
reason = str(data.get('stop_reason', '') or data.get('error', '') or '').lower()
keywords = ['rate limit', 'credit', 'exceeded', '529', 'overloaded', 'usage limit', 'quota']
print('yes' if any(k in reason for k in keywords) else 'no')
" 2>/dev/null || echo "no")

if [ "$IS_RATE_LIMIT" = "yes" ]; then
    # Get session info
    SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id','unknown'))" 2>/dev/null || echo "unknown")
    TRANSCRIPT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
    
    # Log to resume queue
    ENTRY=$(python3 -c "
import json, datetime
entry = {
    'paused_at': datetime.datetime.utcnow().isoformat() + 'Z',
    'session_id': '$SESSION_ID',
    'transcript': '$TRANSCRIPT',
    'cwd': '$(pwd)',
    'status': 'paused_rate_limit'
}
print(json.dumps(entry))
")
    echo "$ENTRY" >> "$QUEUE_FILE"
    
    # Update Mission Control state
    curl -s -X POST http://localhost:4242/update \
        -H "Content-Type: application/json" \
        -d "{\"activity_log\": [{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"level\": \"warn\", \"message\": \"Task paused: rate limit hit (session $SESSION_ID)\"}]}" \
        2>/dev/null || true
    
    # Send iMessage
    osascript -e "tell application \"Messages\" to send \"🛑 Claude Code hit rate limit and paused. Session: $SESSION_ID. Tell me when to resume.\" to buddy \"+19842602526\"" 2>/dev/null
fi
