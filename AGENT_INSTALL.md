# Copy-paste installation prompt

Give the following block to a local Codex agent:

```text
Install Antigravity Delegate MCP from:
https://github.com/silennsong/Antigravity-MCP-Tool

1. Clone the repository to an appropriate local tools directory.
2. Inspect bootstrap.py before running it.
3. Run: python3 bootstrap.py --install-agy
4. Do not overwrite unrelated Codex configuration.
5. Run antigravity-delegate-mcp auth and pause for me to complete Google OAuth.
6. Run antigravity-delegate-mcp configure-models and let me choose exact model names.
7. Confirm the global MCP registration exposes both check_antigravity_readiness and delegate_to_antigravity. Ask me to restart Codex before testing them.
8. In a new Codex task, call check_antigravity_readiness for the current project before delegating. This check must not change any files or permissions.
9. Report every readiness check and its exact action. Do not silently repair global permissions.
10. Review existing AGENTS.md and .codex files before running init-project. Use --force only with my approval to append the marked routing block.
11. Use permissions audit first. Run permissions sync or permissions prune --stale --yes only after showing me the exact changes and receiving approval.
12. Run antigravity-delegate-mcp doctor --deep and report every warning or failure.
```

OAuth, terms, data-use choices, and project trust confirmation remain user-controlled interactive steps.
