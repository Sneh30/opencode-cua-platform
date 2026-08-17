# OpenCode Computer-Use Platform — System Architecture

**Status:** Final v2 · **Doc:** 2 of 4 (PRD → **Architecture** → Master Prompt → Roadmap + Engineering Decisions)
**Last updated:** 2026-08-18

---

## 1. Component Architecture

```
                              ┌────────────────────────┐
                              │          User           │
                              └────────────┬─────────────┘
                                           │ objective (NL)
                                           ▼
                              ┌────────────────────────┐
                              │   OpenCode (unmodified)  │
                              │  planning · decomposition │
                              │  tool selection · context │
                              │  final verification       │
                              └──────┬───────┬───────┬────┘
                                     │        │       │
                     MCP tool calls │        │       │ native tools
                     ┌───────────────┘        │        └───────────────┐
                     ▼                        ▼                        ▼
          ┌────────────────────┐   ┌───────────────────┐   ┌────────────────────┐
          │   CUA Gateway (MCP)  │   │ Browser Adapter (MCP)│  │ Terminal / FS (native)│
          │  ─────────────────  │   │  ──────────────────  │  │  opencode built-ins  │
          │  action normalizer   │   │  single-action       │  └────────────────────┘
          │  verification engine │   │  primitives over      │
          │  recovery engine      │   │  Browser-Use's        │
          │  session/state store  │   │  Controller/Session    │
          │  MCP `instructions`   │   │  (Playwright, DOM-first)│
          │  = Master Prompt      │   └─────────┬───────────┘
          └──────────┬───────────┘             │ CDP / DOM state
     model-provider  │                          ▼
     abstraction     │                 ┌───────────────────┐
                     ▼                 │   Real browser      │
          ┌────────────────────┐       └───────────────────┘
          │  GUI executor         │
          │  (UI-TARS-1.5-7B /    │
          │   UI-TARS-7B-DPO,     │
          │   Qwen-UI-Agent       │
          │   grounding mode,     │
          │   Phase 2)            │
          └──────────┬───────────┘
                     │ screenshots in / actions out
                     ▼
          ┌────────────────────┐
          │  OS Adapter (macOS)  │
          │  screencap · AXUI    │
          │  mouse/kb dispatch   │
          └──────────┬───────────┘
                     ▼
              Real desktop / apps
```

**The components you actually build or configure**, matched against the original five-box proposal:

| Original term | This architecture | Built or configured? |
|---|---|---|
| OpenCode (orchestrator) | Same — runs headless (`opencode serve`), zero core modifications | Configured |
| CUA Gateway/Bridge | An MCP server process, driving UI-TARS as a bare grounding model | **Built** |
| UI-TARS | A local MLX inference endpoint + the Gateway's action executor talking to it | Configured (model) + built (executor glue) |
| Qwen | A second, swappable backend behind the same executor interface, used in grounding-only mode | Configured, Phase 2 |
| Browser Use | A thin **Browser Adapter** MCP server driving Browser-Use's `Controller`/`BrowserSession` one action at a time — not its bundled `Agent` | **Built (thin)** + library dependency |

Two components are genuinely new code: the **CUA Gateway** (the real engineering lift — perception/action/verification/recovery for GUI tasks) and the smaller **Browser Adapter** (a single-action wrapper around Browser-Use's lower-level API, needed for the same reason described in §4.1 and §7 below: Browser-Use's own headline integration path is itself a planning agent, not a passive executor, and using it as shipped would silently introduce a second planner under OpenCode). Everything else is model/library integration.

## 2. Service Boundaries

- **OpenCode** never talks to UI-TARS, Qwen-UI-Agent, or Browser-Use's `Agent` directly. It only ever calls MCP tools that execute one bounded action or observation and return. This is what keeps the orchestrator swappable in principle and keeps "zero core modification" true in practice.
- **The CUA Gateway** owns the perception-action-verification loop for GUI tasks. It does not do task planning — it executes one action or one verification check per call, returns the result, and lets OpenCode decide what happens next. Put planning in the Gateway and you get "duplicated reasoning" — the exact failure mode this project explicitly avoids.
- **The Browser Adapter** owns the same kind of loop for browser tasks: one navigate/click/type/extract/scroll action per call, backed by DOM-first state reads. It is a peer to the Gateway, not a component nested inside it, because browser automation and desktop GUI automation have different state sources (DOM vs. accessibility tree/pixels) and different underlying libraries (Playwright vs. `AXUIElement`/`CGEvent`) — merging them would blur a boundary that's cheap to keep separate.
- **The OS Adapter** is the only layer allowed to call macOS-specific APIs (screen capture, `AXUIElement` accessibility tree, `CGEvent` mouse/keyboard dispatch). This isolation is what makes a future Windows/Linux port additive rather than a rewrite.

## 3. OpenCode Integration

OpenCode is run headless via `opencode serve`, configured with MCP server entries pointing at the CUA Gateway and the Browser Adapter. The integration surface is:

- An MCP config block registering both servers.
- **The Master Prompt is delivered primarily through the CUA Gateway's own MCP `instructions` field**, which OpenCode has natively surfaced into session context since v1.17.10 — no plugin code is required for this to work, which makes the "minimal or no modification to OpenCode itself" requirement true by construction rather than by convention.
- An optional, small OpenCode **plugin** (V2 plugin API, JS/TS) that hooks session lifecycle events to mirror OpenCode's own session log into the platform's structured log format, for unified observability. This is now genuinely optional scope, not load-bearing for prompt delivery — treat it as a nice-to-have that can slip without threatening MVP correctness.
- No changes to OpenCode's planner, tool-calling loop, or model provider handling.

## 4. CUA Gateway — Design

This is the component worth designing carefully, and the one place the original spec left a real decision unmade: **how does the Gateway actually talk to UI-TARS?**

### 4.1 The decision

There are two legitimate options:

**Option A — Bare model, Gateway owns the loop.**
Run UI-TARS-1.5-7B or UI-TARS-7B-DPO (the openly-released checkpoints; see PRD §0.4) behind local MLX inference, and have the Gateway itself capture the screenshot, send it + instruction to the model, parse the returned action, and dispatch it via the OS Adapter.

**Option B — Automate UI-TARS-desktop / Agent TARS as a black box.**
Treat the existing UI-TARS-desktop Electron app (which already bundles screenshot capture, action dispatch, and its own MCP tool integration) as the executor, and have the Gateway drive it rather than the raw model.

| | Option A (bare model) | Option B (black-box app) |
|---|---|---|
| Control over the loop (cadence, retry, what counts as an observation) | Full | Limited to what UI-TARS-desktop exposes |
| Risk of duplicated planning/reasoning | Low — UI-TARS is doing grounding, not planning | **Real** — Agent TARS is itself a planning agent; running it under OpenCode risks two planners disagreeing |
| Time to first working demo | Slower (you write the executor loop) | Faster (app already works end-to-end) |
| Fit with "avoid unnecessary multi-agent loops" (explicit non-goal) | Good fit | Poor fit |
| Fit with verification requirement (inspect *your* choice of state source, not the app's) | Good fit — you control what gets checked | Poor fit — you inherit whatever the app decides is "done" |

**Recommendation: Option A for the platform itself.** It's more work up front, but it's the only option that keeps verification and recovery under your control rather than nested inside someone else's agent loop — which is the actual point of this whole project. Option B is worth keeping in your back pocket as a fast way to demo raw GUI-grounding capability while the real executor is being built, but it shouldn't be what ships.

### 4.2 Gateway internals

```
CUA Gateway (single MCP server process)
├── mcp_server.py           # tool registration, request/response marshaling,
│                            # publishes Master Prompt via MCP `instructions`
├── executor/
│   ├── gui_executor.py     # Option A loop: screenshot → model → action → dispatch
│   ├── model_client.py     # provider abstraction (MLX local / remote endpoint)
│   └── action_schema.py    # normalized action types (click, type, drag, key, scroll)
├── verification/
│   ├── verifier.py         # state-source selection + pass/fail/uncertain logic
│   └── state_sources.py    # screenshot diff, AX tree read, app metadata
├── recovery/
│   └── recovery_engine.py  # retry / re-observe / alternate-strategy / escalate
├── session/
│   └── session_store.py    # local persistence (SQLite) for task/session state
└── os_adapter/
    └── macos_adapter.py    # screencap, AXUIElement, CGEvent — the only platform-coupled code
```

The verification and recovery engines are written to be state-source-agnostic so the Browser Adapter (§7) can reuse the same `act_and_verify`/recovery logic rather than reimplementing it — either as a shared library the two MCP servers both import, or by having the Browser Adapter be a second tool namespace inside the same Gateway process. Either is acceptable; pick the shared-library split for MVP (§Roadmap) since it keeps the two servers independently deployable without duplicating logic.

## 5. UI-TARS Integration

- Model: **UI-TARS-1.5-7B** or **UI-TARS-7B-DPO**, served locally via MLX (`mlx-vlm`), using existing `mlx-community` conversions (e.g. `mlx-community/UI-TARS-1.5-7B-4bit`, `mlx-community/UI-TARS-7B-DPO-4bit`) so no local conversion step is required for MVP.
- `model_client.py` implements a narrow interface: `infer(screenshot, instruction, history) -> Action`. This is the seam that makes swapping to a Qwen-UI-Agent grounding backend in Phase 2 a config change, not a rewrite.
- Action schema is normalized to the Gateway's own format immediately on return from the model — nothing downstream of `model_client.py` knows it's talking to UI-TARS specifically.
- UI-TARS-2 (arXiv:2509.02544) is not used directly in MVP: it is a closed-weight report-only checkpoint as of this writing. Its published numbers are a useful upper-bound reference when evaluating UI-TARS-1.5-7B's real-world MVP performance, not a deployment target. If ByteDance releases UI-TARS-2 weights before Phase 2, re-evaluate as a drop-in `model_client.py` swap.

## 6. Qwen-UI-Agent Integration (Phase 2)

- Qwen-UI-Agent (Alibaba Tongyi-MAI, released 2026-07-30, follow-up to MAI-UI) is **not** a pure grounding model — it's a real-world-centric foundation agent that unifies GUI, CLI, and API actions in a single action space and includes its own DeepSearch/multi-turn planning behavior across trajectories exceeding 100 turns. Wired in naively (its own end-to-end task loop, given a high-level objective), it would reproduce exactly the Option-B risk flagged in §4.1 for Agent TARS: a second planner underneath OpenCode.
- Phase 2 integration must therefore use Qwen-UI-Agent in its **grounding/single-step mode** — screenshot + instruction in, one grounded action out — implementing the same `infer()` interface as UI-TARS, not its bundled multi-turn task-completion behavior. This may require using an underlying grounding-focused checkpoint from the same lineage (e.g., a Qwen3-VL-class vision-language model fine-tuned for GUI grounding) rather than the full Qwen-UI-Agent orchestration stack, depending on what's separable at integration time — confirm this against Qwen-UI-Agent's actual released interfaces before committing Phase 2 scope.
- Because grounding quality and cost profiles differ meaningfully between UI-TARS and the Qwen line, Phase 2 should run both behind an A/B config flag on the *same* acceptance-scenario tasks before deciding which is the default — not adopt whichever is fashionable.

## 7. Browser Adapter Integration

**This is a correction from the original design, not a restatement of it.** The original assumption — that Browser-Use "already ships its own MCP server" and can be wrapped with a config block and no new code — undersells what Browser-Use's headline integration path actually does. Browser-Use's Python `Agent` class (and the community/official MCP servers built around it) takes a natural-language `task`, its own LLM (`langchain`-style `BaseChatModel`, its own API key), and runs a full autonomous ReAct-style loop — deciding its own next action, calling `done` when it thinks it's finished — before returning a single result to the caller. That is architecturally identical to the Agent-TARS problem in §4.1: a second, independent planner running underneath OpenCode, invisible to the verification/recovery loop this project is built around.

**What Browser-Use actually gets you for free, and what still needs to be built:**

- Free: a mature, Playwright-backed browser session (`BrowserSession`) with DOM-first structured state extraction, and a `Controller`/`Tools` action registry (`click_element_by_index`, `input_text`, `scroll`, `navigate`, `extract_content`, etc.) that can be invoked directly, without going through the bundled `Agent`.
- Built (thin, but real): a **Browser Adapter** — a small MCP server, structurally the browser-facing sibling of the OS Adapter — that exposes `browser.observe` / `browser.act` / `browser.verify` tools mirroring the Gateway's `gui.*` contract (§9). Internally, `browser.act` resolves a `target_hint` against the current DOM/accessibility snapshot (Browser-Use's own element-indexing already does most of this work) and dispatches exactly one `Controller` action per call; `browser.observe` returns the DOM/accessibility state Browser-Use already extracts, without invoking the LLM-driven `Agent` layer at all.
- The Gateway's `verifier.py`/`recovery_engine.py` logic (§4.2, §12, §13) is reused here rather than reimplemented — the Browser Adapter is a second thin client of shared verification/recovery code, not a parallel implementation of it.

If a browser task genuinely needs pixel-level interaction that Browser-Use's DOM actions can't express (rare — canvas-heavy UIs, certain drag interactions), the orchestrator can fall back to routing that specific subtask through the GUI executor instead. That fallback decision happens at the OpenCode planning layer, same as any other tool-selection choice.

## 8. Tool Protocol

**MCP, exclusively, for orchestrator-facing calls**, under Linux Foundation (AAIF) governance as of December 2025 — joint stewardship across Anthropic, OpenAI, Google, Microsoft, AWS, Cloudflare, and Bloomberg, which meaningfully de-risks "the protocol changes out from under this project" relative to a single-vendor-controlled spec.

Concretely, the Gateway exposes tools such as:

```jsonc
// tool: gui.observe
// returns current screen state via best available source
{
  "name": "gui.observe",
  "input": { "session_id": "string", "prefer_source": "auto|vision|accessibility" },
  "output": {
    "screenshot_ref": "string",
    "accessibility_tree": "object | null",
    "active_app": "string",
    "source_used": "vision|accessibility"
  }
}

// tool: gui.act
// executes one normalized action
{
  "name": "gui.act",
  "input": {
    "session_id": "string",
    "action": { "type": "click|double_click|right_click|drag|type|key|scroll",
                "target_hint": "string",   // natural-language target, resolved by executor
                "params": "object" },
    "expected_postcondition": "string"      // required — feeds the verifier
  },
  "output": {
    "action_id": "string",
    "dispatched": "boolean",
    "raw_result": "object"
  }
}

// tool: gui.verify
// checks whether expected_postcondition holds
{
  "name": "gui.verify",
  "input": { "session_id": "string", "action_id": "string" },
  "output": {
    "verdict": "pass|fail|uncertain",
    "evidence": "object",
    "source_used": "string"
  }
}
```

`gui.act` **requires** `expected_postcondition` as an input, not an afterthought — this is the mechanism that makes verification structurally enforced rather than a prompting convention OpenCode can forget to follow.

The Browser Adapter mirrors this exact shape, so OpenCode sees one consistent contract regardless of which worker it's calling:

```jsonc
// tool: browser.observe / browser.act / browser.verify
// same input/output shape as gui.*, with:
//   - accessibility_tree replaced/augmented by a DOM snapshot
//   - action.type drawn from: navigate|click|type|extract|scroll|select|key
//   - action dispatch implemented via Browser-Use's Controller registry
//     against the current BrowserSession — never via Browser-Use's Agent
```

## 9. API Contracts

- All MCP tool schemas above are versioned (`gui.v1.act`, `browser.v1.act`, etc.) so the Gateway and Browser Adapter can evolve without breaking an in-flight OpenCode session.
- A secondary OpenAI-compatible `/v1/chat/completions`-shaped HTTP surface may be exposed by the Gateway for non-MCP clients (e.g., a benchmarking harness that isn't running through OpenCode at all) — explicitly a secondary interface, generated from the same internal action schema, not a parallel implementation to maintain by hand.

## 10. Session / State Management

- **Session store:** SQLite, local, one file per platform install (not per task — sessions are rows). Chosen over a heavier datastore because this is a single-user, local-first tool; a heavier store is future work if/when multi-user hosting is ever in scope.
- **Session record:** objective, plan (subtask list with status), permission scope, action log references, artifacts produced, current status (`planning|executing|recovering|done|failed|escalated`).
- **Survives restart:** the Gateway process can crash or be restarted mid-task; on restart it reloads the session record and OpenCode can resume from the last verified checkpoint rather than replaying from scratch. The Browser Adapter shares the same session store (or a clearly-partitioned table within it) so a session that mixes GUI and browser subtasks reconstructs as a single coherent timeline.

## 11. Event Model

A single event stream (also written to the structured log, §14) with event types:

`plan.created`, `subtask.assigned`, `action.dispatched`, `observation.captured`, `verification.result`, `recovery.attempted`, `recovery.exhausted`, `permission.requested`, `permission.granted|denied`, `session.completed`, `session.failed`.

Every event carries `session_id`, `task_id` (subtask), `worker` (`gateway|browser_adapter`), and `timestamp`. This is what makes a session fully reconstructable after the fact (PRD §12/§15).

## 12. Verification Engine

Pseudocode for the core loop enforced by the Gateway (and reused by the Browser Adapter), not left to prompt discipline:

```
def act_and_verify(session, action, expected_postcondition):
    result = execute(action)                       # OS Adapter or Browser Adapter call
    observation = observe(session, prefer=best_available_source(action))
    verdict = verify(observation, expected_postcondition)
    log_event(...)
    if verdict == "pass":
        return Success(result, observation)
    else:
        return Verified_Failure(result, observation, verdict)  # NEVER silently "success"
```

`best_available_source()` picks, in order: accessibility tree (if the target app exposes usable `AXUIElement` data) → DOM (browser tasks, via the Browser Adapter's DOM snapshot) → vision/screenshot diff (fallback for everything else). This ordering is a real engineering task — many macOS apps expose incomplete or misleading accessibility trees, so the selection logic needs a per-app capability probe, not a blind preference, and should degrade gracefully to vision when the AX tree is unreliable.

## 13. Recovery Engine

```
def run_subtask_with_recovery(session, subtask, budget):
    attempts = 0
    while attempts < budget.max_attempts and elapsed() < budget.max_seconds:
        result = act_and_verify(session, subtask.next_action(), subtask.expected_postcondition)
        if result.verdict == "pass":
            return Success(result)
        attempts += 1
        strategy = choose_recovery_strategy(attempts, result)  # retry / re-observe / alternate executor
        apply_strategy(strategy, session, subtask)
    return Escalate(session, subtask, reason="recovery budget exhausted")
```

Recovery strategy selection is deliberately simple for MVP (retry once, then try the alternate state source, then escalate) — this is explicitly flagged in the Roadmap doc as a place to get smarter later (e.g., alternate executor backend, not just alternate observation source) once there's real failure data to learn from.

## 14. Model / Provider Abstraction

- `model_client.py` (GUI executor) is the primary seam. Neither OpenCode's model choice nor the Gateway's GUI model choice needs to match — genuinely provider-agnostic.
- Local inference runs through **MLX** (`mlx-lm` / `mlx-vlm`) directly, or through Ollama (0.19+, which now runs its own MLX-backed engine on Apple Silicon with 32GB+ unified memory — falling back to its previous llama.cpp/Metal path below that threshold, silently, so verify which engine is actually active via the server log rather than assuming). See Roadmap/Engineering Decisions for the full reasoning, including why `vllm-metal` — which *does* now support vision-language models on Apple Silicon as of April 2026, contradicting an earlier draft of this document — still isn't the right choice for a single-session local MVP.
- A single `.env`/config file names: OpenCode's model, the GUI executor's model + endpoint, and the Browser Adapter's model + endpoint (only relevant if it ever needs an LLM for target-hint resolution beyond Browser-Use's built-in element indexing). Independent knobs, no shared assumption that they're the same provider.

## 15. Local vs Remote Deployment

- **MVP: fully local.** Gateway, OS Adapter, Browser Adapter, and (ideally) the GUI executor model all run on the same Mac as the target desktop, because GUI action execution is fundamentally tied to a real display and input focus — there is no meaningful way to "run the GUI executor remotely" without also streaming/tunneling the desktop itself (VNC-style), which is real added complexity, not a config flag.
- **Remote model, local execution** is a supported middle ground: the *model inference* (UI-TARS/Qwen) can call out to a remote endpoint while the OS Adapter and actual clicking/typing stay local — this is just a `model_client.py` endpoint URL, not an architecture change.
- **Fully remote/hosted operation** (Gateway + target desktop both remote) is explicitly out of scope until there's a concrete need for it — see Roadmap Phase 3.

## 16. Security Architecture

- Permission policy (PRD §10) is enforced **inside the Gateway and the Browser Adapter**, at the `gui.act`/`browser.act` boundary — not trusted to the model's judgment and not left to OpenCode's prompt. Even if the model or the orchestrator "decides" to click delete or submit a form, the relevant server checks the action class against policy before dispatching.
- Screenshots, AX tree data, and DOM snapshots are treated as potentially sensitive (they can contain credentials, personal data on screen or on the page). Local-only processing is the default; sending them to a remote model endpoint requires an explicit opt-in in config, surfaced clearly at setup.
- Credential injection (typing a password, submitting a login form) is a distinct, logged action class that can be routed through a dedicated secrets step rather than having the vision model "read" a password off screen, or having a form-fill action populate a password field directly from a vision- or DOM-derived value where avoidable.
- Kill switch: a dedicated MCP tool (`session.halt`) that immediately stops dispatch of further actions for a session, checked before every `gui.act`/`browser.act` call.

## 17. Logging / Telemetry

- Structured JSON-lines log per session, one file under a local log directory, containing every event from §11, tagged by which worker (Gateway or Browser Adapter) produced it.
- A lightweight local viewer (even a static HTML render generated from the log) for human debugging — not a hosted dashboard for MVP.
- Metrics aggregation (success rate, recovery attempts, timing) computed from the log files by a small script, not a separate telemetry service, until there's a real need for one.

## 18. Testing Strategy

| Level | What's tested | How |
|---|---|---|
| Unit | Action schema normalization, verifier state-source selection logic, recovery strategy selection, Browser Adapter action-dispatch mapping onto Browser-Use's `Controller` registry | Standard test framework, no live desktop needed |
| Integration | Gateway ↔ OS Adapter (real clicks against known test apps), Browser Adapter ↔ real browser (real navigation, confirming no `Agent`/LLM call occurs in the dispatch path) | Runs against a controlled local macOS test environment |
| End-to-end | Full acceptance scenario (PRD §13) through OpenCode | Manual + scripted repeated runs, tracked for the ≥80% success target |
| Regression | Re-run acceptance scenario after any Gateway, Browser Adapter, or model swap | CI-style local script, not cloud CI initially (needs a real display) |

## 19. Performance / Scaling Considerations

- **Single active session is the honest MVP target.** True parallel GUI workers on one physical Mac will fight over mouse/keyboard focus and window state — this isn't a config problem, it's a hard constraint of controlling a real desktop. Parallelism (FR-11, Phase 2) requires either isolated virtual displays/VMs or multiple physical/cloud machines, which is a meaningfully bigger lift than the rest of this platform and should be scoped as its own mini-project when it comes up, not assumed to fall out of "just run two sessions."
- Browser tasks parallelize far more easily (headless Chromium instances via multiple `BrowserSession`s are cheap and isolated) — so "parallel workers" is realistic for browser-only tasks well before it's realistic for GUI tasks. Worth sequencing Phase 2 parallelism as browser-first for exactly this reason.
- Vision-model inference latency (screenshot → action) is the dominant per-step cost; batching or speculative actions aren't worth the complexity until real timing data from the MVP says otherwise.

---
**Next file: Master Prompt / System Prompt** — the actual text that defines OpenCode's supervisor role, tool-selection rules, verification discipline, and recovery behavior, ready to drop into the Gateway's MCP `instructions` field.
