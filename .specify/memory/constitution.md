# Aotscript Project Constitution

## Core Principles

### I. Supreme Policy Precedence (AGENTS.md)
`AGENTS.md` holds supreme, non-negotiable authority over all worker release rules, supervisor architectures, and fleet safety invariants. No constitution amendment, plan, or generated task may contradict or weaken rules in `AGENTS.md`.

### II. Zero Hardcoding of Machine Identity & Secrets
Do not add machine identity, device IDs, groups, sessions, credentials, or secrets to releases, manifests, or code. All device state must remain in external protected storage. Fleet protocol identity is strictly dynamic Device ID only.

### III. Root-Cause First (No Symptom Fixes)
The Iron Law of Debugging applies to all defects: `NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST`.
1. Investigate error provenance, call stack, and component boundaries.
2. Form clear testable hypothesis.
3. Test minimally and verify root cause before touching production code.

### IV. Test-Driven Development (Red-Green-Refactor)
`NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST`.
1. **RED:** Write minimal test and observe it fail for the exact expected reason.
2. **GREEN:** Write minimal code to pass (strictly no YAGNI or speculative refactoring).
3. **REFACTOR:** Clean up only after all tests are green.
4. Maintain 80%+ coverage with unit, integration, and regression suites.

### V. Fresh Evidence Gate (No Assumptions)
`NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE ON CURRENT HEAD`.
Never claim a task is fixed, clean, or passing without executing the verification command on the exact current commit SHA, checking exit code 0, and reading full output.

### VI. Fail-Closed Boundaries & Safety Invariants
All error boundaries, update supervisors, transport handlers, and batch action state machines must fail closed. If a release asset hash mismatches, symlink fails, or socket disconnects, the system must cleanly reject or reconnect rather than silently degrading into an invalid state.

### VII. End-to-End Traceability
Every requirement must trace directly to a specification, acceptance criteria, test case, and verification evidence. Large features must define dependency-ordered tasks and pass cross-artifact consistency analysis before implementation.

### VIII. Human-Gated Merges
All changes must pass automated test suites, adversarial review, and quality gates on the final HEAD before merge. Never auto-merge without explicit maintainer approval.

## Governance

1. **Hierarchy of Authority:** User Request → `AGENTS.md` → Project Constitution → Spec Kit Artifacts → Serena Semantic Retrieval → ECC/Superpowers Specialists → Tests & Verification Evidence.
2. **Constitution Amendments:** Any update requires a documented rationale, version bump, and compatibility check against `AGENTS.md`.
3. **Runtime Guidance:** Developers and AI assistants must follow this constitution and `AGENTS.md` across all development workflows.

**Version**: 1.0.0 | **Ratified**: 2026-08-17 | **Last Amended**: 2026-08-17
