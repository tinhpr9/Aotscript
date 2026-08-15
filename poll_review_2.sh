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
  
  # Check if the latest review has "Reviewing files that changed from the base of the PR and between 6d6200fdaf8b4e64c497f20fdab139fecc756108 and 5070bee" or something similar indicating the new commit is reviewed.
  if echo "$COMMENTS" | grep -qi "5070bee"; then
    echo "CodeRabbit review found!"
    gh pr view 43 --comments
    exit 0
  fi
  
  sleep 30
done
