# OpenCode Computer-Use Platform — Product Requirements Document

**Status:** Final v2 · **Doc:** 1 of 4 (PRD → Architecture → Master Prompt → Roadmap + Engineering Decisions)
**Owner:** you · **Last updated:** 2026-08-18

---

## 0. Read this first — how this PRD treats the original proposal

This is a revised pass over an earlier draft. Five things change the design in ways worth flagging before the rest of the document builds on them:

1. **OpenCode is already MCP-native.** Current OpenCode (v1.17.13, released 2026-07-01) ships an MCP **client** — it connects to external local and remote MCP servers and exposes their tools to the model, but it does not itself run as an MCP server. It also has a JS/TS plugin API (V2, namespaced hooks, Effect/Promise support), a headless server mode (`opencode serve`), and native support for MCP resources — including a feature that shipped in v1.17.10 where **an MCP server's own `instructions` field lands directly in session context**, no plugin required. A bespoke "CUA Gateway/Bridge" protocol isn't needed — the Gateway can *be* one or more MCP servers, and it can hand OpenCode the Master Prompt through the standard MCP `instructions` mechanism rather than requiring custom plugin code just to get text into context. That single fact satisfies the "minimal or no modification to OpenCode core" goal almost for free. An OpenAI-compatible completions layer is still worth exposing (§7), but as a secondary interface, not the primary one.
2. **UI-TARS and Browser-Use both already ship MCP-friendly integration paths, not just raw models or libraries** — but neither is a drop-in, zero-code wrapper, and they fail in the same way if treated carelessly. UI-TARS-desktop / Agent TARS bundles its own planning loop around the model. Browser-Use's canonical Python `Agent` class and its published MCP servers do the same thing: they accept a natural-language task and run their *own* LLM-driven ReAct loop (their own tool registry, their own step-by-step reasoning, their own "done" decision) to complete it end-to-end, requiring their own model/API key. Wrapped naively, **both** would hand OpenCode a second, independent planner sitting underneath the first one — precisely the "duplicated reasoning" failure mode this project explicitly wants to avoid. This PRD scopes both integrations the same way: the Gateway drives the *primitive, single-action* layer underneath each tool (UI-TARS as a bare grounding model; Browser-Use's lower-level `Controller`/`Tools` action registry and `BrowserSession` DOM state, not its bundled `Agent`), so OpenCode stays the only planner in the loop.
3. **Running UI-TARS and Qwen-UI-Agent as parallel, interchangeable GUI executors from day one is redundant for an MVP.** Both are strong, both are moving fast (UI-TARS-2's technical report published September 2025, with no open weights as of this writing — only UI-TARS-1.5 and the original UI-TARS 2B/7B/72B line are actually downloadable; Qwen-UI-Agent, from Alibaba's Tongyi-MAI group, shipped as a follow-up to MAI-UI in late July 2026 and is itself a broader real-world agent spanning mobile, computer, web, and search — closer in shape to Agent TARS than to a pure grounding model). Standing up two GUI-grounding stacks simultaneously doubles integration and eval surface for a benefit that can't yet be measured. This PRD scopes MVP around **one** primary GUI executor behind a swappable interface, with a second wired in as a Phase 2 validation exercise, not a launch requirement.
4. **The MVP's actual GUI executor model is UI-TARS-1.5-7B or UI-TARS-7B-DPO (the openly-released 1.0 line), not "UI-TARS-2."** UI-TARS-2 (arXiv:2509.02544, Sept 2025) is referenced throughout these docs as the current state-of-the-art benchmark point for the UI-TARS line, but ByteDance has not published its weights; the actively-requested-but-still-pending Hugging Face release issue for UI-TARS-1.5/2 remains open. What *is* available and already MLX-converted under `mlx-community` (e.g., `mlx-community/UI-TARS-1.5-7B-4bit`, `mlx-community/UI-TARS-7B-DPO-4bit`) is the model this plan actually deploys. Every reference to "UI-TARS" as the MVP executor in this doc set means that openly-licensed checkpoint, not the closed-weight UI-TARS-2 report numbers.
5. **MCP's standing as the right protocol choice got stronger, not weaker, since the first draft.** In December 2025 Anthropic donated MCP's governance to the Linux Foundation's Agentic AI Foundation, with joint stewardship from Anthropic, OpenAI, Google, Microsoft, AWS, Cloudflare, and Bloomberg. That's a materially different risk profile than "a protocol one vendor controls," and it reinforces the choice made in §7 and Architecture §8.

None of this shrinks the vision — it removes accidental complexity and closes a real correctness gap (the Browser-Use planning-duplication risk) so effort goes into the actually-hard parts: verification, recovery, and state management.

---

## 1. Problem Statement

Coding agents (OpenCode, Claude Code, Codex-style CLIs) are good at reasoning about code, files, and terminals, but blind to anything that only exists as pixels: desktop apps without CLIs or APIs, university portals, legacy internal tools, and the long tail of software that will never expose a clean interface. Existing computer-use agents (Claude Computer Use, UI-TARS-desktop, Agent TARS, Qwen-UI-Agent, OpenAI's agent tooling) solve the "see and click" problem in isolation, but they aren't designed to sit *underneath* a general-purpose planning agent that also needs to touch the filesystem, run shell commands, and reason across a multi-step objective that spans both code and GUI.

There is no small, open, provider-agnostic layer that lets a capable planning agent like OpenCode treat "click this button" and "run this shell command" as peers in the same tool-call loop, with the same standard for verifying that the action actually worked — and that doesn't accidentally delegate planning to a worker that has opinions of its own about how to finish the job.

## 2. Vision

A single orchestrator (OpenCode) that can pursue a real-world objective spanning terminal, filesystem, browser, and arbitrary desktop GUIs — delegating GUI and browser execution to specialized workers through a common, swappable interface, and never claiming a task is done without checking the resulting state. Every worker in the loop executes grounded, single-step actions and reports back; none of them plans on OpenCode's behalf. The platform should be small enough for one developer to run locally on a Mac, and open enough that any piece (the GUI model, the browser engine, the orchestrator itself) can be swapped without touching the others.

## 3. Goals and Non-Goals

**Goals**
- Let OpenCode delegate GUI actions and browser actions to specialized workers via a standard tool interface (MCP), with no bespoke protocol invented where MCP already covers the need.
- Make "did the action actually work" a first-class, inspectable step — not an assumption.
- Ensure every worker OpenCode calls executes one grounded action or observation per call and never runs its own independent planning loop underneath OpenCode's.
- Keep every component swappable: GUI model, browser engine, orchestrator, verification source.
- Ship something a single developer can install and run against real macOS apps within weeks, not months.
- Preserve session/task state across a multi-step objective that may take minutes to hours.

**Non-Goals (for this platform, at least initially)**
- Building a new foundation GUI-grounding model. Use existing ones (UI-TARS, Qwen-UI-Agent).
- Building a new browser-automation engine. Use Browser-Use's underlying Playwright-driven session and action registry.
- Full autonomy with no permission gates. Destructive actions always require an explicit policy check.
- Multi-user, multi-tenant, or cloud-hosted operation in the MVP. This is a local-first, single-user tool first.
- Windows/Linux GUI support in the MVP. Design for it; don't build it yet.

## 4. Personas and Use Cases

| Persona | Description | Primary need |
|---|---|---|
| **Builder-operator (primary)** | A developer who already lives in OpenCode/terminal workflows and wants it to reach into GUI apps and websites it currently can't touch. | Reliable delegation without babysitting every click. |
| **Power user / prosumer** | Non-developer comfortable installing a CLI tool, wants repeatable automation of a personal workflow (portal downloads, form-filling, file organizing). | A task that "just runs" and tells the truth about whether it worked. |
| **Researcher/evaluator** | Wants to benchmark computer-use agents (OSWorld-, WebArena-style tasks) using a consistent orchestration harness. | Reproducible task execution + structured logs for scoring. |

**Representative use cases**
1. Download a file from a portal that has no API, rename and move it, then update a README to reference it (the MVP acceptance scenario, see §13).
2. Fill out a repetitive multi-field form in a legacy desktop app that has no accessibility metadata worth trusting, requiring pixel-level interaction.
3. Research a topic across multiple tabs, extract structured data, and write a summary file.
4. Run the same GUI-touching task nightly and get a pass/fail + diff, not just a transcript.

## 5. User Stories

- As a builder-operator, I want to give OpenCode a natural-language objective that requires both terminal and GUI steps, so I don't have to manually bridge the two myself.
- As a builder-operator, I want OpenCode to tell me *why* it believes a step succeeded (what it verified), not just that it executed an action.
- As a builder-operator, I want destructive actions (delete, submit, pay, send) to require my explicit go-ahead unless I've pre-approved that class of action.
- As a power user, I want a failed GUI step to trigger a retry or an alternate strategy, not a silent "done."
- As a researcher, I want every session's plan, actions, observations, and verification results logged in a structured, replayable format.
- As any user, I want to swap the GUI executor (UI-TARS ↔ Qwen-UI-Agent, or a future model) without touching my task definitions, and I want to trust that no worker in the loop is quietly making its own plan.

## 6. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | OpenCode can decompose a natural-language objective into subtasks and assign each to terminal, filesystem, browser, or GUI execution. | MVP |
| FR-2 | A CUA Gateway exposes GUI action primitives (click, type, scroll, drag, key combo, screenshot) as MCP tools callable by OpenCode. | MVP |
| FR-3 | A Browser Adapter exposes navigation, extraction, form-filling, and multi-tab actions as MCP tools, built on Browser-Use's lower-level action registry and DOM/accessibility state (not Browser-Use's bundled autonomous `Agent`), preferring DOM/accessibility data over screenshots when available. | MVP |
| FR-4 | Every GUI/browser action is followed by an observation step (screenshot and/or accessibility/DOM read) before the orchestrator is allowed to consider the step complete. | MVP |
| FR-5 | A verification step compares observed state against the expected state for the subtask and returns pass/fail/uncertain, not just "action executed." | MVP |
| FR-6 | On verification failure, the system attempts a bounded number of recovery strategies (retry, re-observe, alternate executor, ask user) before surfacing failure. | MVP |
| FR-7 | Session state (objective, plan, completed subtasks, artifacts, open questions) persists across the life of a task and survives a gateway restart. | MVP |
| FR-8 | The GUI executor backend is swappable via configuration (UI-TARS ↔ Qwen-UI-Agent's grounding mode ↔ future model) without changing OpenCode-facing tool contracts. | MVP (interface) / Phase 2 (second backend wired in) |
| FR-9 | Users can define a permission policy that gates destructive actions (delete, submit/pay, send, overwrite) behind explicit confirmation. | MVP |
| FR-10 | All actions, observations, verifications, and recoveries are logged in structured (JSON) form with timestamps and session/task IDs. | MVP |
| FR-11 | The system supports running a second, independent GUI/browser worker task in parallel to a primary session. | Phase 2 |
| FR-12 | A benchmarking harness can run a batch of OSWorld/WebArena-style tasks against the platform and score pass/fail automatically. | Phase 2 |
| FR-13 | No tool exposed to OpenCode may run an independent LLM-driven planning loop of its own (e.g., Browser-Use's stock `Agent`, UI-TARS-desktop's Agent TARS mode, Qwen-UI-Agent's DeepSearch/full-agent mode). Every MCP tool the Gateway or Browser Adapter registers executes one bounded, OpenCode-directed action or observation per call. | MVP |

## 7. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Portability** | Core orchestration and gateway logic must not import macOS-only APIs directly; platform-specific execution (AXUIElement, screenshot capture) lives behind an OS-adapter interface. |
| **Latency** | A single GUI action (screenshot → model inference → action dispatch) should complete in well under the time a human would take for the same action on a local 7B-class model, acknowledging that vision-language inference is the dominant cost. |
| **Local-first** | The MVP must run entirely on a single Apple Silicon Mac with no required cloud dependency, though remote model providers should be a drop-in config option. Note: Ollama's MLX-accelerated backend (see §Roadmap/Engineering Decisions) requires 32GB+ unified memory and silently falls back to its slower llama.cpp/Metal path below that — the target hardware spec should account for this. |
| **Interoperability** | Primary tool-calling protocol is MCP, now under Linux Foundation (AAIF) governance. An OpenAI-compatible `/v1/chat/completions`-shaped surface may be exposed by the gateway as a secondary interface for tools that don't speak MCP, not as the primary contract. |
| **Resilience** | A crash or restart of any single worker (browser, GUI executor) must not corrupt session state or silently drop task progress. |
| **Auditability** | Every session must be fully reconstructable from logs: what was planned, what was executed, what was observed, what was verified, and why any recovery was triggered. |
| **Safety** | No destructive action executes without passing the permission policy check, regardless of which worker or model is driving it. |
| **Planning integrity** | No worker tool may be configured or invoked in a mode that gives it its own end-to-end task loop; this is enforced at integration time (FR-13), not left as a hope. |

## 8. Agent / Tool Requirements

- **OpenCode (orchestrator):** owns planning, subtask decomposition, tool selection, session/context management, and final objective verification. Runs as-is, in `opencode serve` headless mode, driven by an MCP config pointing at the CUA Gateway and Browser Adapter.
- **CUA Gateway:** the primary component this project builds from scratch. Normalizes GUI action primitives, screenshot/state capture, and verification calls across whichever GUI executor is configured. Exposed to OpenCode as MCP tools, and supplies the Master Prompt to OpenCode via its MCP `instructions` field.
- **GUI executor (UI-TARS-1.5-7B / UI-TARS-7B-DPO primary for MVP):** consumes screenshots + instruction, returns grounded actions (coordinates, key sequences). Runs locally via MLX (see Architecture §5, §14) — this choice is a named architecture decision, not assumed.
- **Browser Adapter:** a second, smaller piece of new code (not literally zero), built on Browser-Use's `Controller`/`Tools` action registry and `BrowserSession` (Playwright-backed, DOM/accessibility-first state), driven one action at a time by the same `act_and_verify` loop the Gateway uses for GUI tasks. Explicitly does **not** wrap Browser-Use's bundled `Agent`, which runs its own LLM planning loop.
- **Verification/Recovery layer:** logically part of the CUA Gateway for MVP (not a separate service), and shared by both the GUI and Browser Adapter action loops — see Architecture doc for why a separate microservice is premature here.

## 9. Computer-Use Requirements

- Must support, at minimum: click (single/double/right), drag, scroll, type text, key combinations, and screenshot capture.
- Must be able to identify application/window focus and switch between windows/apps as part of a task.
- Must prefer accessibility-tree or DOM data over raw screenshots wherever the target surface exposes it, falling back to vision-only grounding otherwise (this materially affects cost and reliability — flagged as a real engineering task in the Architecture doc, not a one-line abstraction).
- Must tag every action with the source of ground truth used to decide it (vision-only, accessibility tree, DOM) for later debugging.
- Browser tasks must go through the Browser Adapter's single-action primitives (navigate, click, type, extract, scroll), not through Browser-Use's own multi-step agentic task runner.

## 10. Security and Permission Model

- **Default-deny for destructive actions.** A configurable policy file defines action classes (e.g., `file.delete`, `form.submit`, `payment.*`, `email.send`) that require explicit per-session or per-action user confirmation.
- **Scoped sessions.** Each task session declares the applications/domains it's permitted to touch; the gateway rejects actions outside that scope.
- **No credential handling by the GUI/vision model.** Secrets (passwords, tokens) are injected by a dedicated credential step outside the vision-language loop wherever possible, to avoid putting secrets in screenshots sent to a model provider.
- **Local-first data handling.** Screenshots and DOM captures stay on-device unless the user has explicitly configured a remote model provider; that tradeoff is surfaced in setup, not buried in defaults.
- **Kill switch.** A single command/hotkey halts all in-flight GUI/browser actions immediately.

## 11. Failure and Recovery Requirements

- Every action has an expected post-condition defined at plan time (even if coarse-grained, e.g., "a file named X exists in Downloads").
- Failure to reach the expected post-condition triggers, in order: (1) re-observe and retry once, (2) attempt an alternate executor or strategy if one is configured, (3) escalate to the user with the current state and a proposed next step. The system never silently marks a failed step as complete.
- A bounded retry/recovery budget (attempts, wall-clock time) is enforced per subtask to prevent infinite loops.
- All recovery attempts are logged with the reasoning that triggered them.

## 12. Observability

- Structured, session-scoped logs (JSON lines) covering: plan, subtask assignments, tool calls, raw observations (screenshot refs / DOM snippets), verification verdicts, recovery actions, and final outcome.
- A human-readable session transcript view (even a simple local HTML/Markdown render) for debugging without parsing JSON by hand.
- Basic metrics from day one: subtask success rate, average recovery attempts per subtask, wall-clock time per subtask type (GUI vs browser vs terminal).

## 13. MVP Scope

**In scope:**
- OpenCode as orchestrator via `opencode serve` + MCP config.
- CUA Gateway as one MCP server exposing GUI primitives, backed by **one** GUI executor (UI-TARS-1.5-7B / UI-TARS-7B-DPO, served locally via MLX).
- A Browser Adapter exposing single-action primitives built on Browser-Use's `Controller`/`BrowserSession`, wired into the same OpenCode session.
- Verification step after every GUI/browser action, using the best available state source.
- Bounded retry/recovery loop.
- Permission policy for destructive actions.
- Structured logging.
- **Acceptance scenario:** download a report from a real university portal, rename it, move it into a project folder, update the README, with every step verified and a truthful final report of what happened.

**Explicitly out of scope for MVP** (see Roadmap doc for sequencing):
- Second GUI executor (Qwen-UI-Agent, used in grounding-only mode) wired in and benchmarked head-to-head.
- Parallel/multi-session execution.
- Windows/Linux support.
- Automated OSWorld/WebArena-style benchmarking harness.
- Any hosted/multi-user deployment.

## 14. Future Roadmap (pointer)

Full sequencing lives in the Roadmap document. Summary: MVP → Phase 2 adds the second GUI executor + parallel sessions + benchmarking harness → Phase 3 adds Windows/Linux support + hosted option.

## 15. Acceptance Criteria

- [ ] OpenCode, unmodified at the core, drives a task through terminal, filesystem, browser, and GUI steps using only MCP tool calls to the Gateway and Browser Adapter.
- [ ] The acceptance scenario (§13) completes end-to-end at least 8/10 runs without human intervention beyond initial permission grants.
- [ ] At least one deliberately-broken run (e.g., portal UI changed, file already exists) is correctly caught by verification and triggers a recovery attempt rather than a false "success" report.
- [ ] Swapping the GUI executor backend requires only a config change, not a code change in OpenCode-facing contracts.
- [ ] A destructive action (e.g., overwriting an existing file) is blocked by the permission policy in a test run until explicitly confirmed.
- [ ] A full session log for the acceptance scenario is reconstructable and human-readable after the fact.
- [ ] No worker MCP tool used in the MVP is ever invoked in a mode that runs its own independent LLM planning loop (verified by inspecting the actual API/config surface used for each integration, not by inference from documentation).

## 16. Success Metrics

| Metric | MVP target |
|---|---|
| End-to-end task success rate (acceptance scenario, repeated runs) | ≥ 80% |
| False "success" reports (action failed but system claimed success) | 0 tolerated in acceptance testing |
| Median recovery attempts before either success or clean escalation | ≤ 2 |
| Time from `opencode serve` cold start to first tool call ready | < 10s on target hardware |
| Core OpenCode modifications required | 0 (config/MCP-only integration) |

---
**Next file: System Architecture** — component boundaries, the CUA Gateway design, where UI-TARS actually plugs in, how the Browser Adapter avoids the duplicated-planning trap, the verification/recovery engine, and session/state management.
