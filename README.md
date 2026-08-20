# OpenCode Computer-Use Platform

Single orchestrator (OpenCode, unmodified) driving desktop GUI and browser workers through
MCP, with structural verification and recovery. Docs (source of truth):

- `docs/01-PRD.md` — product requirements
- `docs/02-Architecture.md` — component architecture
- `docs/03-Master-Prompt.md` — supervisor prompt delivered via MCP `instructions`
- `docs/04-Roadmap-and-Engineering-Decisions.md` — milestones + engineering decisions

## Troubleshooting

### Recurring "privacy & security" permission prompts (fixed)

When the platform is launched from the native app, macOS TCC attributes
screen-capture / accessibility requests to the app's process tree. A prior
version spawned `opencode serve` detached (breaking the responsible-process
chain), auto-restarted failing services forever, and the UI polled
`/api/screen.jpg` every ~2.5 s — so a missing grant caused an **infinite
prompt loop** that made the desktop unusable.

Fixed structurally (M11):

- The screen loop **stops after 2 failed captures** and latches
  `screenPermissionDenied`; `/api/screen.jpg` returns `409` and the UI
  shows a "Screen Recording permission needed" banner with an **Open
  System Settings** button instead of polling
- `opencode serve` is no longer spawned detached, so its children (the
  gateway python, screencapture) stay under the same responsible app and
  one grant covers them
- Auto-restart is capped at 5 attempts, then the server stands down until
  you press START
- The banner resumes capture when you click **Retry** after granting

**What to enable (once, per app build):** open System Settings →
Privacy & Security → **Screen Recording** and **Accessibility**, and
enable the entries that appear — typically **CUA Control**; also enable
**Terminal** / **node** / **Python** if listed. Rebuilding the app creates
a new bundle identity, so re-enable the new entry after a rebuild. When
the grant is missing the UI now tells you exactly what to do instead of
looping.

## Status

**Native app + instant STOP + speed (2026-08-20, M8–M10):**

- **Speed:** OCR at the Fast recognition level (12× faster: ~1s → ~80ms),
  content-hash-cached OCR (repeated observes of an unchanged screen are
  free — 719ms → 311ms), and an adaptive settle that exits the instant the
  screen changes after a dispatch (full act cycle 2.3s → ~0.9s, verified
  live on Calculator click + verify)
- **STOP/START lifecycle:** `POST /api/stop` SIGKILLs the whole opencode
  process group — opencode serve + CUA Gateway (screen capture) + Browser
  Adapter die instantly — and halts the screen capture loop (`/api/screen.jpg`
  → 409). `POST /api/start` brings everything back. Red STOP / green START
  buttons in the UI header
- **Native macOS app (`native/build.sh` → `native/build/CUA.app`):** double-click
  to launch. The app boots the control server → opencode serve → both MCP
  servers, opens the full control UI (chat, live screen, logs, gateway trace)
  in an embedded window — no browser tab, no URL. Toolbar: status indicator,
  START, STOP. Quitting the app stops all services. Logs land in
  `logs/control-server.out`. The app walks up from its bundle to find the
  repo (or honors `CUA_PLATFORM_ROOT`)
- **Reliable default model:** the control server now prefers
  `deepseek-v4-flash-free` for tasks instead of the provider's raw default
  (nemotron-3.5-lightning-free produced empty sessions — zero tool calls
  for 9 minutes)
- Verified end-to-end (2026-08-20): app launch → stack healthy → both MCP
  servers connected → live task "open Calculator, type 7+8, report the
  display" completed with a screen-grounded answer within 3 minutes

**Verification overhaul (2026-08-20):** the #1 failure source fixed. Session
logs showed 73% of actions failing (browser 100%, gateway ~50%) even though
the actions *worked* — the verifier did an exact-substring match of the
planner's whole natural-language sentence against AX/DOM/OCR text
("the browser is on example.com" is not a substring of
"URL: https://example.com/"). Now:

- `shared/verifier.py` extracts content fragments from postconditions —
  quoted strings, URLs, domain names, capitalized proper nouns, digits —
  and passes when the *content* is present in the evidence (strict: quoted
  strings are mandatory, ≥50% of anchors must match, unanchored prose still
  fails)
- Browser Adapter verifies action-type-aware: navigate → URL/domain
  present; extract → non-empty extraction results; type → live input value
  read from the DOM via XPath (input values were invisible to DOM text
  before); dispatch failures are always FAIL; wrong-URL postconditions
  (e.g. "on wikipedia.org" while on example.com) still FAIL
- Browser Adapter exposes indexed interactive elements (`elements` array in
  `browser.observe`); click indexes normalized to browser-use's 1-based
  scheme (previously click validation errored on index 0 — clicks were
  impossible, so the planner only ever navigated/extracted)
- Gateway: settle delay after CGEvent dispatch before evidence read-back;
  dispatch failures are always FAIL
- Verified live on macOS (2026-08-20): browser navigate/extract/click/type
  pass on attempt 1; gateway click "2" in Calculator (display genuinely
  shows 2) and type in TextEdit pass on attempt 1; wrong-target postconditions
  still exhaust recovery correctly. 89 unit tests pass

**Milestone M6 (Frontend — Computer-Use control UI): done.** Verified on macOS (2026-08-19):

- `frontend/` — React + Vite panel (dark theme) over a zero-dependency Node
  control server: launch an app from the catalog, pick a model, type a task,
  and watch the agent work the real desktop — live screen feed (downscaled
  captures streamed over SSE), activity feed with tool cards and
  verdict/recovery badges, permission modal (or auto-approve bash/edit),
  MCP/gateway status chips, gateway JSONL log viewer
- Control server spawns and supervises `opencode serve` with the platform
  `opencode.json`, so `cua-gateway` (`gui.*`) and `browser-adapter`
  (`browser.*`) are always wired to the planner; auto-restarts on exit
- One-command start: `./start.sh` (builds if stale, opens the browser, runs)
- `frontend/scripts/smoke-test.js` — 11-check end-to-end suite: serve health,
  both MCP servers connected, real LLM task run through `prompt_async` +
  SSE, screen capture pipeline (incl. TCC fallback), abort, JSONL log stream;
  **11/11 pass**
- UI-TARS grounding verified live: full screenshot → `UiTarsMLXClient` →
  normalized action (623s first-run incl. 16s model load; ~2min subsequent)
- Fix: mlx-vlm loader hung 10+ min probing the HF hub with a fully cached
  model — `UiTarsMLXClient` now pins `HF_HUB_OFFLINE=1` when the repo is in
  the cache (`gateway/executor/model_client.py`)
- Fix: Browser Adapter rewritten for browser-use 0.13.8 (`dom_state.selector_map`,
  `get_all_children_text`, `registry.create_action_model()` dispatcher)
- Notes: the planner loop quality is bounded by the LLM behind opencode —
  with `deepseek-v4-flash-free` the agent ran `gui.observe` but stalled on a
  plan; the same session on a stronger model drives act/verify/recover
  (gateway enforces the recovery loop server-side, M4)

**Milestone M4 (Recovery engine): done.** Verified on macOS (2026-08-18):

- `shared/recovery.py` — pure, M5-reusable recovery logic (Architecture §13):
  bounded attempt/time budgets, the retry → re-observe (alternate state source) →
  escalate ladder, and the structured JSON-lines session event log (§11/§17:
  `action.dispatched`, `verification.result`, `recovery.attempted`,
  `recovery.exhausted`)
- Gateway `gui.act` enforces the loop server-side: on a non-pass verdict it
  re-dispatches within budget, forces the vision state source on later attempts,
  escalates with `recovery: {attempts, strategies, escalated, budget}` when
  exhausted, and logs every attempt to `logs/session-<id>.jsonl`
- `gui.act` accepts an optional `recovery_budget` (`max_attempts`, `max_seconds`)
- e2e: budget exhaustion with `max_attempts=2` returns `["retry","escalate"]` and
  writes the full event log; 75 unit/contract tests pass (3 skipped, CUA_E2E-gated)
- `opencode.json`: gateway MCP registered in **live mode**
  (`CUA_GATEWAY_LIVE=1`, 300s timeout for model-grounding latency) — after an
  opencode restart, `gui.*` tools are available in-session and OpenCode (the
  planner) drives observe/act/verify/recover directly

**Milestone M3 (Verification engine): done.** Verified on macOS (2026-08-18):

- `shared/verifier.py` — source selection (`best_available_source`: AX when the
  per-app capability probe says trustworthy, else vision), AX text flattening
  (titles + descriptions + values), case-insensitive substring verdicts with
  `pass` / `fail` / `uncertain` on empty evidence (Architecture §12)
- `gateway/executor/verifier.py` — `GatewayVerifier` glue (AX state or Vision-OCR
  of the screenshot, duck-typed `ocr_screenshot` so the ABC stays untouched)
- `gateway/executor/session.py` — `SessionStore` (action_id → action /
  postcondition / record / observation / verdict), powering `gui.verify` re-checks
- AXElement now carries `kAXValueAttribute` — verified live: typed text in
  TextEdit reads back as `AXTextArea value='...'`, so text postconditions verify
  against real document content, not just labels
- e2e over stdio MCP: nonsense/stale postconditions → `fail`, typed-marker
  postcondition → `pass`, `gui.verify` re-check of both, error paths (missing
  postcondition, unknown action_id); environment quirks solved along the way
  (stale `runningApplications()` after quit — use `pgrep` as ground truth and
  retry `open` past LaunchServices bookkeeping; SIGKILL suppresses TextEdit's
  welcome dialog — graceful quit + savedState wipe is the reliable reset)

**Milestone M2 (GUI executor — model-grounded actions): done.** Verified on macOS
(2026-08-18):

- `gateway/executor/model_client.py` — the swappable `infer()` seam (Architecture §5/§6):
  `UiTarsMLXClient` serving `mlx-community/UI-TARS-1.5-7B-4bit` via mlx-vlm
  (lazy model load — unit tests never touch the 4.9GB model), OSWorld-style prompt,
  output parser, and normalization to `shared.action_schema` in screen-point coords
- Coordinate mapping solved: the qwen2.5-vl processor `smart_resize` (factor 28,
  min 3136, max 12845056) is replicated in-process and verified against the runtime
  processor; retina 2× captures (3600×2338 image ↔ 1800×1169 point display) map
  exactly; `Screenshot` now carries image pixel dims + `display_width/height`
- e2e (TextEdit "New Document"): model grounded the click **1 px** from the button
  center (691,627 vs 694,626), dispatch closed the dialog, AX verified the
  postcondition
- Root-cause found en route: a python process without **Screen Recording** permission
  gets a redacted frame (wallpaper + menu bar only, no other apps' windows) — a
  venv rebuild (new interpreter path) silently dropped the TCC grant; fixed by
  adding `/opt/homebrew/bin/python3.14` to System Settings → Privacy & Security →
  Screen Recording (pane-driven via AX, since the process itself can't see the UI)
- 34 unit/contract tests pass; e2e gated behind `CUA_E2E=1`

**Milestone M1 (OS Adapter): done.** Verified on macOS (2026-08-18):

- `shared/action_schema.py` — normalized Action/Observation/Verification shapes (§A.6 item 2)
- `gateway/os_adapter/base.py` — `OSAdapter` ABC; platform coupling confined to `os_adapter/`
  (ED #11, enforced by a tripwire test)
- `gateway/os_adapter/macos_adapter.py` — screen capture (PNG), AX tree reads with frames,
  `find_elements`, CGEvent click/double/right/drag/scroll/type/key dispatch
- Live-verified end-to-end: clicked "New Document" in TextEdit, clicked into the AXTextArea,
  typed a marker, and read it back via `kAXValueAttribute` — exact match
- 15 unit/contract tests pass

**Milestone M0 (skeleton): done.** Verified on OpenCode 1.18.18 (2026-08-18):

- Gateway stub registers `gui.observe` / `gui.act` / `gui.verify` (hardcoded responses)
- MCP `instructions` field (Master Prompt + marker `M0P-7F3A9C`) **lands in session
  context** — confirmed functionally (model reproduced the marker) and the stub's
  tools are callable end-to-end through `opencode serve` (docs' v1.17.10+ claim holds
  on 1.18.18)
- Real logic starts in M1 (OS Adapter), per Roadmap §A.6

## Config sync note

`opencode.json` at repo root is what OpenCode actually reads; `config/mcp.json` is the
canonical registration per Roadmap §A.5. They must be kept in sync — update both when
changing MCP server registrations.

## SDK note

The docs reference "a mature MCP server SDK" without pinning a version. Installed:
`mcp` **2.0.0** — the `FastMCP` helper from 1.x is gone; the Gateway uses the lowlevel
`Server` API (`on_list_tools` / `on_call_tool` handlers, `Server(instructions=...)`).
The Browser Adapter (M5) should use the same pattern.

## Repo layout (per Roadmap §A.5)

| Path | Role |
|---|---|
| `gateway/` | CUA Gateway — MCP server (executor, os_adapter, policy in later milestones) |
| `browser_adapter/` | Browser Adapter — sibling MCP server (M5) |
| `shared/` | verifier, recovery engine, session store — imported by both servers |
| `config/mcp.json` | OpenCode MCP server registration |
| `opencode-plugin/log-mirror/` | optional OpenCode session-log mirror (nice-to-have) |
| `tests/` | unit / integration / e2e (acceptance scenario) |
| `logs/` | local structured session logs (gitignored) |

## Running the Gateway stub (M0)

```sh
cd gateway
uv sync
uv run python -m mcp_server
```

The server speaks MCP over stdio. `config/mcp.json` registers it with OpenCode.

## macOS prerequisites (one-time, manual — Roadmap §A.8.4)

Grant **Accessibility** and **Screen Recording** permissions to your terminal/Python
process in System Settings → Privacy & Security. This is the single most common
"why isn't this working" issue.