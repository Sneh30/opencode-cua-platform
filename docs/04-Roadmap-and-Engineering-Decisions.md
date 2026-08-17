# OpenCode Computer-Use Platform — Implementation Roadmap & Engineering Decisions

**Status:** Final v2 · **Doc:** 4 of 4 (PRD → Architecture → Master Prompt → **Roadmap + Engineering Decisions**)
**Last updated:** 2026-08-18

---

# Part A — Implementation Roadmap

## A.1 Sequencing Overview

| Phase | Adds | Why it's sequenced here |
|---|---|---|
| **MVP** | Single-session GUI + browser + terminal orchestration, one GUI executor (UI-TARS-1.5-7B / UI-TARS-7B-DPO), a thin Browser Adapter over Browser-Use's action registry, verification/recovery enforced structurally, permission policy, structured logging | Everything needed to make the acceptance scenario real and trustworthy. Nothing else. |
| **Phase 2** | Second GUI executor (Qwen-UI-Agent, grounding mode only), browser-first parallel sessions, automated benchmarking harness | Only makes sense once you have real failure data from the MVP to compare against — benchmarking a system that doesn't work reliably yet just measures noise. |
| **Phase 3** | Windows/Linux OS Adapter, optional hosted/remote deployment mode | Deferred because the OS Adapter boundary was designed to make this additive, not because it's unimportant — do it once the core loop is proven on one platform. |

This is a deliberately narrow MVP. The temptation with a multi-component architecture like this is to build a thin slice of everything at once; the plan below builds the CUA Gateway to completion first, then the Browser Adapter, because those are the only two components that are actually new code.

## A.2 MVP — Milestones

Solo-developer time estimates assume part-time-to-full-time focus and are meant as sanity checks on scope, not commitments.

| Milestone | Deliverable | Rough estimate |
|---|---|---|
| M0 — Skeleton | `opencode serve` running locally; MCP config registering a Gateway stub that returns hardcoded responses and publishes a placeholder `instructions` string; confirms the wiring (including that OpenCode actually surfaces MCP `instructions` into session context on your installed version) works before any real logic exists | 2–3 days |
| M1 — OS Adapter | Screenshot capture, `AXUIElement` read for at least 2–3 real test apps, `CGEvent` mouse/keyboard dispatch, all behind the adapter interface | 1–1.5 weeks |
| M2 — GUI executor loop | MLX-served UI-TARS-1.5-7B (via `mlx-community/UI-TARS-1.5-7B-4bit` or similar), `model_client.py` implementing `infer()`, action schema normalization, first successful "click a specific button" end-to-end test | 1.5–2 weeks |
| M3 — Verification engine | State-source selection (AX tree → vision fallback), `gui.verify` tool, pass/fail/uncertain logic against real `expected_postcondition` strings; write this as a shared library the Browser Adapter will also import in M5 | 1 week |
| M4 — Recovery engine | Bounded retry/re-observe/escalate loop, recovery budget enforcement, structured event logging for every attempt | 3–5 days |
| M5 — Browser Adapter | New MCP server exposing `browser.observe`/`browser.act`/`browser.verify`, built directly on Browser-Use's `Controller`/`Tools` registry and `BrowserSession` (Playwright-backed DOM/accessibility state) — explicitly bypassing Browser-Use's bundled `Agent`. Reuses the verifier/recovery library from M3/M4. One end-to-end browser-only test task, plus a specific regression test confirming no LLM call happens inside the dispatch path. | 1–1.5 weeks (larger than the original "config only" estimate — this is real integration code, just smaller than the GUI executor loop) |
| M6 — Permission policy | Policy config, destructive-action-class blocking at both the `gui.act` and `browser.act` boundaries, kill switch tool | 4–6 days |
| M7 — OpenCode integration | Confirm the Gateway's MCP `instructions` field actually lands the Master Prompt (doc 3) in OpenCode session context; add the optional log-mirroring plugin if time allows; adversarial prompt tests from Master Prompt §5 | 2–4 days |
| M8 — Acceptance scenario | The portal-download-rename-move-README task, run end-to-end repeatedly, tracked against the ≥80% success target | 1–1.5 weeks (mostly debugging real-world flakiness, not new code) |

**Total MVP estimate: roughly 8–11 weeks solo, part-time-adjusted** (revised up modestly from an earlier estimate to account for the Browser Adapter being real integration work, not a config-only task). M8 is intentionally the longest tail item — real GUI/portal automation is where theoretical architecture meets actual UI quirks, and that's genuinely where most of the time goes on a project like this.

## A.3 Phase 2 — Milestones

- **Second GUI executor:** wire a Qwen-UI-Agent grounding-mode backend behind the same `infer()` interface (Architecture §6). Confirm at integration time exactly which released interface exposes single-step grounding without the full multi-turn/DeepSearch agent loop — this needs to be verified against Qwen-UI-Agent's actual public release artifacts when Phase 2 starts, since the surface may have changed since this doc was written. Run the MVP acceptance scenario plus 4–5 additional real tasks against both backends, config-flagged, and compare success rate / latency / cost before picking a default.
- **Browser-first parallelism:** multiple concurrent `BrowserSession`s (cheap, isolated per Architecture §19) before attempting any GUI-level parallelism.
- **Benchmarking harness:** automated runner against a batch of tasks with pass/fail scoring (§A.5 below).
- **Recovery strategy tuning:** revisit the recovery engine's strategy selection (Architecture §13) using real failure logs accumulated during MVP + Phase 2 usage, rather than the initial simple retry/re-observe/escalate sequence.

## A.4 Phase 3 — Milestones

- **Windows/Linux OS Adapter:** implement the same adapter interface using platform-native equivalents (UI Automation on Windows, AT-SPI on Linux) — additive because nothing above the adapter boundary changes.
- **Optional hosted mode:** only if a concrete need emerges (explicitly a non-goal for MVP). Requires solving the "GUI execution needs a real display" constraint (Architecture §15/§19) via VM-hosted virtual displays — scope this as its own design pass when it comes up, not a checkbox.

## A.5 Repository Structure

```
opencode-cua-platform/
├── README.md
├── docs/
│   ├── 01-PRD.md
│   ├── 02-Architecture.md
│   ├── 03-Master-Prompt.md
│   └── 04-Roadmap-and-Engineering-Decisions.md
├── gateway/                      # the CUA Gateway
│   ├── pyproject.toml
│   ├── mcp_server.py              # publishes Master Prompt via MCP `instructions`
│   ├── executor/
│   │   ├── gui_executor.py
│   │   ├── model_client.py       # MLX local / remote endpoint
│   │   └── action_schema.py
│   ├── os_adapter/
│   │   ├── base.py                # abstract interface
│   │   └── macos_adapter.py       # AXUIElement / CGEvent, pyobjc-based
│   └── policy/
│       └── permission_policy.yaml
├── browser_adapter/                # the Browser Adapter — sibling MCP server, thin but real
│   ├── pyproject.toml
│   ├── mcp_server.py
│   ├── action_dispatch.py          # maps browser.act -> Browser-Use Controller registry calls
│   └── session_wrapper.py          # thin wrapper over Browser-Use's BrowserSession, no Agent import
├── shared/                          # imported by both gateway/ and browser_adapter/
│   ├── verification/
│   │   ├── verifier.py
│   │   └── state_sources.py
│   ├── recovery/
│   │   └── recovery_engine.py
│   └── session/
│       └── session_store.py        # SQLite
├── opencode-plugin/
│   └── log-mirror/                 # optional — mirrors OpenCode's session log; not load-bearing
├── config/
│   ├── mcp.json                   # OpenCode MCP server registration (Gateway + Browser Adapter)
│   └── platform.env                # model endpoints, provider selection
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│       └── acceptance_scenario/
├── benchmarks/                    # Phase 2
│   └── runner/
└── logs/                          # local structured session logs, gitignored
```

Moving verification, recovery, and session storage into a `shared/` package (rather than nesting them inside `gateway/` as in an earlier draft) is a direct consequence of the Browser Adapter needing the same logic — see Engineering Decision #5.

## A.6 Interfaces to Implement First

In dependency order, since each of these blocks the next:

1. `os_adapter.base` — the abstract interface, before any macOS-specific code, so the boundary is real from day one rather than retrofitted.
2. `shared.action_schema` — the normalized action/observation/verification data shapes (the JSON contracts from Architecture §8), since everything else, including the Browser Adapter, speaks in these types.
3. `model_client.infer()` — the single-method seam that makes the GUI executor swappable.
4. `gateway.mcp_server` tool registration (`gui.observe`, `gui.act`, `gui.verify`) — the actual OpenCode-facing contract for desktop tasks.
5. `shared.session_store` — needed before recovery/logging can persist anything meaningful.
6. `browser_adapter.mcp_server` tool registration (`browser.observe`, `browser.act`, `browser.verify`) — built once `shared.*` exists, so it's a thin client from the start rather than something to refactor into one later.

Build in this order even though it delays a "visible" end-to-end demo — building the demo path first tends to produce an implicit, undocumented interface instead of the explicit one Architecture §8 specifies.

## A.7 Recommended Technologies

| Layer | Recommendation | Why |
|---|---|---|
| Gateway / Browser Adapter language | Python | Matches Browser-Use's native ecosystem, has a mature MCP server SDK, and `pyobjc` gives direct `AXUIElement`/`CGEvent` access without a separate Swift helper binary for MVP. |
| GUI executor model | UI-TARS-1.5-7B or UI-TARS-7B-DPO (MVP) → a Qwen-UI-Agent grounding-mode checkpoint or UI-TARS-72B (Phase 2) | The 1.5-7B/7B-DPO checkpoints are the openly-released, MLX-converted models actually available today; UI-TARS-2's headline numbers are a useful reference point but its weights aren't public as of this writing. 7B is the realistic laptop-class starting point. |
| Local inference | **MLX** (`mlx-lm` / `mlx-vlm`), or Ollama 0.19+ on Macs with 32GB+ unified memory (its MLX-backed engine, promoted from preview to default as of Ollama v0.30, May 2026) | `vllm-metal` gained native vision-language support in April 2026 (image-only, on its paged backend) — an earlier draft of this doc claimed it was text-only and disqualifying, which is now out of date. The reason to still prefer direct MLX (or Ollama's MLX engine) for MVP isn't a support gap; it's that `vllm-metal`'s serving-oriented features (continuous batching, paged KV cache across concurrent requests) solve a problem this single-session, single-user MVP doesn't have. Revisit if/when Phase 2 parallel sessions make concurrent inference serving a real requirement. |
| Browser automation | Browser-Use's `Controller`/`Tools` action registry and `BrowserSession` (Python, Playwright-backed) — **not** Browser-Use's bundled `Agent` | The registry and session already do DOM-first structured observation and action dispatch; using them directly (instead of through the `Agent`, which requires its own LLM and runs its own planning loop) keeps OpenCode the only planner in the system. See Engineering Decision #5. |
| Orchestrator | OpenCode, headless (`opencode serve`) | As specified; MCP-native client, plugin system, zero core modification required. Confirmed current release v1.17.13 (2026-07-01). |
| Session store | SQLite | Single-user, local-first — no reason to reach for a server-based datastore yet. |
| Tool protocol | MCP | Both UI-TARS-desktop/Agent TARS and Browser-Use are already MCP-friendly at some layer, and MCP itself is now under multi-vendor (Linux Foundation AAIF) governance rather than single-vendor control, which reduces the risk of building on it. |
| Structured logging | JSON-lines, local files | No hosted telemetry service needed for a single-user local tool. |
| Testing | `pytest` (unit/integration), scripted e2e runner | Standard, low-ceremony choice appropriate to project size. |

## A.8 Local Development Setup

1. Install OpenCode (`v1.17.13+`) and confirm `opencode serve --port 4096` starts cleanly.
2. Install `mlx-lm`/`mlx-vlm` and pull `mlx-community/UI-TARS-1.5-7B-4bit` (or `mlx-community/UI-TARS-7B-DPO-4bit`) from Hugging Face; confirm it serves locally and responds to a basic vision prompt via `python -m mlx_vlm.generate`.
3. `pip install browser-use` (with Playwright's Chromium install step). **Do not** instantiate `browser_use.Agent` anywhere in the Gateway/Browser Adapter codebase — the Browser Adapter should only ever import `Controller`, `Tools`, and `BrowserSession`/`Browser`. Add a lint rule or `grep`-based CI check for `from browser_use import Agent` outside of test fixtures that explicitly test the exclusion.
4. Grant Accessibility and Screen Recording permissions to your terminal/Python process in macOS System Settings — this is a manual, one-time step the Gateway cannot automate around, and it's worth documenting prominently since it's the single most common "why isn't this working" issue for new contributors.
5. Register both MCP servers (Gateway + Browser Adapter) in `config/mcp.json`, point OpenCode at it, and confirm `opencode` sees both tool sets listed and that the Gateway's `instructions` text actually appears in a new session's context (check via OpenCode's session/context inspection, not just assumption).
6. If targeting Ollama instead of raw `mlx-lm`/`mlx-vlm`, confirm you're on 0.19+ with 32GB+ unified memory, and check the `ollama serve` log for `using mlx backend` — below that memory threshold, or on an older version, Ollama silently falls back to its slower llama.cpp/Metal path with no error.
7. Run M0 from §A.2 as your "hello world" — a hardcoded Gateway response and a hardcoded `instructions` string reaching OpenCode end-to-end — before writing any real executor logic.

## A.9 Testing Methodology

Builds on Architecture §18. In practice, for a solo/small-team project:

- **Unit tests** run on every change, no display required — this should be your fast inner loop.
- **Integration tests** require a real macOS session with the target test apps installed (Calculator, TextEdit, Finder are good, stable, low-risk targets for early adapter testing before pointing at anything real) and, for the Browser Adapter, a real (or consistently mocked) target page.
- **E2E acceptance tests** are slower and flakier by nature (real portals, real network conditions) — run them less frequently (e.g., before each milestone sign-off) rather than on every commit, and track success rate as a rolling metric rather than expecting 100% on every run.
- **Master Prompt adversarial tests** (Master Prompt §5), including the second-planner smoke test, should be part of the same e2e suite, not a separate, easily-forgotten checklist.

## A.10 Benchmarking Methodology & OSWorld/Browser-Agent Evaluation

This is explicitly Phase 2 scope, but worth planning for early so the logging/event model doesn't need retrofitting later.

- **Relevant current benchmarks** to evaluate against once the harness exists: **OSWorld 2.0** and **OSWorld-Verified** for computer-use scoring, **ScreenSpot-Pro** for GUI grounding accuracy specifically, and **WebArena** / **Online-Mind2Web** for browser-agent tasks. UI-TARS-2 and Qwen-UI-Agent both report against this same benchmark family (UI-TARS-2: 47.5 OSWorld, 88.2 Online-Mind2Web, 50.6 WindowsAgentArena, 73.3 AndroidWorld; Qwen-UI-Agent: 79.5 OSWorld-Verified, 81.5 ScreenSpot-Pro, 73.6 WebArena), which makes your own numbers directly comparable to published results rather than only meaningful in isolation — keep in mind these are numbers for the full, closed-weight/bundled-agent versions of each model, not necessarily the openly-released grounding-mode checkpoints this platform actually runs, so treat them as a ceiling reference, not a same-conditions comparison.
- **Harness design:** a batch runner that feeds a task list (objective + expected end-state check) through the same `act_and_verify` loop the platform already uses in production (Architecture §12) — the benchmarking harness should be a thin wrapper around real platform code, not a separate evaluation-only code path that could silently drift from what actually ships.
- **Scoring:** reuse the platform's own verification verdicts as ground truth for pass/fail wherever the benchmark's expected end-state matches what your verifier already checks; only build custom benchmark-specific scoring where the standard suites require checks your platform doesn't already produce.
- **What to actually track over time:** success rate by task category (GUI vs browser), success rate by executor backend (UI-TARS vs Qwen line, once both exist), and recovery-attempts-per-success as a proxy for how much of your "success" is first-try versus recovery-rescued — a system that only succeeds after heavy recovery is a different reliability story than one that succeeds cleanly, even at the same headline success rate.

---

# Part B — Engineering Decisions

## How to use this document

Each entry is a decision actually made somewhere in docs 1–3, pulled out so the reasoning is auditable in one place instead of buried in prose. If a decision starts looking wrong once real usage data exists, this is the doc to revise — and the rest of the doc set was written to make that revision as localized as possible.

**Quick-reference table:**

| # | Decision | Now or later |
|---|---|---|
| 1 | MCP as the sole orchestrator-facing tool protocol | Now |
| 2 | Single GUI executor (UI-TARS-1.5-7B / UI-TARS-7B-DPO) for MVP, not two in parallel | Now (second one is Phase 2) |
| 3 | Bare model + Gateway-owned loop, not UI-TARS-desktop as a black box | Now |
| 4 | MLX for local inference, not vLLM-based serving | Now |
| 5 | Browser Adapter built on Browser-Use's `Controller`/`BrowserSession`, not its bundled `Agent` | Now |
| 6 | Verification enforced structurally, not just prompted | Now |
| 7 | Simple bounded recovery strategy for MVP | Now (smarter version is Phase 2) |
| 8 | SQLite for session state | Now |
| 9 | Single active session, no GUI-level parallelism in MVP | Now (Phase 2 is browser-first parallel) |
| 10 | Permission policy enforced in Gateway/Browser Adapter, not left to the model/prompt | Now |
| 11 | OS Adapter as the sole platform-coupling boundary | Now |
| 12 | Local-first processing as the default | Now |
| 13 | Local structured logs, no hosted telemetry | Now |
| 14 | Python for the Gateway and Browser Adapter | Now |
| 15 | Prompt + schema redundancy for reliability-critical behavior | Now |
| 16 | Qwen-UI-Agent (Phase 2) used in grounding-only mode, never its full agent stack | Later, decided now |

---

### 1. MCP as the sole orchestrator-facing tool protocol

**Why needed:** OpenCode needs a standard way to call the Gateway and Browser Adapter; something has to define that contract.
**Alternatives considered:** A bespoke OpenAI-compatible completions-shaped protocol; a custom REST/RPC protocol.
**Trade-offs:** MCP wins because OpenCode is already an MCP client, and UI-TARS-desktop/Agent TARS and Browser-Use are each MCP-friendly at some layer — building a second protocol would mean maintaining a translation layer for no added capability. As of December 2025, MCP's governance moved to the Linux Foundation's Agentic AI Foundation, jointly stewarded by Anthropic, OpenAI, Google, Microsoft, AWS, Cloudflare, and Bloomberg — a materially different, lower-risk profile than a single-vendor protocol. The OpenAI-compatible surface is kept, but demoted to a secondary interface (Architecture §9) for non-MCP clients like a future benchmarking harness.
**Complexity:** Low — this is a complexity *reduction* relative to a from-scratch protocol.
**Failure modes:** MCP tooling itself is still a moving target — OpenCode shipped MCP resource support, the `instructions`-into-context behavior, and a `cwd` option for local servers all within the same few months of its v1.17.x line. A protocol version bump could require Gateway/Browser Adapter updates. Mitigated by explicit tool versioning (Architecture §9).
**Revisit if:** MCP stops being the de facto standard OpenCode and its ecosystem converge on — unlikely near-term given the multi-vendor governance shift, but worth a periodic sanity check as the ecosystem moves fast.

### 2. Single GUI executor for MVP

**Why needed:** The original brief wanted UI-TARS and Qwen usable interchangeably from the start.
**Alternatives considered:** Build both integrations in parallel from day one; build a generic "any VLM" adapter with no named default.
**Trade-offs:** Building two integrations before either is proven means debugging two unknowns at once when something fails, and no baseline to know which one caused a regression. A single default with a clean swap interface (Architecture §5/§6) gets you the interchangeability goal without the simultaneous-unknowns cost.
**Complexity:** Medium — the swap interface itself (`model_client.infer()`) has to be designed well up front even though only one backend exists initially; this is the one place "build for two, ship one" is worth the extra care.
**Failure modes:** If UI-TARS-1.5-7B turns out to have a systematic weakness a Qwen-UI-Agent grounding checkpoint doesn't (or vice versa), MVP acceptance testing won't catch it until Phase 2's head-to-head comparison. Acceptable risk given both lineages are credible, actively-developed options.
**Revisit if:** Early MVP testing surfaces a UI-TARS-specific failure pattern severe enough that waiting for Phase 2 isn't acceptable — in that case, pull the Qwen integration forward.

### 3. Bare model + Gateway-owned loop, not UI-TARS-desktop as a black box

**Why needed:** The original brief left this genuinely open — "expose UI-TARS to OpenCode as a computer-use capability" doesn't specify whether that means the model or the app.
**Alternatives considered:** Automate/embed UI-TARS-desktop or Agent TARS directly, since both already ship working end-to-end GUI agent loops with MCP integration.
**Trade-offs:** The black-box option is faster to a demo but reintroduces a second planner underneath OpenCode (Agent TARS does its own reasoning, not just grounding), which directly conflicts with the explicit non-goal of "unnecessary multi-agent loops and duplicated reasoning." The bare-model option costs more upfront engineering (you own the perception-action loop) but keeps verification and recovery under your control, which is the actual point of the platform.
**Complexity:** High relative to the alternative — this is one of the two biggest engineering lifts in the MVP (Roadmap M1–M4), alongside the Browser Adapter (Decision #5).
**Failure modes:** Writing your own action-dispatch loop means you own bugs the UI-TARS-desktop team has already fixed in theirs. Mitigate by reading their `computer-use` package source as a reference even though you're not depending on it directly.
**Revisit if:** MVP timeline pressure becomes severe enough that a black-box demo is worth the architectural compromise for a specific presentation/deadline — treat that as an explicitly-labeled prototype branch, not a quiet substitution for the real Gateway.

### 4. MLX for local inference, not vLLM-based serving

**Why needed:** Something has to serve the UI-TARS weights locally on Apple Silicon.
**Alternatives considered:** `vllm-metal` (vLLM's Apple Silicon backend); llama.cpp directly; Ollama.
**Trade-offs (corrected from an earlier draft):** An earlier version of this decision disqualified `vllm-metal` outright on the grounds that it was text-only with no vision-model support. That was true when written but is no longer accurate — `vllm-metal` added native (image-only) multimodal support in April 2026, running on its paged attention backend, and in fact uses MLX as its own primary compute backend under the hood. The real reason to prefer direct `mlx-lm`/`mlx-vlm` (or Ollama's MLX-backed engine) for this MVP isn't a capability gap; it's that `vllm-metal`'s value proposition — continuous batching and paged KV cache across many concurrent requests — solves a concurrency problem a single-session, single-user local tool doesn't have. Standing it up would be extra operational surface (a second serving layer, its own supported-models list to track) for no MVP-relevant benefit. `mlx-vlm` already has ready-made conversions for the exact UI-TARS checkpoints this project targets.
**Complexity:** Low — this is a config/dependency choice, not a design choice.
**Failure modes:** MLX's vision-model ecosystem, while real and actively used by the `mlx-community` UI-TARS conversions specifically, is younger and smaller than the text-model ecosystem — a future checkpoint you want might not have a ready MLX conversion the day you need it, requiring you to convert one yourself. Separately, if using Ollama instead of raw `mlx-vlm`, its MLX-backed engine requires 32GB+ unified memory and silently falls back to a slower path below that — a real footgun for anyone following this doc on a lower-memory Mac.
**Revisit if:** Phase 2 parallel sessions (browser-first, but eventually possibly GUI-adjacent) create genuine concurrent-inference demand — that's the point where `vllm-metal`'s serving features start earning their complexity. Also revisit on a move off Apple Silicon (Phase 3 Windows/Linux support), where `vllm-metal` stops being relevant and standard CUDA-backend vLLM becomes a legitimate option on Linux+NVIDIA hardware.

### 5. Browser Adapter built on Browser-Use's `Controller`/`BrowserSession`, not its bundled `Agent`

**Why needed:** Something has to drive browser tasks, and it needs to do so without introducing a second planner underneath OpenCode.
**Alternatives considered:** Wrap Browser-Use's headline `Agent` class (or one of its published MCP server wrappers) directly, treating it as a zero-code, config-only integration, as an earlier draft of this project assumed; route all browser interaction through the GUI executor instead; build a custom Playwright wrapper from scratch with no dependency on Browser-Use at all.
**Trade-offs:** This decision was revised from an earlier version of the doc set that treated Browser-Use as "wrapped via its own MCP server, not rebuilt" at near-zero engineering cost. That undersold what Browser-Use's actual headline integration does: its `Agent` class (and the MCP servers built around it, including the officially-referenced one) takes a natural-language task, uses its own LLM, and runs a complete autonomous ReAct loop before returning one final result — architecturally identical to the Agent-TARS problem this project explicitly designed around in Decision #3. Using it as shipped would mean OpenCode delegates real planning to Browser-Use's `Agent` for every browser subtask, silently violating the project's core "single planner" principle. The corrected design keeps what's genuinely valuable — Browser-Use's mature, DOM-first `BrowserSession` and its `Controller`/`Tools` single-action registry (`click_element_by_index`, `input_text`, `navigate`, `extract_content`, `scroll`, etc.) — and wraps *those* directly in a small new MCP server (the Browser Adapter), calling one action per `browser.act`, exactly mirroring how the Gateway drives UI-TARS. This is more work than "a config block," but far less work than a from-scratch browser automation stack, since Playwright orchestration and DOM/accessibility extraction are still fully reused.
**Complexity:** Low-to-Medium — smaller than the GUI executor loop (Decision #3), because the hard problems (browser control, DOM state extraction, element resolution) are already solved by Browser-Use's lower-level API; what's left to build is the thin MCP wrapper, the `target_hint`-to-element resolution glue, and reuse of the shared verifier/recovery library.
**Failure modes:** Browser-Use's `Controller` action registry is documented and used as an LLM tool surface by design — its element-indexing and action semantics are stable enough to call directly, but this integration point is less battle-tested *as a standalone API* than the fully-wrapped `Agent` path is, since most Browser-Use users go through the `Agent`. Budget real debugging time in Roadmap M5 for this, and keep the regression test that confirms no LLM call ever occurs in the dispatch path — that test is the tripwire for a future contributor accidentally reaching for the easier-looking `Agent` import.
**Revisit if:** A specific target site's interaction pattern turns out to be a poor fit for direct `Controller` calls often enough that the fallback to the GUI executor (Architecture §7) becomes the common case rather than the exception — that would be a signal to invest further here, not to fall back to wrapping the `Agent`.

### 6. Verification enforced structurally, not just prompted

**Why needed:** The system must not assume success just because an action executed — this needs a mechanism, not just an instruction.
**Alternatives considered:** Rely entirely on the Master Prompt to instruct OpenCode to always verify; make verification a suggested-but-optional tool call.
**Trade-offs:** Prompt-only discipline degrades under context pressure in long sessions — a well-known failure mode for any instruction-following system asked to hold a rule across many tool calls. Making `expected_postcondition` a required schema field on `gui.act`/`browser.act` (Architecture §8) means the Gateway/Browser Adapter can reject a malformed call structurally, independent of whether the prompt "worked" that turn.
**Complexity:** Low to add, given it's just a required field — the real complexity is in the verifier logic that consumes it (state-source selection, Architecture §12), which was going to be needed regardless.
**Failure modes:** A required field can be satisfied with a lazy, unfalsifiable postcondition ("the action worked") that technically passes schema validation but provides no real verification value. This is a quality problem the schema alone can't solve — it's why the Master Prompt explicitly coaches toward checkable postconditions, and it's a good target for the adversarial test suite (Master Prompt §5) to catch.
**Revisit if:** Real usage shows OpenCode consistently gaming the postcondition field with vacuous strings — at that point, consider a stricter postcondition format (e.g., structured predicates instead of free text) rather than just better prompting.

### 7. Simple bounded recovery strategy for MVP

**Why needed:** Failed actions need a defined next step, not silent failure or infinite retry.
**Alternatives considered:** A learned/adaptive recovery policy from day one; unlimited retries with exponential backoff and no escalation.
**Trade-offs:** A learned policy needs failure data that doesn't exist yet at MVP time — building it now means guessing at strategy weights instead of deriving them. Unlimited retry risks silently burning time/cost on a doomed approach, which the Master Prompt explicitly warns against. The simple retry → re-observe → alternate → escalate sequence (Architecture §13) is deliberately unambitious and treated as a known Phase 2 upgrade target once real logs exist.
**Complexity:** Low for MVP, explicitly deferred complexity for later.
**Failure modes:** The simple strategy may escalate to the user more often than a smarter one would need to, in the early going. Treated as an acceptable MVP cost, not a defect.
**Revisit if:** Phase 2 benchmarking (§A.10) shows recovery-attempts-per-success is high enough that a smarter strategy would meaningfully change the success rate, not just the escalation rate.

### 8. SQLite for session state

**Why needed:** Session/task state must survive a Gateway restart (PRD FR-7).
**Alternatives considered:** Postgres or another server-based datastore; in-memory only with no persistence.
**Trade-offs:** This is a single-user, local-first tool — a server-based datastore adds an operational dependency (a running database process) for no benefit at this scale. In-memory-only would fail the restart-survival requirement outright. SQLite is the correct-sized tool, and sharing one store between the Gateway and Browser Adapter (§A.5) keeps a mixed GUI+browser session's timeline coherent without a distributed-transactions problem.
**Complexity:** Low.
**Failure modes:** SQLite's single-writer model could become a bottleneck under genuine multi-session parallelism, or under two MCP server processes (Gateway and Browser Adapter) writing concurrently within one session — but that's explicitly Phase 2+ scope (Decision #9), worth revisiting alongside that work, not before. If concurrent writes from two processes cause contention even in MVP, a lightweight write-queue in front of the shared store is a much smaller fix than swapping datastores.
**Revisit if:** Multi-user or hosted deployment (Phase 3) becomes a real requirement — that's the point where a server-based store earns its operational cost.

### 9. Single active session, no GUI-level parallelism in MVP

**Why needed:** The original architecture goals mention "eventually support parallel workers where useful" — worth being explicit about why "eventually" and not "now."
**Alternatives considered:** Design for N parallel GUI sessions from the start.
**Trade-offs:** Real GUI parallelism on one physical Mac means fighting over mouse/keyboard focus and window state — not a config flag, a genuine constraint of controlling a real desktop (Architecture §19). Designing for it prematurely would mean building isolation infrastructure (virtual displays or VMs) before the single-session case even works reliably.
**Complexity:** Deferring this avoids a large, currently-unjustified complexity cost.
**Failure modes:** None directly — this is a scope decision, not a risk.
**Revisit if:** Phase 2's browser-first parallelism (which is realistic now, since multiple `BrowserSession`s are cheap and isolated) proves the orchestration pattern works, making GUI-level parallelism a smaller incremental step than starting from zero.

### 10. Permission policy enforced in the Gateway and Browser Adapter, not left to the model/prompt

**Why needed:** Destructive actions need a hard stop that doesn't depend on the model "remembering" to ask.
**Alternatives considered:** Rely solely on the Master Prompt's permission-boundary instructions; rely on OpenCode's own general safety training.
**Trade-offs:** Same reasoning as Decision #6 — prompted behavior is a nudge, not a guarantee, especially deep into a long session. Both servers checking the action class against policy before dispatch (Architecture §16) is the backstop; the prompt's job is to make OpenCode ask *before* even attempting, so the block is rarely hit in practice rather than being the primary mechanism.
**Complexity:** Low — a shared policy config file and a check at each `act` call site.
**Failure modes:** A policy that's too coarse-grained either blocks legitimate actions (annoying) or misses a destructive action class the author didn't think to list (dangerous). Start conservative (block more, not less) and loosen deliberately.
**Revisit if:** False-positive blocks become frequent enough to hurt usability — tune the policy list based on real logged `permission.denied` events, not guesswork.

### 11. OS Adapter as the sole platform-coupling boundary

**Why needed:** The original brief explicitly wants macOS-first without permanently coupling the core to macOS.
**Alternatives considered:** Let platform-specific calls (screenshot APIs, accessibility APIs) live wherever convenient in the Gateway; build a full cross-platform abstraction (Windows/Linux included) from day one.
**Trade-offs:** A single, disciplined boundary (Architecture §2) makes Phase 3's Windows/Linux work additive rather than a rewrite, without paying the cost of building and testing three platform adapters before any of them ships. This is the standard "design the seam, build one side of it" pattern. The Browser Adapter has no equivalent problem — Playwright is already cross-platform — so this boundary discipline is specific to `os_adapter/`.
**Complexity:** Low added cost now, meaningful cost avoided later.
**Failure modes:** Discipline can erode — a convenient macOS-specific shortcut taken elsewhere in the Gateway "just this once" defeats the purpose. Worth a lightweight lint/review rule (no `pyobjc` imports outside `os_adapter/`) rather than relying on memory.
**Revisit if:** Phase 3 actually starts — at that point, audit for boundary violations before beginning the Windows/Linux adapter, not after.

### 12. Local-first processing as the default

**Why needed:** Screenshots, accessibility data, and DOM snapshots can contain sensitive on-screen or on-page information (PRD §10).
**Alternatives considered:** Default to whichever model provider is fastest/cheapest regardless of where it runs; make remote processing opt-out instead of opt-in.
**Trade-offs:** Defaulting to local processing costs some raw performance/model-quality headroom (a hosted larger model might outperform a local 7B) in exchange for not shipping screenshots or page content of the user's real session to a third party by default. Given this project starts as a personal/local tool, that trade favors privacy by default; the remote-endpoint option remains available and explicit (Architecture §15), not removed.
**Complexity:** Low — this is a default, not a restriction.
**Failure modes:** None beyond the acknowledged performance trade-off.
**Revisit if:** A use case emerges where local hardware is genuinely insufficient and the user has explicitly decided the privacy trade is acceptable — the config path for that already exists, it just isn't the default.

### 13. Local structured logs, no hosted telemetry

**Why needed:** Sessions need to be debuggable and reconstructable (PRD §12).
**Alternatives considered:** A hosted logging/telemetry service from the start.
**Trade-offs:** For a single-user local tool, a hosted telemetry pipeline is pure operational overhead — another account, another dependency, another thing that can fail independently of the actual task. Local JSON-lines files plus a simple local viewer (Architecture §17) satisfy the same debuggability requirement at near-zero cost.
**Complexity:** Low.
**Failure modes:** No cross-session aggregate dashboards without writing a small script to compute them — acceptable at this scale.
**Revisit if:** Multi-user/hosted deployment (Phase 3) becomes real, or the sheer volume of local logs makes ad hoc script-based analysis genuinely painful.

### 14. Python for the Gateway and Browser Adapter

**Why needed:** Some language has to host both MCP servers, the executor loop, and the OS Adapter glue.
**Alternatives considered:** TypeScript/JS (matches OpenCode's own plugin language, and Browser-Use has a JS/TS port too, though its canonical implementation is Python); Swift (native macOS accessibility/Cocoa access with no bridging layer); Rust (performance, safety).
**Trade-offs:** Python wins on ecosystem fit specifically: Browser-Use's canonical implementation (and its `Controller`/`BrowserSession` internals this project depends on directly) is Python, `pyobjc` gives direct `AXUIElement`/`CGEvent` access without a separate compiled helper, and MLX's Python bindings (`mlx-lm`/`mlx-vlm`) are the primary, best-supported interface. Swift would give cleaner native macOS access but forces a second-language bridge to both Browser-Use and MLX's Python-first tooling. TypeScript would match OpenCode's plugin language but loses the direct accessibility API access and the MLX/Browser-Use ecosystem fit — and would mean depending on Browser-Use's TS port rather than its more mature canonical Python library for the low-level `Controller` access Decision #5 depends on.
**Complexity:** Python's runtime overhead is a minor concern for this workload (I/O- and inference-bound, not CPU-bound in the hot path).
**Failure modes:** `pyobjc`'s accessibility API coverage can lag behind the latest macOS APIs occasionally — worth a spike/prototype early (Roadmap M1) specifically to confirm coverage for your actual target apps before committing further.
**Revisit if:** M1 (OS Adapter) prototyping reveals `pyobjc` gaps serious enough to block real apps — the fallback would be a small Swift helper binary the Python Gateway shells out to for accessibility calls specifically, keeping everything else in Python.

### 15. Prompt + schema redundancy for reliability-critical behavior

**Why needed:** This is a meta-decision that shows up across #6, #10, and the Master Prompt itself.
**Alternatives considered:** Pick one layer (prompt-only, or schema-only) and keep the system simpler.
**Trade-offs:** Deliberately redundant: the Master Prompt explains *why* a rule exists so OpenCode's reasoning stays aligned with it under normal conditions, while the schema/policy layer catches the cases where reasoning alone fails — long sessions, context pressure, or an edge case the prompt author didn't anticipate. This isn't accidental duplication; it's the actual design principle behind "optimize for reliability rather than blindly maximizing autonomy," made concrete.
**Complexity:** Modest — maintaining two layers that need to stay conceptually consistent (a schema change should usually come with a prompt update explaining it, and vice versa).
**Failure modes:** The two layers drifting out of sync over time (prompt says one thing, schema enforces something subtly different) is the main risk — worth a periodic manual cross-check between the Master Prompt doc and the Gateway/Browser Adapter's actual schema/policy code as both evolve.
**Revisit if:** Never fully — this principle should hold for any future reliability-critical rule added to the system, not just the ones listed here.

### 16. Qwen-UI-Agent (Phase 2) used in grounding-only mode, never its full agent stack

**Why needed:** Qwen-UI-Agent (Alibaba Tongyi-MAI, July 2026) is not a drop-in replacement for UI-TARS's role in this architecture — it's a broader real-world agent unifying GUI, CLI, and API actions with its own multi-turn planning behavior, structurally closer to Agent TARS than to bare UI-TARS. This decision exists to make sure Phase 2 doesn't quietly reintroduce the exact problem Decision #3 was written to avoid, just with a different model.
**Alternatives considered:** Integrate Qwen-UI-Agent's full agent loop directly, since it would likely produce strong headline results faster than extracting a grounding-only mode; skip Qwen-UI-Agent entirely and pick a narrower Qwen-VL-family grounding checkpoint instead.
**Trade-offs:** Using the full agent loop would be faster to a Phase 2 demo but would mean OpenCode delegating real planning to a second system for GUI subtasks — undermining the verification/recovery model this entire platform is built around, and making Phase 2's "compare backends on the same tasks under the same harness" benchmarking (§A.10) apples-to-oranges, since one backend would be doing its own planning and the other wouldn't. Restricting to grounding-only mode keeps the comparison fair and keeps the architecture's core guarantee intact, at the cost of needing to confirm (at Phase 2 start, against whatever Qwen-UI-Agent's actual public release looks like by then) that a single-step grounding interface is genuinely separable from its full agent stack.
**Complexity:** Medium — comparable to the original UI-TARS integration effort (Decision #3), not a "just swap a config value" change, because it requires the same kind of interface discipline.
**Failure modes:** If Qwen-UI-Agent's public release doesn't cleanly separate grounding from planning by the time Phase 2 starts, the fallback is to use a narrower Qwen-VL-family GUI-grounding checkpoint from the same research lineage instead of Qwen-UI-Agent proper, preserving the spirit of the comparison (a strong, current Alibaba/Tongyi GUI-grounding model) without the full-agent risk.
**Revisit if:** Phase 2 planning begins — this decision should be re-confirmed against Qwen-UI-Agent's actual released interfaces at that time, not assumed to still be accurate, since this is a fast-moving model line that was only weeks old as of this doc's last update.

---

**That's the full set.** Four files: PRD, Architecture, Master Prompt, and this combined Roadmap/Engineering Decisions log. They're written to be read in order once, then referenced independently after — each one links forward to what depends on it, so you shouldn't need to re-read the whole set to make a single change later.
