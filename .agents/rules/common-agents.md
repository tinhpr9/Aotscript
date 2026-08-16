# Agent Orchestration

## Available Subagents

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| **planner** | Implementation planning | Complex features, refactoring |
| **code-architect** | Architecture design | Architectural blueprints |
| **architect** | System design | System-level decisions |
| **tdd-guide** | Test-driven development | New features, bug fixes |
| **code-reviewer** | Code review | After writing code |
| **python-reviewer** | Python code review | Python runtime / supervisor changes |
| **typescript-reviewer** | JS/TS code review | Cloudflare Worker changes |
| **silent-failure-hunter** | Bug & exception hunting | Swallowed errors detection |
| **security-reviewer** | Security analysis | Auth, input validation, permissions |
| **build-error-resolver** | Fix build errors | When test / build fails |

## Immediate Agent Usage

No user prompt needed:
1. Complex feature requests - Use **planner** agent
2. Code just written/modified - Use **code-reviewer** agent
3. Bug fix or new feature - Use **tdd-guide** agent
4. Architectural decision - Use **architect** agent

## Parallel Task Execution

Use parallel subagent execution for independent operations:

```markdown
Launch subagents in parallel:
1. Agent 1: Security analysis of batch module
2. Agent 2: Code review of relay controller
```

## Delegation Completion Contract

1. **Your final message IS the deliverable.** Wait for delegated tasks to complete before responding.
2. **If you delegate, you own collection.** Integrate findings before completing your turn.
