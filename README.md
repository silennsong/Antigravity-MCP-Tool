# Antigravity Delegate MCP

A distributable local-STDIO MCP server that lets Codex invoke Antigravity CLI from any project. The global server is policy-neutral; each project owns its routing guidance and enforceable policy.

## Install from a release package

Clone the public repository:

```bash
git clone --depth 1 https://github.com/silennsong/Antigravity-MCP-Tool.git
cd Antigravity-MCP-Tool
python3 bootstrap.py --install-agy
```

Or download and extract the source archive from the latest GitHub Release, inspect `bootstrap.py`, then run:

```bash
python3 bootstrap.py --install-agy
```

The bootstrapper:

1. Creates an isolated venv under `~/.local/share/antigravity-delegate-mcp/venv`.
2. Installs this package without modifying the system Python environment.
3. Optionally installs `agy` from Google's official installer.
4. Registers `antigravity_delegate` globally through `codex mcp add`.
5. Preserves unrelated Codex configuration and adds MCP timeout/tool settings.
6. Runs `doctor`.

For interactive onboarding as part of setup:

```bash
python3 bootstrap.py --install-agy --auth --configure-models
```

Google OAuth, terms, data-use choices, and workspace trust require the user to interact with Antigravity. The installer does not silently accept them.

See `AGENT_INSTALL.md` for a prompt that can be copied to a local coding agent.

Project homepage: <https://github.com/silennsong/Antigravity-MCP-Tool>

## Commands

```bash
antigravity-delegate-mcp install --replace
antigravity-delegate-mcp auth --workspace /path/to/project
antigravity-delegate-mcp configure-models
antigravity-delegate-mcp init-project --workspace /path/to/project --profile read-only
antigravity-delegate-mcp validate-policy --workspace /path/to/project
antigravity-delegate-mcp doctor --workspace /path/to/project
antigravity-delegate-mcp doctor --deep --workspace /path/to/project
```

`init-project` offers two profiles:

- `read-only`: generates routing rules, a restrictive project policy, a local JSON Schema, and maps the mode to `agy --mode plan --sandbox --disable-slash-commands`.
- `open`: creates a minimal project policy without global task/model/mode restrictions.

Existing `AGENTS.md` files are not overwritten. Use `--force` to append a marked Antigravity section after reviewing the file.

The read-only profile also adds scoped Antigravity CLI grants in `~/.gemini/antigravity-cli/settings.json`: recursive `read_file(<project>)` is allowed and `write_file(<project>)` is denied. Use `--no-agy-permissions` to skip that change.

## Responsibility split

Global layer:

- Registers one `delegate_to_antigravity` MCP tool.
- Resolves stored or environment-provided model names.
- Adapts to the verified `agy` print-mode flags.
- Applies process timeout/output handling and returns structured metadata.
- Does not globally hard-code task categories, model routing, or read/write behavior.

Project layer:

- `AGENTS.md` tells Codex when delegation is useful.
- `.codex/antigravity-policy.json` defines enforceable task, model, mode, prompt, CLI-argument, output, and environment rules.
- `.codex/antigravity-policy.schema.json` gives editors a local policy schema.
- The closest policy found while walking upward from `workspace` is used.

Without a project policy, the global MCP applies no semantic task restriction.

## Authentication and model configuration

The official Antigravity CLI installer for macOS/Linux is:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

This project downloads the installer before executing it instead of piping it blindly. To complete Google OAuth and project trust:

```bash
antigravity-delegate-mcp auth --workspace /path/to/project
```

Then configure exact model IDs returned by `agy models`:

```bash
antigravity-delegate-mcp configure-models \
  --flash gemini-3.6-flash-low \
  --pro gemini-3.1-pro-high \
  --non-interactive
```

Mappings are stored with mode `0600` in:

```text
~/.config/antigravity-delegate/config.json
```

Environment variables such as `ANTIGRAVITY_FLASH_MODEL` override stored mappings. Exact `model` input is also supported when project policy permits it.

## Headless permissions

Antigravity print mode cannot display interactive tool-approval cards. A project that needs file access must first complete workspace trust or configure appropriately scoped Antigravity permission grants. `init-project --profile read-only` handles the scoped read/deny-write grant automatically. Do not solve this with `--dangerously-skip-permissions` unless the project explicitly chooses that risk.

The MCP treats exit code 0 with empty stdout as an error and returns Antigravity's permission explanation instead of reporting false success.

## Tool input

```json
{
  "task": "Inspect the repository entry points and map the main modules.",
  "task_kind": "repository_exploration",
  "workspace": "/absolute/path/to/current/project",
  "model_tier": "flash",
  "mode": "read_only",
  "max_output_chars": 12000,
  "timeout_seconds": 600
}
```

Globally, `task_kind`, `model_tier`, and `mode` accept project-defined strings. Project policy may restrict them. Passing `model` uses an exact Antigravity model name instead of tier lookup.

## Project policy fields

- `version`
- `allowed_task_kinds`
- `allowed_model_tiers`
- `allow_explicit_model`
- `allowed_modes`
- `model_tier_task_kinds`
- `mode_cli_args`
- `min_task_chars`, `max_task_chars`
- `max_output_chars`, `max_timeout_seconds`
- `forbidden_task_patterns`
- `worker_instructions`
- `required_output_sections`
- `forward_env`

Use `validate-policy` or `doctor` before relying on a new policy.

## Build distributable artifacts

```bash
python3 -m build
```

Outputs:

```text
dist/antigravity_delegate_mcp-<version>-py3-none-any.whl
dist/antigravity_delegate_mcp-<version>.tar.gz
```

## Verification

```bash
python3 -m unittest discover -s tests -v
antigravity-delegate-mcp doctor --deep --workspace /path/to/project
codex mcp get antigravity_delegate
```

Runtime Python dependencies are intentionally empty. Python 3.10 or newer is required.
