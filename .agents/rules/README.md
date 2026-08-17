---
description: Overview of Aotscript ECC rules architecture.
trigger: model_decision
---

# Aotscript Pruned ECC Rules

This directory contains the minimal, high-value ruleset configured for Aotscript:

## Rule Layers

1. **Common Rules** (`common-*.md`):
   - Universal quality, review standards, performance, security, and testing guidelines.
2. **Python Stack** (`python-*.md`):
   - Coding style, patterns, security, and testing for Python worker runtime, controller, and supervisor scripts.
3. **TypeScript / JavaScript Stack** (`typescript-*.md`):
   - Coding style, patterns, security, and testing for Cloudflare Worker backend and tests.

## Priority Order

1. **`AGENTS.md`**: Top-level immutable release rules, 2PC fail-closed protocols, and fleet supervisor policies.
2. **Stack Rules** (`python-*`, `typescript-*`): Specific language patterns.
3. **Common Rules** (`common-*`): Universal defaults.
