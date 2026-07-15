#!/bin/bash
CLAUDE_PID=$(cat /Users/admin/orchestrator/.spikes/probe/hook-test/claude.pid)
LOG=/Users/admin/orchestrator/.spikes/probe/hook-test/rss.log
MAX_WAIT=90
START=$(date +%s)

> "$LOG"
while true; do
  ELAPSED=$(($(date +%s) - START))
  if [ $ELAPSED -ge $MAX_WAIT ]; then
    break
  fi
  if ps -p $CLAUDE_PID > /dev/null 2>&1; then
    RSS=$(ps -o rss= -p $CLAUDE_PID | awk '{print $1}')
    TIMESTAMP=$(date +%s.%N)
    echo "$TIMESTAMP RSS=$RSS" >> "$LOG"
  else
    break
  fi
  sleep 3
done
