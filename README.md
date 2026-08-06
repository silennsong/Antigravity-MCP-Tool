# Antigravity Delegate MCP

A distributable local-STDIO MCP server that lets Codex invoke Antigravity CLI from any project. The global server is policy-neutral; each project owns its routing guidance and enforceable policy.

Version 0.4 adds a read-only first-run readiness tool. A new or renamed project can expose every missing prerequisite—CLI, authentication, model mapping, project policy, routing guidance, file permissions, and stale historical paths—without silently changing global or project configuration.

Version 0.4 also packages the MCP and `gemini-delegation-router` Skill as one Codex Plugin. After installation, Codex can implicitly route suitable subtasks when a user says phrases such as “过程中可以调用 Gemini MCP”, while project policy and Codex review remain in control.

## Install the Codex Plugin from GitHub

After this version is merged to the repository's default branch:

```bash
codex plugin marketplace add silennsong/Antigravity-MCP-Tool
codex plugin add antigravity-mcp@antigravity-tools
```

Start a new Codex task after installation so the bundled Skill and MCP tools are loaded. A natural-language trigger is enough:

```text
完成这个任务，过程中可以调用 Gemini MCP。
```

Explicit invocation is the most deterministic option:

```text
使用 $gemini-delegation-router 完成这个任务。
```

The Plugin bundles its dependency-free Python MCP runtime, so its server does not depend on the repository checkout path or a globally installed Python package. It still needs Python 3.10+ and the Antigravity CLI. On first use, `check_antigravity_readiness` exposes copy-paste commands for any missing CLI installation, OAuth, model mapping, project policy, or scoped workspace permission. Interactive Google consent and project trust remain user-controlled.

For local development of this branch:

```bash
codex plugin marketplace add /absolute/path/to/Antigravity-MCP-Tool
codex plugin add antigravity-mcp@antigravity-tools
```

The marketplace definition is stored in `.agents/plugins/marketplace.json`; the installable package is under `plugins/antigravity-mcp`.

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

## First project connection

Restart Codex after installation, open the target project, and ask the Agent:

```text
Call the MCP tool check_antigravity_readiness for the current workspace before any delegation.
Report every failed or missing check and the exact suggested commands.
Do not change global permissions or project files unless I explicitly approve the relevant command.
```

The readiness tool is read-only. It reports two independent states:

- `ready_to_delegate`: Antigravity CLI, authentication, and the requested model tier work.
- `safe_project_ready`: the project also has policy, AGENTS routing, and a read/deny-write permission pair.

For a complete explicit first-run setup from the terminal:

```bash
antigravity-delegate-mcp setup \
  --workspace /path/to/project \
  --auth \
  --configure-models \
  --init-project \
  --profile read-only \
  --deep
```

If the project already has `AGENTS.md`, inspect it first. Add `--force` only when you want the installer to append its clearly marked Antigravity routing block; existing content is preserved.

## Commands

```bash
antigravity-delegate-mcp install --replace
antigravity-delegate-mcp auth --workspace /path/to/project
antigravity-delegate-mcp configure-models
antigravity-delegate-mcp init-project --workspace /path/to/project --profile read-only
antigravity-delegate-mcp validate-policy --workspace /path/to/project
antigravity-delegate-mcp permissions audit --workspace /path/to/project
antigravity-delegate-mcp permissions sync --workspace /path/to/project
antigravity-delegate-mcp permissions prune --stale
antigravity-delegate-mcp doctor --workspace /path/to/project
antigravity-delegate-mcp doctor --deep --workspace /path/to/project
```

`init-project` offers two profiles:

- `read-only`: generates routing rules, a restrictive project policy, a local JSON Schema, and maps the mode to `agy --mode plan --sandbox --disable-slash-commands`.
- `open`: creates a minimal project policy without global task/model/mode restrictions.

Existing `AGENTS.md` files are not overwritten. Use `--force` to append a marked Antigravity section after reviewing the file.

The read-only profile also adds scoped Antigravity CLI grants in `~/.gemini/antigravity-cli/settings.json`: recursive `read_file(<project>)` is allowed and `write_file(<project>)` is denied. Use `--no-agy-permissions` to skip that explicit initialization change.

Absolute-path permissions are global Antigravity CLI state. Renaming or moving a project can leave a stale historical rule. The lifecycle is deliberately explicit:

1. `permissions audit` reads current coverage and stale paths without changing anything.
2. `permissions sync` adds only the supplied workspace's read/deny-write pair.
3. `permissions prune --stale` is preview-only.
4. Rerun the prune command with `--yes` only after reviewing every listed rule.

Delegation never edits this permission file automatically. Permission or authentication failures return structured `onboarding_required`, `code`, and `actions` fields so an Agent can explain exactly what is missing.

## Responsibility split

Global layer:

- Registers `delegate_to_antigravity` plus the read-only `check_antigravity_readiness` tool.
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

Readiness may therefore report `ready_to_delegate: true` and `safe_project_ready: false`. This distinction preserves a policy-neutral global entrypoint while making incomplete project onboarding visible.

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

It classifies common missing prerequisites for Agents:

- `antigravity_cli_missing`
- `model_mapping_missing`
- `authentication_required`
- `workspace_read_permission_missing`

Each classified failure includes copy-paste remediation commands and confirms that no configuration change was made.

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

Each forbidden-pattern entry may set `ignore_negated: true` when phrases such as “do not delete files” should not be treated as a request to perform the forbidden action.

## Build distributable artifacts

```bash
python3 scripts/build_plugin_bundle.py
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

Plugin-specific validation:

```bash
python3 scripts/build_plugin_bundle.py
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/antigravity-mcp/skills/gemini-delegation-router
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/antigravity-mcp
```
