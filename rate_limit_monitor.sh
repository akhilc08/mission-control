#!/bin/bash
# Rate limit statusline monitor — called by Claude Code with rate_limits JSON
# At 90%: pause all running tasks, iMessage alert, schedule auto-resume via launchd

QUEUE_FILE="$HOME/.openclaw/mission_control/resume_queue.jsonl"
WARNED_FILE="/tmp/.rate_limit_warned_90"
RESUME_PLIST="$HOME/Library/LaunchAgents/ai.openclaw.rate-limit-resume.plist"

# Read JSON from stdin
INPUT=$(cat)

# Extract 5-hour window stats
read USED RESETS_AT <<< $(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
rl = data.get('rate_limits', {})
five_hr = rl.get('five_hour', {})
used = int(float(five_hr.get('used_percentage', 0)))
resets = five_hr.get('resets_at', '')
print(used, resets)
" 2>/dev/null || echo "0 ")

# Already warned recently?
if [ -f "$WARNED_FILE" ]; then
    WARNED_AT=$(cat "$WARNED_FILE")
    NOW=$(date +%s)
    if [ $((NOW - WARNED_AT)) -lt 1800 ]; then
        exit 0
    fi
fi

if [ "$USED" -ge 90 ]; then
    # Format reset time
    RESET_NICE=$(python3 -c "
from datetime import datetime, timezone
try:
    dt = datetime.fromisoformat('$RESETS_AT'.replace('Z','+00:00'))
    print(dt.astimezone().strftime('%I:%M %p'))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

    # Get reset epoch for scheduling
    RESET_EPOCH=$(python3 -c "
from datetime import datetime
try:
    dt = datetime.fromisoformat('$RESETS_AT'.replace('Z','+00:00'))
    print(int(dt.timestamp()))
except:
    import time; print(int(time.time()) + 3600)
" 2>/dev/null || echo "")

    # Send iMessage
    osascript -e "tell application \"Messages\" to send \"🛑 Hit 90% Claude usage. Pausing all tasks. Will resume at $RESET_NICE.\" to buddy \"+19842602526\"" 2>/dev/null

    # Log to Mission Control
    curl -s -X POST http://localhost:4242/update \
        -H "Content-Type: application/json" \
        -d "{\"agent\": {\"status\": \"paused\", \"current_action\": \"Rate limit at 90% — paused until $RESET_NICE\"}, \"activity_log\": [{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"level\": \"warn\", \"message\": \"Rate limit 90% — all tasks paused until $RESET_NICE\"}]}" \
        2>/dev/null || true

    # Schedule auto-resume via launchd at reset time
    if [ -n "$RESET_EPOCH" ]; then
        # Write a one-shot launchd plist that fires at reset time
        python3 << PYEOF
import plistlib, os

plist = {
    'Label': 'ai.openclaw.rate-limit-resume',
    'ProgramArguments': [
        '/bin/bash',
        os.path.expanduser('~/.openclaw/mission_control/auto_resume.sh')
    ],
    'StartCalendarInterval': [],
    'RunAtLoad': False,
    'StandardOutPath': os.path.expanduser('~/.openclaw/mission_control/resume.log'),
    'StandardErrorPath': os.path.expanduser('~/.openclaw/mission_control/resume.log'),
}

# Convert epoch to calendar interval
import datetime, time
dt = datetime.datetime.fromtimestamp($RESET_EPOCH + 120)  # +2 min buffer
plist['StartCalendarInterval'] = [{
    'Hour': dt.hour,
    'Minute': dt.minute,
    'Weekday': dt.weekday() + 1  # launchd: 0=Sun, but just set for today
}]

path = os.path.expanduser('~/Library/LaunchAgents/ai.openclaw.rate-limit-resume.plist')
with open(path, 'wb') as f:
    plistlib.dump(plist, f)
print(f"Resume plist written for {dt.strftime('%I:%M %p')}")
PYEOF

        # Load the plist
        launchctl unload "$RESUME_PLIST" 2>/dev/null || true
        launchctl load "$RESUME_PLIST" 2>/dev/null || true
    fi

    # Record warning time
    date +%s > "$WARNED_FILE"
fi
