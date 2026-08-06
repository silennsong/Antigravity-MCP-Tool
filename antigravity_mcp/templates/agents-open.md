## External Antigravity worker

Before the first delegation in a new or renamed workspace, call `check_antigravity_readiness`. Explain every missing prerequisite and its exact action. The readiness check must not change project files or global permissions.

The global `delegate_to_antigravity` MCP is available as a general external worker. Decide whether to delegate based on the current task, cost of independent context reading, and the value of a second model. Pass this project as `workspace` and state the intended task kind, model tier, mode, and expected output explicitly.

The project policy is `.codex/antigravity-policy.json`. Keep routing decisions and execution permissions appropriate to this repository.
