---
name: systematic-debugging
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
metadata:
  origin: Superpowers
---

# Systematic Debugging

## Overview

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

**Violating the letter of this process is violating the spirit of debugging.**

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## Relationship with Bug-Hunter

- **`systematic-debugging`** is the active investigative workflow: reproduce → trace → hypothesize → verify → minimal fix.
- **`bug-hunter`** is the independent adversarial reviewer/blocker: audits findings, proves implementation wrong, and gates merge.
- Do not conflate the two: `systematic-debugging` investigates the problem; `bug-hunter` verifies the solution independently.

## When to Use

Use for ANY technical issue:
- Test failures
- Bugs in production or fleet relays
- Unexpected behavior or flaky locks
- Performance problems
- Build/type failures
- Integration issues across components (e.g., worker ↔ hub ↔ device)

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

**Don't skip when:**
- Issue seems simple (simple bugs have root causes too)
- You're in a hurry (rushing guarantees rework)
- Maintaining fail-closed safety invariants

## The Four Phases

You MUST complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages & Stack Traces Carefully**
   - Don't skip past errors or warnings.
   - They often contain the exact failure location.
   - Note line numbers, file paths, exception types, and error codes.

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps or environment variables?
   - Does it happen every time?
   - If not reproducible → gather more data with instrumentation, don't guess.

3. **Check Recent Changes**
   - What changed that could cause this?
   - Git diff, recent commits, base SHA.
   - New dependencies, configuration changes, environment differences.

4. **Gather Evidence in Multi-Component Systems**
   **WHEN system has multiple components (e.g., Device Relay ↔ Hub DO ↔ Supervisor ↔ Setup Driver):**
   - Add diagnostic instrumentation at component boundaries:
     - Log what data enters component.
     - Log what data exits component.
     - Verify environment/config propagation.
     - Check state at each layer.
   - Run once to gather evidence showing WHERE it breaks.
   - Analyze evidence to identify the failing component, then investigate that specific component.

5. **Trace Data Flow & Exception Provenance**
   - Where does the bad value or unhandled exception originate?
   - What called this function with the bad value?
   - Keep tracing up the call stack until you find the source.
   - Fix at the root cause, never at the superficial symptom.

### Phase 2: Pattern Analysis

**Find the pattern before fixing:**

1. **Find Working Examples**
   - Locate similar working code in the same codebase.
   - What works that is similar to what is broken?

2. **Compare Against Reference Specifications**
   - If implementing a protocol or pattern, read the reference specification completely (e.g., AGENTS.md, protocol spec).
   - Understand the contract fully before modifying code.

3. **Identify Differences**
   - What is different between working and broken components?
   - List every difference, however small. Do not assume "that can't matter."

4. **Understand Dependencies & Safety Invariants**
   - What assumptions does the code make about file systems, locks, symlinks, or network?
   - Will the fix weaken fail-closed behavior or leak credentials?

### Phase 3: Hypothesis and Testing

**Apply the scientific method:**

1. **Form a Single Clear Hypothesis**
   - State clearly: *"I think X is the root cause because Y."*
   - Be specific, not vague.

2. **Test Minimally**
   - Make the SMALLEST possible change to test the hypothesis.
   - One variable at a time.
   - Don't fix multiple things at once.

3. **Verify Before Continuing**
   - Did the test confirm the hypothesis?
     - Yes → Proceed to Phase 4.
     - No → Form a NEW hypothesis. Do NOT stack more fixes on top.

### Phase 4: Implementation & Verification

**Fix the root cause, not the symptom:**

1. **Create Failing Regression Test Case (RED)**
   - Simplest possible reproduction as an automated unit/integration test.
   - MUST observe the test fail with the expected error before implementing the fix.

2. **Implement Single Minimal Fix (GREEN)**
   - Address the root cause identified.
   - ONE change at a time.
   - No bundled refactoring or "while I'm here" modifications.

3. **Verify Fix & Check Regressions**
   - Test passes now (GREEN)?
   - All existing tests still pass?
   - Issue actually resolved?
   - Use `verification-loop` with fresh evidence before claiming success.

4. **3-Fix Rule: Question Architecture**
   - If 3 attempted fixes fail to resolve the defect: **STOP**.
   - Do NOT attempt Fix #4 blindly.
   - Step back and re-evaluate the architecture, state machine, or assumptions.
