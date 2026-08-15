#!/bin/bash
START_TIME=$(date +%s)
MAX_WAIT=600

while true; do
  NOW=$(date +%s)
  if [ $((NOW - START_TIME)) -gt $MAX_WAIT ]; then
    echo "Timeout waiting for CodeRabbit review."
    exit 1
  fi
  
  COMMENTS=$(gh pr view 43 --json comments -q '.comments | map(select(.author.login == "coderabbitai[bot]")) | last | .body')
  
  if echo "$COMMENTS" | grep -qi "review"; then
    echo "CodeRabbit review found!"
    gh pr view 43 --comments
    exit 0
  fi
  
  sleep 60
done
