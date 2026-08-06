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
7. Run antigravity-delegate-mcp doctor and report every warning or failure.
8. For the current project, run init-project with the read-only profile only after reviewing existing AGENTS.md and .codex files.
```

OAuth, terms, data-use choices, and project trust confirmation remain user-controlled interactive steps.
