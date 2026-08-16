---
description: Adversarial dual-review convergence loop — two independent model reviewers must both approve before code ships.
---

# Santa Loop

Adversarial dual-review convergence loop using the santa-method skill. Two independent reviewers with isolated contexts must both return NICE before code is declared ready.

## Purpose

Run two independent reviewers (Subagent A + Subagent B in isolated contexts) against the current task output. Both must return NICE before hand-off. If either returns NAUGHTY, fix all flagged issues and re-run fresh reviewers — up to 3 rounds.

## Usage

```
/santa-loop [file-or-glob | description]
```

## Security & External Transmission Policy

- **Local / Self-Contained Review Only**: All review rounds run using local isolated subagents (such as `code-reviewer`, `security-reviewer`, `python-reviewer`, `typescript-reviewer`, or `silent-failure-hunter`).
- **No Automatic External Transmission**: The workflow does NOT automatically detect or execute external CLIs (such as Codex or Gemini) and never sends source code or repository contents to third-party endpoints unless the user explicitly requests external review with `--external`.
- **No Automatic Git Push/Merge**: Conforms strictly to `AGENTS.md`. No automated git commits, pushes, or PR merges.

## Workflow

### Step 1: Identify What to Review

Determine the scope from `$ARGUMENTS` or fall back to uncommitted changes:

```bash
git diff --name-only HEAD
```

Read all changed files to build the full review context.

### Step 2: Build the Rubric

Construct a rubric appropriate to the file types under review. Every criterion must have an objective PASS/FAIL condition:

| Criterion | Pass Condition |
|-----------|---------------|
| Correctness | Logic is sound, no bugs, handles edge cases |
| Security | No secrets, injection, XSS, or OWASP Top 10 issues |
| Error handling | Errors handled explicitly, no silent swallowing |
| Completeness | All requirements addressed, no missing cases |
| Internal consistency | No contradictions between files or sections |
| No regressions | Changes don't break existing behavior |

### Step 3: Dual Independent Review

Launch two reviewers in parallel in isolated contexts:
- **Reviewer A**: Independent subagent (e.g. `code-reviewer` or `python-reviewer`/`typescript-reviewer`)
- **Reviewer B**: Independent subagent (e.g. `silent-failure-hunter` or `security-reviewer`)

Each reviewer evaluates every rubric criterion as PASS or FAIL, then returns structured JSON:

```json
{
  "verdict": "PASS" | "FAIL",
  "checks": [
    {"criterion": "...", "result": "PASS|FAIL", "detail": "..."}
  ],
  "critical_issues": ["..."],
  "suggestions": ["..."]
}
```

### Step 4: Verdict Gate

- **Both PASS** → **NICE** — proceed to Step 6 (Hand-off)
- **Either FAIL** → **NAUGHTY** — merge all critical issues from both reviewers, deduplicate, proceed to Step 5

### Step 5: Fix Cycle (NAUGHTY path)

1. Display all critical issues from both reviewers.
2. Fix every flagged issue — change only what was flagged.
3. Re-run Step 3 with **fresh reviewers** (no memory of previous rounds).
4. Repeat until both return PASS (Maximum 3 iterations).

If still NAUGHTY after 3 rounds, stop and escalate to the maintainer.

### Step 6: Verification Completion & Hand-off (NICE path)

When both reviewers return PASS:
- Mark output as verified.
- Do NOT perform git push or merge. Present findings and verification summary to maintainer.

### Step 7: Final Report

Print the output report:

```text
SANTA VERDICT: [NICE / NAUGHTY (escalated)]

Reviewer A: [PASS/FAIL]
Reviewer B: [PASS/FAIL]

Agreement:
  Both flagged:      [issues caught by both]
  Reviewer A only:   [issues only A caught]
  Reviewer B only:   [issues only B caught]

Iterations: [N]/3
Result:     [VERIFIED / ESCALATED TO MAINTAINER]
```
