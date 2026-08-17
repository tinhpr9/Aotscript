---
trigger: always_on
---

# Code Review Standards

## Purpose

Code review ensures quality, security, and maintainability before code is integrated. This rule defines the code review standards for Aotscript.
Project-level release rules in `AGENTS.md` take absolute precedence over this document.

## When to Review

**Mandatory review triggers:**
- After writing or modifying code
- When security-sensitive code is changed (auth, WebSocket actions, IPC, intents)
- When architectural changes or protocol modifications are made
- Before creating a pull request

## Review Checklist

Before marking code complete:
- [ ] Code is readable, idiomatic, and well-named
- [ ] Functions are focused and maintainable
- [ ] Errors are handled explicitly; no silent exception swallowing
- [ ] No hardcoded secrets, credentials, or private URLs
- [ ] Tests exist for new functionality and regression paths
- [ ] Conforms to fail-closed and rollback requirements in `AGENTS.md`

## Security Review Triggers

**Engage security review when:**
- Handling remote inputs or WebSocket batch actions
- Intent actions or root command executions
- File system and staging operations
- Cryptographic checks, hash verification, or token parsing

## Review Severity Levels

| Level | Meaning | Action |
|-------|---------|--------|
| CRITICAL | Security vulnerability or data loss risk | **BLOCK** - Must fix before merge |
| HIGH | Bug or significant quality issue | **WARN** - Should fix before merge |
| MEDIUM | Maintainability concern | **INFO** - Consider fixing |
| LOW | Style or minor suggestion | **NOTE** - Optional |

## Agent Usage

Use these subagents for code review:

| Agent | Purpose |
|-------|---------|
| **code-reviewer** | General code quality, patterns, best practices |
| **security-reviewer** | Security vulnerabilities, OWASP Top 10, secret scanning |
| **typescript-reviewer** | Cloudflare Worker JavaScript/TypeScript review |
| **python-reviewer** | AOT Python worker and supervisor review |
| **silent-failure-hunter** | Detection of swallowed errors and unhandled fallbacks |

## Review Workflow

1. Run `git diff` to understand changes.
2. Review security checklist.
3. Review code quality checklist.
4. Run relevant tests.
5. Use appropriate reviewer subagents for detailed analysis.

## Common Issues to Catch

### Security
- Hardcoded credentials or tokens
- Command injection in shell executions
- Path traversal in file operations
- Missing parameter sanitization

### Code Quality
- Deep nesting (use early returns)
- Missing error handling (handle explicitly)
- Missing unit or integration tests
- Silent fallbacks without logging
