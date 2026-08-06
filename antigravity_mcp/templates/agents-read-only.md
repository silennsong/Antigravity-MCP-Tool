## External Antigravity worker

Before the first delegation in a new or renamed workspace, call `check_antigravity_readiness`. Report missing policy, routing, authentication, model, and file-permission checks with their exact actions. The readiness check is read-only. Do not run permission sync or confirmed pruning without explicit user approval.

Use `delegate_to_antigravity` selectively when an external worker can independently inspect substantial context and return a shorter result. Prefer the `flash` tier for routine exploration and drafts. Use `pro` only for large-context review, a genuine second opinion, or a follow-up after an incomplete Flash result.

Do not delegate small tasks, context Codex already read, final decisions, sensitive credentials, destructive operations, deployments, or direct working-tree edits. Codex verifies evidence, performs all edits and tests, and owns final conclusions.

The enforceable execution policy is `.codex/antigravity-policy.json`.
