---
name: gemini-delegation-router
description: Route suitable subtasks to the antigravity_delegate Gemini MCP while Codex remains the primary agent. Use when the user says Gemini MCP or Antigravity may be used during a task, including Chinese prompts such as “过程中可以调用 Gemini MCP”, or asks for token-efficient delegation, repository exploration, large-context reading, extraction, summaries, first drafts, independent review, adversarial review, a second opinion, or Flash/Pro model routing.
---

# Gemini Delegation Router

Use Gemini as a selective worker, not as the owner of the overall task.

## Route the work

1. Identify independent subtasks before delegating. Prefer work that saves substantial context or produces an independently useful perspective.
2. Call `check_antigravity_readiness` before the first delegation in a new, moved, or renamed workspace. Pass the current workspace explicitly.
3. If readiness is incomplete, report its checks and exact actions. Do not silently change authentication, global permissions, project policy, or an existing `AGENTS.md`.
4. Call `delegate_to_antigravity` only for a well-bounded subtask. Pass a self-contained task, absolute workspace, meaningful `task_kind`, selected `model_tier`, mode, and proportionate timeout.
5. Respect the closest `.codex/antigravity-policy.json`. Treat a policy rejection as a project boundary, not as a reason to bypass the MCP.
6. Verify important Gemini claims against local files, commands, tests, or primary sources. Codex owns final decisions, edits, tests, and the user-facing answer.

## Select useful delegations

Prefer delegation for:

- broad repository or document exploration that would consume substantial primary-agent context;
- repetitive extraction, classification, comparison, summarization, boilerplate, and first drafts;
- a genuinely independent interpretation or second opinion;
- adversarial review of a proposed design, diagnosis, test plan, or completed change;
- parallelizable research whose result can be checked against concrete evidence;
- escalation after a prior Gemini result was incomplete and a stronger pass has clear value.

Keep work in Codex when delegation overhead exceeds the likely benefit, the required context is already loaded, the subtask cannot be isolated, or the work is the final synthesis, consequential judgment, direct edit, or acceptance decision. Project policy may permit broader execution; do not treat these routing preferences as global runtime prohibitions.

## Choose the model

- Use `flash` for routine exploration, extraction, summaries, comparisons, boilerplate, test-case drafts, and other high-volume first passes.
- Use `pro` for unusually large-context review, adversarial review, second opinions, ambiguous cross-module reasoning, or escalation after an inadequate Flash result.
- Use a project-defined tier when its policy or task requires one. Do not invent exact model IDs; let configured tiers resolve them unless the project explicitly allows an exact model.

## Construct the assignment

Include:

- the precise outcome and exclusions;
- relevant paths, symbols, artifacts, or questions;
- the expected evidence and output structure;
- the current workspace and requested mode;
- enough context to stand alone without relying on earlier conversation.

After the result returns, distinguish Gemini's observations from Codex's verified conclusions and identify any uncertainty or disagreement.
