#!/bin/bash
gh pr comment 43 --body "@coderabbitai full review"
echo "Requested review."

START_TIME=$(date +%s)
MAX_WAIT=600

while true; do
  NOW=$(date +%s)
  if [ $((NOW - START_TIME)) -gt $MAX_WAIT ]; then
    echo "Timeout waiting for CodeRabbit review."
    exit 1
  fi
  
  COMMENTS=$(gh pr view 43 --json comments -q '.comments | map(select(.author.login == "coderabbitai[bot]")) | last | .body')
  
  if echo "$COMMENTS" | grep -qi "3ecb5ba"; then
    echo "CodeRabbit review found!"
    gh pr view 43 --comments
    exit 0
  fi
  
  if echo "$COMMENTS" | grep -qi "Review rate limited"; then
    echo "Rate limited again. Waiting 1 minute..."
    sleep 60
    continue
  fi
  
  sleep 30
done
