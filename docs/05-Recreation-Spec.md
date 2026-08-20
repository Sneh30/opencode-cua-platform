# OpenCode Computer-Use Platform — Complete Recreation Specification

**Status:** Final · **Doc:** 5 of 5 (PRD → Architecture → Master Prompt → Roadmap + Engineering Decisions → **Recreation Spec**)
**Purpose:** This single document is written so that **another LLM (or engineer) can recreate the entire application from documentation alone, without the codebase.** Every module, constant, algorithm, contract, prompt, and roadblock is specified here. Where this doc references `docs/01`–`docs/04`, those are the companion requirement/architecture documents that also live in the repo.

**Reference build (what this spec recreates):** milestone M11 of `opencode-cua-platform` (git tag/commit `ab3cd80`), verified on **macOS 26.5.2 (Apple Silicon)** with **node v24.17.0** (`/opt/homebrew/bin/node`), **uv** (`~/.local/bin/uv`), **opencode v1.18.18** (`~/.opencode/bin/opencode`), Python **3.14** venvs managed by uv, `swiftc` at `/usr/bin/swiftc`.

---

## 1. What this system is

A local-first, single-user platform that gives **OpenCode** (an unmodified AI coding agent that runs headless as `opencode serve`) the ability to control **real macOS desktop applications** and **a real web browser**, one grounded action at a time, with structural verification after every action and bounded recovery on failure.

Architecture in one paragraph: OpenCode is the only planner. It calls MCP tools exposed by two custom MCP servers:

- **CUA Gateway** (`gui.*` tools) — controls the desktop: captures screenshots, reads the macOS accessibility tree, dispatches mouse/keyboard events via CGEvent, and verifies each action against an `expected_postcondition` written by the planner.
- **Browser Adapter** (`browser.*` tools) — controls a real browser via Playwright through the `browser-use` library's low-level `Controller` registry and `BrowserSession` DOM state (deliberately NOT its bundled `Agent`, which runs its own LLM planning loop — that would create a second planner underneath OpenCode, an explicitly forbidden failure mode).

A **Control Server** (zero-dependency Node.js) wraps `opencode serve`: it spawns/supervises it, serves a web UI, streams a live screen capture, watches structured JSONL session logs, auto-approves routine permission prompts, and exposes STOP/START lifecycle endpoints. A **native macOS app** (`CUA Control`) embeds the web UI in a WKWebView and boots the whole stack on double-click.

The single non-negotiable behavioral rule: **an action being dispatched is not an action succeeding.** Every `gui.act`/`browser.act` call requires an `expected_postcondition`, the server verifies it after dispatch, and the verdict (`pass`/`fail`/`uncertain`) is returned to the planner. Never report success without a `pass` verdict.

---

## 2. System diagram and data flow

```
                        ┌───────────────────────────────┐
                        │          User                 │
                        │  (web UI or native app)       │
                        └──────────────┬────────────────┘
                                       │ HTTP / SSE (localhost:8787)
                                       ▼
                    ┌────────────────────────────────────┐
                    │  Control Server  (node server/index.js)│
                    │  · spawns/supervises opencode serve  │
                    │  · web UI + JSON API + SSE hub        │
                    │  · live screen capture loop + TCC latch│
                    │  · gateway JSONL log watcher          │
                    │  · auto-approve bash/edit permissions │
                    └──────────────────┬─────────────────────┘
                                       │ spawns (cwd = repo root, NOT detached)
                                       ▼
                    ┌────────────────────────────────────┐
                    │  opencode serve  (localhost:49152) │  ← the ONLY planner
                    │  reads ./opencode.json at repo root│
                    └───────┬──────────────────┬─────────┘
                            │ MCP stdio        │ MCP stdio
                            ▼                  ▼
        ┌──────────────────────────┐  ┌──────────────────────────┐
        │ CUA Gateway (Python MCP) │  │ Browser Adapter (Py MCP) │
        │ gui.observe/gui.act/     │  │ browser.observe/act/     │
        │ gui.verify               │  │ verify                   │
        │ + Master Prompt via MCP  │  │ + browser Master Prompt  │
        │   `instructions` field   │  └──────────┬───────────────┘
        └─────────────┬────────────┘             │ browser-use Controller
                      │ OS Adapter (macos_adapter.py) │ BrowserSession (Playwright)
                      │ CGDisplayCreateImage · AXUIElement · CGEvent
                      ▼                             ▼
            Real macOS desktop apps          Real browser (headless
            (Calculator, TextEdit, …)        Chromium, 1280x720)
```

**Per-action data flow (gateway):** planner calls `gui.act(session_id, {type, target_hint, params}, expected_postcondition)` → gateway resolves coordinates if missing (model grounding, slow path) → dispatches via CGEvent → **adaptive settle** (poll screen content hash until it changes, capped) → captures fresh screenshot → picks best verification source (AX tree if trustworthy, else Vision OCR) → `shared/verifier.py` renders verdict → on non-`pass`, retry ladder (`retry` → `reobserve` with vision forced → `escalate`) with bounded budget → every attempt appended to `logs/session-<id>.jsonl` → result with `verdict` + `evidence` + `recovery` returned to planner.

---

## 3. Environment prerequisites (one-time setup)

1. macOS 26.x on Apple Silicon. Xcode Command Line Tools (`xcode-select --install`) for `swiftc`, `sips`, `screencapture` (the latter two are in `/usr` anyway).
2. **Node.js ≥ 20** (verified: v24.17.0 at `/opt/homebrew/bin/node`).
3. **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`) at `~/.local/bin/uv`.
4. **opencode CLI** installed to `~/.opencode/bin/opencode` (version 1.18.18 verified). Ensure `~/.opencode/bin` is on PATH for the control server (the native app injects it).
5. **Python 3.14** available to uv (uv downloads it automatically).
6. **TCC permissions (System Settings → Privacy & Security):**
   - **Screen Recording** and **Accessibility** for the terminal/python that will run the gateway venv (`gateway/.venv/bin/python`).
   - Screen Recording for the responsible app of the control server (Terminal, node, or the CUA.app bundle identity depending on launch context).
   - The gateway python needs Screen Recording to see other apps' windows at all (a process without the grant gets a redacted frame: wallpaper + menu bar only).
7. A model provider configured for opencode (the control UI lists providers via `GET /provider`). Free-tier model `deepseek-v4-flash-free` is the verified reliable default; see Roadblock R12.
8. (Optional) `mlx-vlm` downloads `mlx-community/UI-TARS-1.5-7B-4bit` (~4.9 GB) to the HuggingFace cache on first grounding use.

---

## 4. Repository layout (must recreate exactly)

```
opencode-cua-platform/
├── opencode.json              # MCP registration read by opencode serve (gateway + browser)
├── start.sh                   # one-command start: build UI if missing, open browser, run control server
├── README.md
├── docs/
│   ├── 01-PRD.md  02-Architecture.md  03-Master-Prompt.md  04-Roadmap-and-Engineering-Decisions.md
│   └── 05-Recreation-Spec.md           # this file
├── shared/                    # pure logic, imported by BOTH MCP servers (no platform deps)
│   ├── __init__.py
│   ├── action_schema.py       # normalized dataclasses/enums (§5.1)
│   ├── verifier.py            # fragment-matching verification engine (§5.2)
│   └── recovery.py            # bounded retry ladder + JSONL event log (§5.3)
├── gateway/                   # CUA Gateway MCP server (Python, uv-managed venv, Python 3.14)
│   ├── pyproject.toml         # package cua-gateway, script entry cua-gateway
│   ├── mcp_server.py          # MCP server, LiveGateway loop, Master Prompt, tool schemas (§6)
│   ├── os_adapter/
│   │   ├── __init__.py        # exports MacOSAdapter
│   │   ├── base.py            # OSAdapter ABC + AppInfo/AdapterCapabilities/errors (§6.2)
│   │   ├── macos_adapter.py   # the ONLY macOS-coupled module (§6.3)
│   │   └── keycodes.py        # macOS key names → CGEvent keycodes/flags (§6.4)
│   └── executor/
│       ├── __init__.py
│       ├── session.py         # in-memory SessionStore (action history) (§6.5)
│       ├── model_client.py    # swappable infer() seam; UiTarsMLXClient + parsers (§6.6)
│       └── verifier.py        # GatewayVerifier: AX/OCR → shared.verifier (§6.7)
├── browser_adapter/           # Browser Adapter MCP server (Python, uv venv)
│   ├── pyproject.toml         # package browser-adapter, script entry browser-adapter
│   ├── mcp_server.py          # MCP server, act-and-verify loop, BROWSER_MASTER_PROMPT (§7.1)
│   ├── action_dispatch.py     # Controller-based dispatcher + deterministic extract JS (§7.2)
│   └── session_wrapper.py     # BrowserSession wrapper: DOM-first observe (§7.3)
├── frontend/
│   ├── package.json           # react/react-dom, vite build → ../dist; scripts build/dev/start/test:smoke
│   ├── vite.config.js         # root "web", outDir "../dist", dev proxy /api → :8787
│   ├── index.html             # (at web/ level) — see web/index.html
│   ├── server/                # Control Server (zero npm runtime deps)
│   │   ├── index.js           # HTTP server + SSE hub + API routes + lifecycle (§8.1)
│   │   ├── opencode.js        # OpencodeServer: spawn/supervise/SSE client/stop (§8.2)
│   │   ├── screen.js          # capture pipeline + permission latch (§8.3)
│   │   ├── apps.js            # macOS app catalog + launcher (§8.4)
│   │   ├── logs.js            # GatewayLogs JSONL watcher (§8.5)
│   │   └── capture_fallback.py# gateway-venv fallback capture (§8.6)
│   ├── scripts/
│   │   └── smoke-test.js      # 11-check end-to-end suite (§15.3)
│   └── web/src/               # React UI (§9)
│       ├── main.jsx  App.jsx  styles.css  index.html
│       ├── lib/api.js         # fetch wrapper
│       └── components/
│           ├── StatusBar.jsx  AppLauncher.jsx  LiveScreen.jsx  ActivityFeed.jsx
│           ├── TaskComposer.jsx  GatewayLog.jsx  SessionsList.jsx  PermissionModal.jsx
├── native/                    # native macOS app
│   ├── main.swift             # AppKit + WKWebView launcher (§10.1)
│   ├── Info.plist             # bundle ai.opencode.cua-control (§10.2)
│   └── build.sh               # swiftc + ad-hoc codesign → build/CUA.app (§10.3)
├── config/mcp.json            # canonical MCP registration (must stay in sync with opencode.json)
├── tests/
│   ├── unit/                  # 89 tests, run from gateway/ (see §15.2)
│   │   ├── test_action_schema.py  test_gateway_stub.py  test_gateway_verifier.py
│   │   ├── test_model_client.py   test_os_adapter_base.py  test_recovery.py
│   │   └── test_verifier.py
│   └── integration/           # M2/M3 live tests (gated by CUA_E2E=1)
├── logs/                      # runtime: session-*.jsonl, observe.png, act.png, control-server.out (gitignored)
└── .gitignore                 # .venv/, frontend/node_modules/, frontend/dist/, native/build/, logs/
```

`config/mcp.json` and `opencode.json` MUST be kept identical in their MCP blocks (documented in README as "Config sync note").

---

## 5. Shared layer (`shared/`)

Pure Python, no dependencies beyond stdlib + the other shared modules. Both MCP servers do `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` at import time so `import shared...` works regardless of cwd.

### 5.1 `shared/action_schema.py` — normalized data shapes

Enums (all `str`-valued `Enum`):

- `ActionType`: `click`, `double_click`, `right_click`, `drag`, `type`, `key`, `scroll`
- `BrowserActionType`: `navigate`, `click`, `type`, `extract`, `scroll`, `select`, `key`
- `ObservationSource`: `vision`, `accessibility`, `dom`
- `Verdict`: `pass`, `fail`, `uncertain`

Frozen dataclasses (all with `.to_dict()`):

- `Action`: `type: ActionType`, `target_hint: str = ""`, `params: dict = {}`; `from_dict` classmethod. Semantics: at executor level `target_hint` is natural language (model-grounding); at OS-adapter level `params` carries resolved values.
- `Screenshot`: `ref: str`, `width: int` (image pixel width — Retina 2×: 3600×2338 image for 1800×1169-point display), `height: int`, `captured_at: str` (`%Y-%m-%dT%H:%M:%S%z`), `display_width: int|None`, `display_height: int|None`, `sha: str = ""` (content hash of the PNG bytes). `.to_dict()` keys: `screenshot_ref`, `width`, `height`, `captured_at`, `display_width`, `display_height`, `sha`.
- `AXElement`: `role`, `title=""`, `description=""`, `value=""`, `frame: tuple[int,int,int,int]|None` = `[x, y, width, height]` in screen points, `children: list[AXElement]`.
- `AccessibilityState`: `app_name`, `app_pid`, `root: AXElement|None`, `trustworthy: bool` (per-app capability probe result).
- `Observation`: `screenshot_ref`, `accessibility_tree: dict|None`, `active_app=""`, `source_used=ObservationSource.VISION`, `extra={}` (`.to_dict()` splats `extra` at top level).
- `ActionRecord`: `action_id`, `dispatched: bool`, `raw_result: dict = {}`.
- `VerificationResult`: `verdict: Verdict`, `evidence: dict = {}`, `source_used: str = ""`.

### 5.2 `shared/verifier.py` — verification engine (the heart of "did it actually work")

**Core insight:** the planner writes natural-language postconditions ("the browser is on example.com"); exact substring matching of the full sentence against evidence text is a false-negative machine. Instead, extract **content-bearing fragments** from the postcondition and check whether that *content* is present in the evidence.

**Normalization:** `normalize(text)` = lowercase + collapse all whitespace runs to single spaces + strip.

**Fragment extraction** (`extract_fragments(expected) -> {"quotes":[], "urls":[], "domains":[], "proper":[], "numeric":[]}`):

- `quotes`: contents of double- or single-quoted strings (non-empty).
- `urls`: `https?://[^\s'"]+` matches, stripped of trailing `"'. ,`.
- `domains`: `\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b` matches, minus any already inside a URL.
- `proper`: capitalized words (≥2 chars) that are NOT the sentence's first word (a sentence-initial "The"/"A" is prose), and not in the stopword set.
- `numeric`: alphanumeric tokens (≤6 chars) containing a digit (length ≥2 unless it's pure digits), not in stopwords.
- A large `_STOPWORDS` frozenset (~140 words: articles, prepositions, "browser", "window", "page", "button", "element", "contains", "shows", "clicked", "field", "value", "title", "successfully", "open", "closed", "then", "now", etc.) — anything that carries no verification value.

**Priority-ordered deduplicated fragment list:** quotes + urls + domains + proper + numeric, normalized, deduped preserving order.

**`has_verifiable_anchors(expected)`:** True iff the fragment list is non-empty. A prose-only postcondition ("the page loads") cannot be textually judged — callers fall back to clean-dispatch as the best signal.

**`verify_text(actual, expected) -> Verdict`:**

1. Either side blank → `UNCERTAIN`.
2. Normalized `expected` is a substring of normalized `actual` → `PASS`.
3. Fragments exist:
   - All quoted strings must be present in `actual` (any missing → `FAIL`).
   - `found/len(anchors) >= 0.5` AND `found >= 1` → `PASS`, else `FAIL`.
4. No fragments (pure prose): token overlap of `[a-z0-9]{3,}` tokens minus stopwords; `PASS` if `found >= max(1, (len(tokens)+1)//2)`, else `FAIL`.

**Other functions:** `matched_fragments(actual, expected)` (which fragments matched), `ax_text(state)` (flatten AX tree: concatenate every node's title/description/value, space-separated), `best_available_source(state, prefer="auto")` (auto: AX only if `state.trustworthy and root and ax_text` non-empty; `prefer` ∈ auto/vision/accessibility), `verify(expected, actual_text, source) -> VerificationResult` (evidence dict: `{expected, actual_text (first 400 chars), actual_text_length, match, matched_fragments}`, `source_used`), `observation_text_source(observation, state)`.

### 5.3 `shared/recovery.py` — bounded recovery ladder

- Constants: `STRATEGY_RETRY="retry"`, `STRATEGY_REOBSERVE="reobserve"`, `STRATEGY_ESCALATE="escalate"`, `STRATEGY_DONE="done"`.
- `RecoveryBudget(max_attempts=3, max_seconds=300.0)` (frozen dataclass, `.to_dict()`).
- `RecoveryTracker(budget)`: `attempts`, `strategies[]`, `elapsed` (monotonic), `exhausted` (attempts ≥ max OR elapsed ≥ max_seconds), `next_strategy(verdict)` (PASS→done; exhausted→escalate; attempts<2→retry; else reobserve), `advance()` (attempts += 1), `summary(escalated)` → `{attempts, strategies, escalated, budget}`.
- `choose_recovery_strategy(attempts_used, verdict, budget=None)` — stateless twin of `next_strategy`.
- `append_event(log_dir, *, session_id, worker, event_type, **fields) -> Path` — appends one JSON line to `log_dir/session-<session_id>.jsonl`: `{"event": event_type, "session_id": ..., "worker": ..., "timestamp": "%Y-%m-%dT%H:%M:%S%z", **fields}`.

---

## 6. CUA Gateway (`gateway/`)

### 6.1 `pyproject.toml`

`[project]` name `cua-gateway`, version 0.1.0, `requires-python = ">=3.14"`. Dependencies (pinned as installed):
`jinja2>=3.1.6`, `mcp[cli]==1.26.0`, `mlx-vlm>=0.6.14`, `pyobjc-framework-applicationservices>=12.2.2`, `pyobjc-framework-cocoa>=12.2.2`, `pyobjc-framework-quartz>=12.2.2`, `pyobjc-framework-vision>=12.2.2`.
`[project.scripts] cua-gateway = "mcp_server:main"`. `[project.optional-dependencies] dev = ["pytest>=9.1.1"]`. `[tool.uv] package = true`. `[tool.setuptools] py-modules = ["mcp_server"]`. `[tool.pytest.ini_options] testpaths = ["../tests"], pythonpath = [".", ".."]`.

### 6.2 `gateway/os_adapter/base.py` — the platform boundary

The ONLY module allowed to touch macOS APIs lives in `os_adapter/` (Engineering Decision #11). Everything above is platform-agnostic.

- `OSAdapterError(Exception)`; `OSAdapterPermissionError(OSAdapterError)`.
- `AppInfo(pid:int, name:str, bundle_id:str="", is_active:bool=False)` (frozen).
- `AdapterCapabilities(accessibility_trusted, screen_recording_available, active_display_size=None)` with properties `can_observe_ax`, `can_capture_screen`.
- `OSAdapter(ABC)`: `capabilities()`, `capture_screen(output_path) -> Screenshot`, `active_app() -> AppInfo|None`, `list_running_apps() -> list[AppInfo]`, `accessibility_state(app) -> AccessibilityState`, `find_elements(app, *, role, title, description, max_depth=40) -> list[(handle, frame)]`, `dispatch(action) -> ActionRecord` (params already resolved: click/double/right → `{"point":[x,y]}`; drag → `{"from":[x,y],"to":[x,y]}`; scroll → `{"delta":[dx,dy]}`; type → `{"text":...}`; key → `{"key":"cmd+shift+4"}`), `move_mouse(point)`.

### 6.3 `gateway/os_adapter/macos_adapter.py` — concrete macOS implementation (pyobjc)

**Imports (pyobjc):** `Quartz`; `ApplicationServices` (`AXIsProcessTrusted`, `AXUIElementCopyAttributeValue`, `AXUIElementCreateApplication`, `AXValueGetValue`, `kAX*Attribute` constants, `kAXValueCGPointType`, `kAXValueCGSizeType`, `kAXWindowsAttribute`, `kAXErrorSuccess`); `AppKit` (`NSApplicationActivationPolicyRegular`, `NSBitmapImageFileTypePNG`, `NSBitmapImageRep`, `NSWorkspace`). **Only the `k`-prefixed constant names exist in top-level `Quartz`/`ApplicationServices`** — see Roadblock R2.

State: `_shot_sha: dict[str,str]`, `_ocr_cache: dict[str,str]` (keyed by content hash), `_last_sha: str|None`.

- `capabilities()`: `AXIsProcessTrusted()`; screen-ok iff `CGDisplayCreateImage(CGMainDisplayID())` returns non-None; size from `CGDisplayBounds`.
- `capture_screen(output_path)`: `CGDisplayCreateImage` (None → raise `OSAdapterPermissionError("screen capture failed — Screen Recording permission missing?")`) → `NSBitmapImageRep.initWithCGImage_` → PNG data (`representationUsingType_properties_(NSBitmapImageFileTypePNG, None)`) → write atomically → `Screenshot(ref, pixelsWide, pixelsHigh, captured_at, display_width=bounds.size.width, display_height=bounds.size.height)` → `sha = sha256(PNG bytes).hexdigest()` stored in `_shot_sha[path]`, `_last_sha`, `shot.sha`.
- `screen_hash()`: same capture path but **no disk write**, returns `sha256(PNG bytes)` or None on any failure. Used by adaptive settle.
- `ocr_screenshot(screenshot)`: if `sha = screenshot.sha or _shot_sha.get(ref)` is in `_ocr_cache` → return cached text (unchanged screenshots cost ~0). Uses **`Vision.VNRecognizeTextRequest` with `VNRequestTextRecognitionLevelFast`** on the file at `screenshot.ref` (`VNImageRequestHandler.initWithURL_options_`), joins all `topCandidates_(1)[0].string()`. Caches by sha. Missing file → "". Failed request → "".
- `active_app()`: **live WindowServer query, NOT `NSWorkspace.frontmostApplication()`** (stale in long-lived processes without a run loop — Roadblock R1). `_frontmost_window_owner()` calls `CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID)`, iterates front-to-back: skip `kCGWindowLayer != 0`; skip windows with width<50 or height<50 (transient overlays); skip owners in the blacklist `{"Window Server","WindowManager","Control Center","Dock","SystemUIServer","loginwindow","Spotlight","TextInputMenuAgent"}`; first hit → owner PID. Resolve PID → `NSWorkspace.sharedWorkspace().runningApplications()` match; fallback to `frontmostApplication()` if no PID matched or none found. Returns `AppInfo(pid, localizedName, bundleIdentifier, is_active=True)`.
- `list_running_apps()`: `runningApplications()` filtered to `activationPolicy() == NSApplicationActivationPolicyRegular`.
- `window_titles(app)`: `AXUIElementCopyAttributeValue(root, kAXWindowsAttribute)` → per-window `kAXTitleAttribute` (best effort).
- `accessibility_state(app)`: raise `OSAdapterPermissionError` if `not AXIsProcessTrusted()`. Build AX tree: `_ax_element(el, pid, depth)` recurses with **max depth 60**; collects role/title/description/value (value only when str), frame via `_frame` (kAXPositionAttribute + kAXSizeAttribute → `AXValueGetValue(kAXValueCGPointType/kAXValueCGSizeType)`). `trustworthy = _probe_trustworthiness(root)`: `AXUIElementCopyAttributeValue(root, kAXChildrenAttribute)` succeeds AND children non-empty.
- `find_elements`: recursive attribute match over `kAXChildrenAttribute`.
- `dispatch(action)` (requires `AXIsProcessTrusted()`, else permission error):
  - click/double/right: move mouse to point, post `kCGEventLeftMouseDown/Up` (double: two down/up pairs, second pair with `kCGMouseEventClickState=2`; right: `kCGEventRightMouseDown/Up` with `kCGMouseButtonRight`).
  - drag: move to start, down, 12 interpolated `kCGEventLeftMouseDragged` steps, up at end.
  - scroll: optional `move_mouse(point)` then `CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 2, -dy, -dx)` with `delta=(dx,dy)`.
  - type: per character — `\n` → key combo `return`, `\t` → `tab`, else `CGEventCreateKeyboardEvent(None, 0, True/False)` + `CGEventKeyboardSetUnicodeString` (UTF-16 length), post down then up.
  - key: `parse_key_combo(key)` (modifiers array prepended with `+` joins if present) → `CGEventCreateKeyboardEvent` down with flags → post → up with flags.
  - Events posted with `CGEventPost(kCGHIDEventTap, event)`. Returns `ActionRecord(action_id=f"ax-{uuid12}", dispatched=True, raw_result={"type", "params"})`.
- `move_mouse(point)`: `CGEventCreateMouseEvent(None, kCGEventMouseMoved, CGPoint(x,y), kCGMouseButtonLeft)` → post.

### 6.4 `gateway/os_adapter/keycodes.py`

pyobjc 12 no longer ships HIToolbox bindings, so keycodes are enumerated by their stable Apple ABI values:

- ANSI letters/numbers/punct: `a`=0x00 … `m`=0x2E (standard kVK_ANSI_* table), backtick 0x32.
- Special: `enter`/`return`=0x24, `tab`=0x30, `space`=0x31, `backspace`/`delete`=0x33, `escape`/`esc`=0x35, `forward_delete`=0x75, `home`=0x73, `end`=0x77, `page_up`=0x74, `page_down`=0x79, `left`=0x7B, `right`=0x7C, `down`=0x7D, `up`=0x7E, `keypad_enter`=0x4C, `f1`=0x7A, `f2`=0x78, `f3`=0x63, `f4`=0x76, `f5`=0x60, `f6`=0x61, `f7`=0x62, `f8`=0x64, `f9`=0x65, `f10`=0x6D, `f11`=0x67, `f12`=0x6F.
- Modifiers: `cmd`/`command` → `kCGEventFlagMaskCommand`; `ctrl`/`control` → `kCGEventFlagMaskControl`; `alt`/`option`/`opt` → `kCGEventFlagMaskAlternate`; `shift` → `kCGEventFlagMaskShift`.
- `parse_key_combo("cmd+shift+4") -> (keycode, flags)`: split on `+`, each part must be a modifier or key name, exactly one key required, OR the flags. Raises `ValueError` otherwise.

### 6.5 `gateway/executor/session.py`

`SessionStore`: `dict[session_id, {"actions": {action_id: {action, expected_postcondition, record, observation, verdict}}}]`. `record_action(...) -> action_id` (uuid4 hex[:12]), `get_action`, `latest_action`. In-memory by design.

### 6.6 `gateway/executor/model_client.py` — the swappable `infer()` seam

- `ModelClientError(Exception)`; `UnsupportedModelAction(name, args)` (for `wait`/`finished`).
- `ModelClient(ABC)` with `infer(screenshot: Screenshot, instruction: str, history=None) -> Action` (returns normalized Action in **screen point** coordinates).
- **UI-TARS action space prompt** (OSWorld-style): click/left_double/right_single/drag/hotkey/type/scroll/wait/finished with `<|box_start|>(x,y)<|box_end|>` coordinates.
- **System template:** "You are a GUI agent. …## Output Format `Thought: ...` `Action: ...` … The task is: {instruction} Please give me the grounding action according to the last screenshot."
- **`smart_resize(height, width)`** replicates mlx-vlm's qwen2.5-vl processor exactly: `IMAGE_FACTOR=28, MIN_PIXELS=3136, MAX_PIXELS=12845056`; round h/w to multiples of 28, cap/floor by pixel budget with the same beta formulas.
- **Parsers:** `parse_model_output(text)` (strips `Action: ` prefix, takes first line, truncates at last `)`, `ast.parse` in eval mode, extracts kwargs of constant/str values); `parse_box(value)` (`<|box_start|>(x,y)<|box_end|>`, or `(x,y)`, or `[x,y]`).
- **`UiTarsActionNormalizer(image_w, image_h, display_w, display_h)`**: `to_screen(mx, my)` maps model(resized) coords → image pixels → screen points (`mx/resized_w*image_w`, then `/image_w*display_w`). `normalize(name, args, target_hint)` maps click→CLICK(point), left_double→DOUBLE_CLICK, right_single→RIGHT_CLICK, drag→DRAG(from,to), type→TYPE(text from `content`), hotkey→KEY(combo via `_normalize_hotkey`: split on spaces, alias arrowleft/arrowright/arrowup/arrowdown/space/esc→left/right/up/down/space/escape, join with `+`), scroll→SCROLL(delta by direction: down=(0,3) up=(0,-3) left=(-3,0) right=(3,0), optional point), wait/finished→`UnsupportedModelAction`.
- **`UiTarsMLXClient(model_id="mlx-community/UI-TARS-1.5-7B-4bit")`**: lazy `_ensure_loaded()` (`mlx_vlm.load` + `load_config`); **`_force_offline_if_cached()`**: if the model repo dir exists under `HF_HOME/hub/models--mlx-community--UI-TARS-1.5-7B-4bit`, set `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` (mlx-vlm hangs minutes probing the HF hub with a fully cached model — Roadblock R7). `warmup()` loads in a daemon thread at gateway start. `infer`: builds messages `[{role:user, content:[{type:image, url:ref}, {type:text, text:prompt}]}]`, `apply_chat_template(processor, config, messages, num_images=1)`, `generate(..., max_tokens=256, temperature=0.0)`, parses + normalizes.
- `get_client()` singleton; `_CLIENT` global (tests swap it directly).

### 6.7 `gateway/executor/verifier.py`

`GatewayVerifier(adapter)`: `verify(observation, expected, force_source=None)`. Resolves the app (observation's `active_app` matched against `list_running_apps()`, else `active_app()`), reads AX state, `best_available_source(state, prefer=force_source or "auto")`; if ACCESSIBILITY → `verify(expected, ax_text(state), source)`; else Vision OCR of `observation.screenshot_ref` via `adapter.ocr_screenshot` (duck-typed — the ABC doesn't declare it; missing file or missing method → "" → UNCERTAIN).

### 6.8 `gateway/mcp_server.py` — the server and the live loop

- `SETTLE_SECONDS` caps per action: CLICK/DOUBLE_CLICK/RIGHT_CLICK 1.2, DRAG 1.4, TYPE 1.0, KEY 0.8, SCROLL 0.8. These are **caps, not fixed sleeps** (adaptive settle, Roadblock R15).
- `LIVE = os.environ.get("CUA_GATEWAY_LIVE") == "1"`. Stub mode (default): hardcoded `gui.*` responses with `stub: true`; validates required fields.
- **`MASTER_PROMPT`** (the server's `instructions` field — delivered into opencode session context by MCP, verified working on opencode 1.18.18; canonical full text in `docs/03-Master-Prompt.md`, must be embedded verbatim). Contents: (1) Role: the only planner; gui./browser. tools are "hands", never collaborators; be suspicious if a tool response reads like it made a decision for you (second-planner failure mode). (2) Tool selection order: native terminal/fs first → browser.* for anything browser-reachable → gui.* for native desktop apps only; never reimplement a cheaper tool with a fancier one. (3) Launch apps with `open -a "Name"` via shell, never by clicking the CUA Control UI. (4) gui.observe returns TEXT (AX tree with frames in screen points; model may not see screenshots). (5) gui.act: pass `params.point` (frame center) for clicks — the fast path; `target_hint`-only grounding takes ~2 min; key combos `{"type":"key","params":{"key":"cmd+a"}}`; every act REQUIRES `expected_postcondition`; for `type` the gateway will not re-dispatch on failure (would duplicate text) — it re-verifies. (6) Before every act: state the expected postcondition — checkable, not vague. (7) Verification discipline: call observe/verify, read the verdict, recovery on fail/uncertain, say explicitly when unverifiable. (8) Recovery behavior: re-observe once, then alternate approach, then escalate with specifics; bounded budget. (9) Permission boundaries: deleting files, submitting forms, overwriting files, credentials/payments require explicit confirmation. (10) Honesty: final report distinguishes verified / attempted-but-unverified / not-attempted. (11) Reliability over autonomy: ask when genuinely uncertain.
- `ACT_TYPES = ["click","double_click","right_click","drag","type","key","scroll"]`; `POINT_TYPES = {CLICK, DOUBLE_CLICK, RIGHT_CLICK}`.
- **Tool schemas:**
  - `gui.observe`: `{session_id: string}`, optional `prefer_source: enum[auto,vision,accessibility] default auto`.
  - `gui.act`: `{session_id, action: {type: enum ACT_TYPES, target_hint: string, params: object}, expected_postcondition: string}` (all required; `recovery_budget: {max_attempts int min 1, max_seconds number min 1}` optional).
  - `gui.verify`: `{session_id, action_id}`.
- **`LiveGateway(adapter, logs_dir)`:** store = `SessionStore()`, verifier = `GatewayVerifier(adapter)`, `_model_client` warmed at startup (best-effort try/except).
  - `observe(session_id, prefer)`: capture `logs/observe.png`; `active_app()`; AX state (best-effort); `best_available_source`; tree trimmed by **`_trim_tree` with `MAX_TREE_NODES=400`** (DFS cap, truncation marker node `{"role":"…","title":"…",...}`, `tree_truncated` flag) — full macOS trees are tens of thousands of nodes and blow up text-only planner context; `running_apps` array of `{name, bundle_id, windows[]}`; returns `{screenshot_ref, accessibility_tree, active_app, source_used, running_apps, tree_truncated, note}`.
  - `act(session_id, action_data, expected_postcondition, recovery_budget)`: budget → `RecoveryTracker` → resolve action → **`_ground`**: only for POINT_TYPES with no `point` in params, via `model_client.infer` (raises `RuntimeError("grounding failed: ...")` on `ModelClientError`); record `_pre_dispatch_sha = adapter._last_sha`; append `action.dispatched` event. Loop while not exhausted: `tracker.advance()`; **re-dispatch only if `attempts == 1` or type not in {TYPE, KEY}`** (type/key are non-idempotent — re-verify, never re-dispatch, on retry); `adapter.dispatch`; `_settle(type, pre_dispatch_sha)`; `_observe_for_act()` (capture `logs/act.png`, active_app, extra dims); verify with `force_source="vision" if attempts >= 2 else None` (the §13 re-observe strategy); if `not record.dispatched` → replace result with FAIL `reason: "dispatch_failed"`; append `verification.result`; `next_strategy` → done/retry/reobserve, append `recovery.attempted`; on budget exhaustion with non-pass → append `recovery.exhausted`. Store action, return `{action_id, dispatched, raw_result, verdict, evidence, source_used, recovery: {attempts, strategies, escalated, budget}}`.
  - `_settle(action_type, pre_dispatch_sha)`: poll `adapter.screen_hash()` every 0.15 s; **return the instant the hash differs from `pre_dispatch_sha` (and isn't None)** — the action landed; otherwise run out the cap.
  - `verify(session_id, action_id)`: lookup in store; fresh `_observe_for_act` + verify; returns `{verdict, evidence, source_used}` or None (unknown action_id → error).
- Server: `mcp.server.lowlevel.Server("cua-gateway", version="0.1.0", instructions=MASTER_PROMPT)` with `list_tools`/`call_tool` handlers; tool calls run in `asyncio.to_thread`; results are `types.TextContent` with `json.dumps(result, indent=2)`. Stub mode when `_LIVE_GATEWAY is None`. `main()`: logging INFO, `asyncio.run(amain())` with `stdio_server()`.

---

## 7. Browser Adapter (`browser_adapter/`)

### 7.1 `pyproject.toml`

name `browser-adapter`, `requires-python = ">=3.14"`, deps: `browser-use>=0.13.8`, `mcp[cli]==1.26.0`; dev: `pytest`, `pytest-asyncio>=1.4.0`. Script `browser-adapter = "mcp_server:main"`. Same setuptools/pytest config pattern as the gateway.

### 7.2 `mcp_server.py`

- `SETTLE_SECONDS = {navigate: 1.5, click: 0.6, type: 0.8, extract: 0.4, scroll: 0.5, select: 0.8, key: 0.5}` (plain sleeps here — DOM reads are cheap).
- **`BROWSER_MASTER_PROMPT`** (subset of the gateway prompt, embedded verbatim): role (sole planner, browser.* are hands); prefer browser.* over gui.* for browser-hosted tasks; `expected_postcondition` required on every act; `browser.observe` returns an `elements` array with **1-based indexes** used by click/type/select/scroll; verification checks quoted text/URLs/named content actually on the page; verify before treating a step done; recovery discipline.
- `ACT_TYPES = ["navigate","click","type","extract","scroll","select","key"]`; tool schemas mirror `gui.*` exactly (`browser.observe` adds `include_screenshot: boolean default true`; `browser.act` same shape + `recovery_budget`).
- **`LiveBrowserAdapter(logs_dir, headless=True)`:** lazy `_ensure_session()` on first use — `BrowserSessionWrapper.start()` + `create_browser_session(headless)` → `BrowserDispatcher(session, controller)`. Headless from `BROWSER_HEADLESS` env (`"true"` default; non-"false" = headless).
  - `observe`: wrapper's DOM observation → `{screenshot_ref, accessibility_tree, active_app, source_used}`.
  - `act`: same RecoveryTracker loop as the gateway: budget → advance → `dispatcher.dispatch(action)` → `asyncio.sleep(SETTLE_SECONDS[type])` → `_verify_action` → log events → strategy ladder → store → return same shape as gateway (`action_id, dispatched, raw_result, verdict, evidence, source_used, recovery`).
  - `verify(session_id, action_id)`: rebuild action/record from store, `_verify_action` again.
  - **`_verify_action(action, record, expected_postcondition)` — action-type-aware:**
    - `not record.dispatched` → always FAIL (`reason: "dispatch_failed"`).
    - `navigate`: `verify_text(dom_text, expected)`; if not pass, only fall back to "the action URL is in the DOM" when the postcondition **names no URL/domain of its own** (a wrong-URL postcondition like "on wikipedia.org" while on example.com must FAIL even though the navigate action succeeded — Roadblock R3).
    - `extract`: dispatch success + non-empty `matches` → PASS (the evidence IS the extracted data).
    - `type`: typed text present in `dom_text` → PASS; else read the element's live value via `dispatcher.read_element_value(index)` (XPath evaluation of `el.value ?? el.textContent` — DOM text does not include input values), then substring or fragment-verify.
    - `click`/`scroll`/`select`/`key`: fragment-verify against DOM text.
    - Evidence `actual_text` truncated to 400 chars; `source_used: "dom"` always.
- Server bootstrap identical pattern to the gateway (server name `"browser-adapter"`, `instructions=BROWSER_MASTER_PROMPT`, stdio).

### 7.3 `action_dispatch.py`

- `BrowserDispatcher(browser_session, controller)`: `_model()` = `controller.registry.create_action_model()` (lazy). `dispatch(action)` maps:
  - `navigate`: `{"url": params.url or target_hint, "new_tab": bool}` → `controller.act(model(navigate=...), browser_session=...)`; raw_result `{navigated_to, result}`.
  - `click`: requires `params.index`; **`index < 1 → clamped to 1`** (browser-use 0.13.8 click indexes are 1-based `ge=1`; planner 0-based first element must be tolerated — Roadblock R5); raw_result `{clicked_index, result}`.
  - `type`: requires `index`; `{input: {index, text, clear: default true}}`; raw_result `{typed_index, text, result}`.
  - `extract`: **deterministic DOM extraction, no extraction LLM** (browser-use's stock extract errors with "Action extract requires page_extraction_llm" — Roadblock R6). `query = params.query or params.text or target_hint`. Runs in-page JS via `page.evaluate`: try `querySelectorAll(query)` (catch → empty); if no matches, filter `h1,h2,h3,h4,p,li,a,button,span,td,th,div,img` by `textContent.toLowerCase().includes(query.toLowerCase())`; cap 20 elements; per element `{text: trimmed textContent, whitespace-collapsed, ≤2000 chars}` plus optional `href`/`src` when `extract_links`/`extract_images` set. Returns `{query, matches}` (JSON-parsed).
  - `scroll`: `{down: default true, pages: default 1.0, index?}`.
  - `select`: requires `index` + `option` → `{select_dropdown: {index, text: option}}`.
  - `key`: requires `params.keys` → `{send_keys: {keys}}`.
  - Any exception → `ActionRecord(dispatched=False, raw_result={error: str})` (never raises).
  - `read_element_value(index)`: `session.get_browser_state_summary()` → `state.dom_state.selector_map.get(index)` → element's XPath → `page.evaluate` `el.value ?? el.textContent` ("" when missing/frame-crossed).
- `create_browser_session(headless=True, viewport={"width":1280,"height":720})` → `(BrowserSession(browser_profile=BrowserProfile(headless, viewport)), Controller())`.

### 7.4 `session_wrapper.py`

- `BrowserSessionWrapper(headless=True, viewport=1280x720)`: `start()` (BrowserProfile + `session.start()`, idempotent), `stop()`, async context manager.
- `observe() -> Observation`: `get_browser_state_summary()`; `accessibility_tree = {url, title, dom_text (first 5000 chars), elements (indexed), tabs: [{url,title,target_id}], focused_element or page_info pixels}`; `source_used = "dom"`; `active_app = "browser"`; `extra = {url, title, viewport dims}`; screenshot via `_take_screenshot()` → `/tmp/browser_screenshot_<ms>.png`.
- **`_indexed_elements(state)`**: iterate `state.dom_state.selector_map` → `{index (the map key — 1-based), tag_name, text (get_all_children_text, ≤200 chars), attributes}`.
- **`_dom_to_text(state)`**: parts joined with `" | "`: `URL: <url>`, `Title: <title>`, then per element: tag_name, `id=`, `class=`, `placeholder=`, `type=`, `value=`, and children text. This is the verification evidence.
- `get_dom_text()`: fresh state → `_dom_to_text`.

---

## 8. Control Server (`frontend/server/`)

Zero npm runtime dependencies (node:http, node:fs, node:path, node:crypto, node:child_process, node:events only).

### 8.1 `index.js`

- Constants: `REPO_ROOT = <repo root>` (resolve `..`/`..` from `server/`), `DIST_DIR = frontend/dist`, `LOGS_DIR = <repo root>/logs`, `CONTROL_PORT = PORT env or 8787`, `OPENCODE_PORT = OPENCODE_PORT env or 49152`. MIME map for static serving; SPA fallback to `index.html`.
- **State:** `{opencode, logs, activeSession, sessionStatus, mcp, providers, models, defaultModel, autoApprove: true, autoApproveTypes: Set(["bash","edit"]), lastScreen, screenInterval, appsRunning, startedAt, screenCaptureEnabled: true, screenPermissionDenied: false}`.
- **SSE hub:** `clients: Set` of response objects; `broadcast(event)` writes `data: <json>\n\n`; `isBusy()` = active session's status type === "busy".
- **opencode lifecycle:** `ensureOpencode()` — create `OpencodeServer({repoRoot, port: CONTROL_PORT})`, wire `event` → `handleOpencodeEvent`, `log` → broadcast `opencode.log`; `start({port: OPENCODE_PORT})`; then `refreshMeta()`. On failure, null out and rethrow.
- **`refreshMeta()`:** fetch `mcpStatus()`, `sessionStatus()`, `listProviders()`; build `models` from connected providers (skip `deprecated`/`experimental`); **default model preference: `["deepseek-v4-flash-free"]` first, then provider default (`def.opencode`), then first model** (Roadblock R12). Broadcast `meta.updated`.
- **`handleOpencodeEvent`:** `session.status` → store + broadcast; passthrough broadcast for `session.idle/created/updated/deleted/error`, `message.updated`, `message.part.updated/removed`, `message.removed`, `todo.updated`, `file.edited`, `session.diff`, `permission.replied`; `permission.updated` → broadcast + `maybeAutoApprove`; ignore `server.connected`, `installation.updated`; default → broadcast.
- **`maybeAutoApprove(perm)`:** if autoApprove on AND (`perm.type in {"bash","edit"}` OR `perm.type.startsWith("bash:")`) → `opencode.replyPermission(sessionID, id, "allow", false)` and broadcast `permission.auto`.
- **Screen loop (M11):** `captureTick()` — if `screenCaptureEnabled` and clients exist: `screen.capture()`; if `shot.permission` → **stopScreenLoop(), `screenPermissionDenied = true`, broadcast `screen.permission {denied: true, error}`**; if ok and `at` changed → `screen.updated`; else `screen.error`. `startScreenLoop()` resets permission state and polls at **`isBusy() ? 1200 : 2500` ms** (unref'd); `stopScreenLoop()` clears.
- **HTTP plumbing:** `sendJson`, `readBody` (2 MB cap), `serveStatic` (path-traversal guard via `startsWith(DIST_DIR)`).
- **`metaPayload()`:** `{healthy, version, opencodeUrl, mcp, models, defaultModel, autoApprove, busy, screen: screen.lastCaptureInfo(), screenPermissionDenied, logsDir, startedAt, gatewayLogs: logs.snapshot()}`.
- **Routes (all `127.0.0.1:8787`):**
  - `GET /api/status` → 200 `metaPayload()`.
  - `GET /api/event` → SSE: writes `hello` event with metaPayload, adds client, `startScreenLoop()`; on close, remove client, stop loop if empty. Heartbeat `ping` every 20 s.
  - `GET /api/apps` → `{apps: APPS with running flags}`; `POST /api/apps/launch` `{name, path?}` → 400 missing name, `apps.launch`, broadcast `app.launched`, 200/500.
  - `GET /api/screen.jpg` → 409 `{error}` if `!screenCaptureEnabled` ("screen capture stopped"); 409 if `screenPermissionDenied` ("screen recording permission needed — enable Screen Recording for CUA Control (and Terminal/python if listed) in System Settings"); else capture, stream JPEG with `X-Capture-At` header, 500 on failure.
  - `GET /api/gateway-screenshot/<name>` → file from LOGS_DIR (basename + png/jpg/webp regex guard), 404 if missing.
  - `GET /api/logs` → `logs.snapshot()`.
  - `POST /api/stop` → `stopScreenLoop()`, `screenCaptureEnabled=false`, `activeSession=null`, `opencode.stop()`, `opencode=null`, broadcast `meta.updated`, 200 `{ok:true, stopped:true}`.
  - `POST /api/start` → `screenCaptureEnabled=true`, `startScreenLoop()`, `ensureOpencode()`, broadcast, 200 `{ok:true, healthy:true}` / 500.
  - Guards: any opencode route while `!state.opencode?.running` → 503 "opencode server not running — retry in a moment".
  - `GET/POST /api/sessions`, `GET/DELETE /api/session/<id>`, `POST /api/session/<id>/task` (`{text, model?}`; model without `modelID` dropped; sets `activeSession = {id, title: text.slice(0,60)}`; `promptAsync`; 202; broadcast `task.started`), `POST /api/session/<id>/abort`, `GET /api/session/<id>/messages`, `POST /api/session/<id>/permissions/<permId>` (`{response: allow|deny, remember}`).
  - `POST /api/settings` `{autoApprove?, screenCaptureEnabled?}` — enabling capture clears `screenPermissionDenied`, `screen.resetPermission()`, starts loop; disabling stops it; broadcasts `meta.updated`; 200 `{ok, autoApprove}`.
  - else 404.
- **`main()`:** mkdir logs; `GatewayLogs` wired to broadcast `gateway.log` + `gateway.screenshots`; heartbeat interval; `server.listen(CONTROL_PORT, "127.0.0.1")`; on listen → `ensureOpencode()` (log version/url); SIGINT/SIGTERM → stop logs + opencode, close server, exit (3 s hard timeout).

### 8.2 `opencode.js` — `OpencodeServer` (EventEmitter)

- `OPENCODE_BIN = env OPENCODE_BIN || "opencode"`.
- **`start({port})`:** spawn `opencode serve --port <port> --hostname 127.0.0.1 --cors http://localhost:<controlPort> --print-logs`, `cwd = repoRoot`, env inherited, stdio `["ignore","pipe","pipe"]`. **CRITICAL: no `detached`** — the opencode process must stay in the control server's process tree so macOS TCC attributes its children (gateway python, screencapture) to the same responsible app; detaching broke that chain and caused the permission-prompt storm (Roadblock R13). stdout/stderr → `log` events. `error` event → emit log, null proc, `_scheduleRestart()`. `exit` → emit log + `exit`, null proc, abort SSE, `_scheduleRestart()`.
- **`_scheduleRestart()`:** attempts += 1; **cap at 5 — after 5, "giving up auto-restart after 5 attempts — press START"** and stand down; else retry in `min(2000 * attempts, 10_000)` ms.
- **`stop()`:** clear retry timer, `_manuallyStopped = true`, null proc, abort SSE; if proc: removeAllListeners("exit"), `SIGTERM`; then `spawn("/usr/bin/pkill", ["-9","-f", pattern])` for **each of `cua-gateway` and `browser-adapter`** (MCP children do not die with opencode); 800 ms later `SIGKILL` the proc (escalation so STOP is instant).
- `_waitHealthy(30_000)`: poll `GET /global/health` (4 s timeout each, 500 ms apart).
- `_startEventStream()`: `fetch /event` with AbortController, line-based SSE reader (`data:` lines), emits `event`; reconnect on error with backoff (2 s base); abort on stop.
- `_fetch(path, {method, body, timeoutMs})`: JSON in/out; error → throw with `method path -> status: detail` from `json.data.message`.
- API wrappers: `listSessions` (GET /session), `createSession` (POST /session `{title?}`), `getSession`, `deleteSession`, `sessionStatus` (GET /session/status), `listMessages(id, limit=200)`, `promptAsync(id, {text, model: {providerID, modelID}|null})` → POST `/session/<id>/prompt_async` with `{model, parts:[{type:"text", text}], agent?}` (15 s timeout), `abort` (POST /session/<id>/abort), `replyPermission(sessionId, permId, response, remember)`, `listProviders` (GET /provider), `mcpStatus` (GET /mcp).

### 8.3 `screen.js` — capture pipeline + permission latch

- Work dir: `os.tmpdir()/cua-control` (created). Output: `screen.jpg`.
- **`capture()`** (single-inflight):
  1. If `permissionDenied` latched → return `{...lastCapture, path, permission: true}` immediately (no TCC hammering).
  2. `/usr/sbin/screencapture -x <raw.png>`; fail if error, no file, or 0 bytes.
  3. `/usr/bin/sips -s format jpeg -s formatOptions 62 -Z 1500 <raw> --out screen-full.jpg` → rename to `screen.jpg`. (Downscaled to ≤1500px, JPEG quality 62.)
  4. On any error → **fallback `captureViaGateway()`**: run `gateway/.venv/bin/python capture_fallback.py <raw-gw.png>` (20 s timeout) — the venv interpreter is the process with the TCC grants — then sips-resize the same way.
  5. On still-error → `consecutiveFailures += 1`; **at ≥2 consecutive failures, latch `permissionDenied = true`** (the control server then stops polling and the UI shows the settings banner — Roadblock R13). On success → reset counter + latch.
  6. Cleanup raw files; return `{...lastCapture, path, permission}`.
- `lastCapture = {at, bytes, ok, error}`; `screenFile()`; `lastCaptureInfo()` (+`exists`); `resetPermission()` clears latch + counter; `permissionStatus()`.
- `capture_fallback.py <output.png>`: `sys.path.insert(0, repo root)`, `MacOSAdapter().capture_screen(out)`, prints `captured <ref> WxH`.

### 8.4 `apps.js`

Catalog of 30+ macOS apps `{name, proc (pgrep -x process name), icon, desc}` (TextEdit, Calculator, Notes, Calendar, Reminders, Safari, Chrome, Finder, Terminal, Mail, Messages, Preview, Music, Photos, Maps, Stocks, Weather, Podcasts, Numbers, Pages, Keynote, Freeform, VSCode(proc: Code), iTerm2, Slack, Discord, Zoom(proc: zoom.us), Spotify, Notion, System Settings, Activity Monitor). Dedupe by `name|proc`. `isRunning(proc)` via `/usr/bin/pgrep -x <proc>` (3 s); `runningStatus()` parallel for all; `launch(name, {path})` via `/usr/bin/open -a <name>` (or open path), 15 s; `activate = launch`.

### 8.5 `logs.js` — `GatewayLogs`

- Watches `logs/` for `session-*.jsonl` files (fs.watch + 1.5 s poll fallback). `_scanFile`: incremental tail from last size (rotation-safe: size shrink → reset); each parsed line emits `append {file, event}`.
- `_scanScreenshots`: mtime+size for `observe.png`/`act.png` with URLs `/api/gateway-screenshot/<name>?v=<mtime>`; emits `screenshots` on change.
- `snapshot()` → `{files: {name: [events]}, screenshots}`.

### 8.6 Frontend build

`package.json`: scripts `build: vite build`, `dev: vite web`, `start: node server/index.js`, `test:smoke: node scripts/smoke-test.js`; deps `react@^18.3.1`, `react-dom@^18.3.1`; devDeps `@vitejs/plugin-react@^4.3.4`, `vite@^6.0.0`. `vite.config.js`: root `web`, outDir `../dist`, `emptyOutDir`, target es2022, dev server port 5173 with `/api` proxy → `http://localhost:8787`.

---

## 9. Web UI (`frontend/web/src/`)

React 18 SPA, dark theme, three columns. State managed by a single `useReducer` in `App.jsx` fed by an EventSource on `/api/event` (ignores `ping`/`hello`; dispatches `{type: event.type, properties}`). Polls: apps every 8 s, sessions every 15 s; loads messages when a session is selected.

- **`lib/api.js`:** `j(url, opts)` JSON helper (throws `Error(body.error || status)`); API surface: `status, apps, launchApp, sessions, createSession, session, messages, task, abort, deleteSession, replyPermission, logs, settings, stop, start`; `fmtTime`, `fmtClock`, `prettyJson`.
- **`App.jsx` reducer:** merges `message.updated`/`message.part.updated` (text/reasoning deltas accumulate); `gateway.log` appends to per-file arrays; `screen.updated` sets `screenAt`; `screen.permission` sets `screenPermissionDenied`; `meta.updated`/`hello` pull `meta`, `busy`, `gatewayLogs`, `gatewayShots`, `screenPermissionDenied`; `permission.updated` queues permission modal; `task.started` sets busy + active session; `session.status` busy logic; `toasts` (cap 7) for errors/auto-approves/app launches. Actions: `runTask` (creates session if none, `api.task`, reload messages), `replyPermission`, `toggleAutoApprove`, `toggleScreen`, `stopEverything` (api.stop → screen off), `startEverything` (api.start → screen on). MCP health: `gatewayOk = mcp["cua-gateway"]?.status === "connected"`, same for browser.
- **`StatusBar.jsx`:** brand; connection dot ("running"/"stopped"/"reconnecting…"); opencode version; `MCPChip` per server (`connected`/`disabled`/`failed`/warn colors); RUNNING badge when busy; 📷 screen toggle; **⏹ STOP (red) when healthy, ▶ START (green) when not** — both disabled while `lifecycleBusy`, STOP title: "Stop opencode and screen capture NOW (kills the whole process group instantly)".
- **`LiveScreen.jsx`:** polls `/api/screen.jpg?t=<ts>` every `busy ? 1300 : 2600` ms (skips when disabled or permissionDenied); expand/shrink; **permission banner when `permissionDenied`**: "Screen Recording permission needed", instructions to enable in System Settings (CUA Control, Terminal, Python or node), **"Open System Settings" anchor with `href="x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"`** (the native app routes this scheme to NSWorkspace — §10.1), and a "Retry capture" button (`onRetry` → re-enable capture); grounding strip shows `observe.png`/`act.png` from `gateway.screenshots`; "captured <time> · agent active".
- **`ActivityFeed.jsx`:** messages sorted by created time; part renderers: text, reasoning (💭), tool cards (✓/✕/…/○ status; verdict badge from parsed JSON output `parsed.verdict`; `recovery ×N` badge when `recovery.attempts > 1`; "not dispatched" badge; input/output JSON, 4000-char cap), step-start, agent, subtask, file, patch, snapshot, retry (⚠), compaction.
- **`TaskComposer.jsx`:** textarea (⌘/Ctrl+Enter to submit), model `<select>` from `providerID/modelID` options (default from `meta.defaultModel`), **auto-approve checkbox**, Run task button (label shows model short name), Stop (abort) button while busy.
- **`GatewayLog.jsx`:** gateway JSONL viewer — rows for `action.dispatched`/`verification.result`/`recovery.*` with verdict badges (`pass`/`fail`/`uncertain`), attempt/strategy chips, expandable JSON (evidence truncated 600 chars); auto-stick to bottom.
- **`SessionsList.jsx`:** session list, select/new, two-click delete.
- **`AppLauncher.jsx`:** grid of app tiles with running dots, disabled while a launch is in flight; custom app-name input.
- **`PermissionModal.jsx`:** full-screen backdrop; per pending permission: type chip, pattern, title, **Deny / Allow** buttons.
- `styles.css`: dark theme, `.btn-stop` red / `.btn-start` green, chips, toasts, screen panel, log rows, responsive min sizes.

---

## 10. Native macOS app (`native/`)

### 10.1 `main.swift`

AppKit app with a WKWebView. On `applicationDidFinishLaunching`: setup window (1320×860, min 1000×640, title "CUA Control"), find repo root, start services, load UI, poll status every 2 s.

- **Repo discovery:** `CUA_PLATFORM_ROOT` env override (must contain marker), else walk up from `Bundle.main.bundleURL` up to 8 levels looking for `frontend/server/index.js`.
- **Node binary candidates:** `/opt/homebrew/bin/node`, `/usr/local/bin/node`, `/usr/bin/node`, else `/usr/bin/env node`.
- **`serviceEnv()`:** inherits env, sets `PATH = ~/.opencode/bin:~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` (opencode lives in `~/.opencode/bin`; uv in `~/.local/bin`) and `PORT=8787`.
- **`startServices()`:** `checkStatus` (GET `/api/status`, 3 s, `healthy` field) — if a control server is already up, adopt it (status "running", load UI); else `spawnControlServer()`: `node server/index.js`, `cwd = <repoRoot>/frontend`, env as above; **stdout+stderr piped and appended to `<repoRoot>/logs/control-server.out`** (FileHandle append, readabilityHandlers) with a `===== CUA app launched <date> =====` header; `terminationHandler` → status "control server exited (code N)".
- **Toolbar:** status field (secondary label color), **⏹ STOP** (red), **▶ START** (green). STOP: disable buttons, `POST /api/stop` (20 s), on failure belt-and-suspenders `pkill -9 -f "opencode serve"` + `pkill -9 -f "cua_gateway"`; status "stopped", enable START. START: `POST /api/start`, on ok reload UI.
- **Status loop:** every 2 s `checkStatus`; healthy → "running", STOP enabled, START disabled, `loadUI()` (once).
- **`loadUI()`:** `webView.load(http://127.0.0.1:8787)` with `websiteDataStore = .nonPersistent()`; `didFailProvisionalNavigation` → retry after 2 s (server still booting).
- **`decidePolicyFor`:** navigation actions with scheme `x-apple.systempreferences` → `NSWorkspace.shared.open(url)` + `.cancel` (lets the UI's "Open System Settings" button work from inside the WKWebView); everything else `.allow`.
- **`cleanupServices()`** (on quit): terminate control task, SIGKILL after 2.5 s, plus `pkill -9 -f` for `"opencode serve"`, `"cua_gateway"`, `"browser-adapter"`.
- `applicationShouldTerminateAfterLastWindowClosed` → true; `presentFatal` NSAlert + terminate when repo not found.

### 10.2 `Info.plist`

Bundle id `ai.opencode.cua-control`, name "CUA Control", `CFBundleExecutable` = `CUA`, `CFBundlePackageType APPL`, `LSMinimumSystemVersion 12.0`, category `public.app-category.productivity`, `NSHighResolutionCapable`, `NSPrincipalClass NSApplication`, and **`NSAppTransportSecurity → NSAllowsLocalNetworking = true`** (needed for the WKWebView to load `http://127.0.0.1:8787`).

### 10.3 `build.sh`

`rm -rf build`, mkdir `build/CUA.app/Contents/{MacOS,Resources}`, copy Info.plist, `swiftc -O -o build/CUA.app/Contents/MacOS/CUA main.swift`, then **`codesign --force --deep --sign -` (ad-hoc)**. See Roadblock R14 for why ad-hoc (no keychain identity).

---

## 11. Configuration files

### `opencode.json` (repo root — what opencode serve reads; keep `config/mcp.json` in sync)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cua-gateway": {
      "type": "local",
      "command": ["uv", "run", "--project", "gateway", "cua-gateway"],
      "cwd": ".",
      "environment": { "CUA_GATEWAY_LIVE": "1" },
      "timeout": 600000,
      "enabled": true
    },
    "browser-adapter": {
      "type": "local",
      "command": ["uv", "run", "--project", "browser_adapter", "browser-adapter"],
      "cwd": ".",
      "environment": { "BROWSER_HEADLESS": "true" },
      "timeout": 300000,
      "enabled": true
    }
  }
}
```

Notes: `cwd: "."` matters — opencode serve runs with cwd = repo root, so the gateway's `sys.path.insert` for `shared/` and the `logs/` directory resolution both work. `CUA_GATEWAY_LIVE=1` switches the gateway from stub to live mode. `config/mcp.json` contains only the gateway entry (the browser entry was added to `opencode.json` later — they are now kept in sync per the README).

### `start.sh`

`set -euo pipefail`; `cd "$(dirname "$0")"`; `PORT=${PORT:-8787}`; `--build-only` → `(cd frontend && npm run build)`; if `frontend/dist/index.html` missing → `npm install && npm run build`; unless `--no-open`, `open http://localhost:${PORT}` after 2 s; finally `cd frontend && exec node server/index.js`.

### `.gitignore`

`.venv/`, `frontend/node_modules/`, `frontend/dist/`, `native/build/`, plus standard Python/node entries and `logs/`.

---

## 12. Control-server API reference (complete)

| Method | Path | Request | Success | Notes |
|---|---|---|---|---|
| GET | `/api/status` | – | 200 `metaPayload()` | `healthy, version, opencodeUrl, mcp, models, defaultModel, autoApprove, busy, screen, screenPermissionDenied, logsDir, startedAt, gatewayLogs` |
| GET | `/api/event` | – | SSE | `hello` (metaPayload) first; then `ping`/20 s, `opencode.log`, `screen.updated`, `screen.error`, `screen.permission`, `gateway.log`, `gateway.screenshots`, `meta.updated`, `session.*`, `message.*`, `todo.updated`, `file.edited`, `session.diff`, `permission.*`, `task.started`, `app.launched` |
| GET | `/api/apps` | – | `{apps:[{name,proc,icon,desc,running}]}` | running via pgrep |
| POST | `/api/apps/launch` | `{name, path?}` | 200 `{ok:true}` | 400 missing name; 500 on failure |
| GET | `/api/screen.jpg` | – | JPEG stream, `X-Capture-At` | 409 stopped; 409 permission-denied (with actionable message); 500 no capture |
| GET | `/api/gateway-screenshot/<name>` | – | PNG/JPEG | basename guard; 404 missing |
| GET | `/api/logs` | – | `{files, screenshots}` | |
| POST | `/api/stop` | `{}` | `{ok:true, stopped:true}` | halts screen loop + kills opencode + MCP children |
| POST | `/api/start` | `{}` | `{ok:true, healthy:true}` | restores everything |
| GET | `/api/sessions` | – | list | 503 when opencode down |
| POST | `/api/sessions` | `{title?}` | created session | |
| GET | `/api/session/<id>` | – | session | |
| DELETE | `/api/session/<id>` | – | `{ok:true}` | |
| POST | `/api/session/<id>/task` | `{text, model?}` | 202 `{ok:true}` | 400 missing text; fires `prompt_async` |
| POST | `/api/session/<id>/abort` | `{}` | `{ok:true}` | |
| GET | `/api/session/<id>/messages` | – | message list (limit 200) | |
| POST | `/api/session/<id>/permissions/<pid>` | `{response: allow\|deny, remember}` | `{ok:true}` | |
| POST | `/api/settings` | `{autoApprove?, screenCaptureEnabled?}` | `{ok, autoApprove}` | re-enabling capture resets permission latch + restarts loop |

---

## 13. MCP tool contract reference

Both servers: `Server(name, version="0.1.0", instructions=<prompt>)`, `mcp.server.lowlevel`, stdio transport. All tool results are `types.TextContent` with `json.dumps(..., indent=2)`.

| Tool | Input | Output |
|---|---|---|
| `gui.observe` | `{session_id: string, prefer_source?: auto\|vision\|accessibility}` | `{screenshot_ref, accessibility_tree\|null, active_app, source_used, running_apps:[{name,bundle_id,windows}], tree_truncated, note}` |
| `gui.act` | `{session_id, action:{type: enum(click,double_click,right_click,drag,type,key,scroll), target_hint, params}, expected_postcondition: string, recovery_budget?: {max_attempts, max_seconds}}` | `{action_id, dispatched, raw_result, verdict: pass\|fail\|uncertain, evidence, source_used, recovery:{attempts, strategies:[retry\|reobserve\|escalate\|done], escalated, budget}}` |
| `gui.verify` | `{session_id, action_id}` | `{verdict, evidence, source_used}` (error on unknown action_id) |
| `browser.observe` | `{session_id, include_screenshot?: bool=true}` | `{screenshot_ref, accessibility_tree:{url,title,dom_text,elements:[{index(1-based),tag_name,text,attributes}],tabs,focused_element}, active_app:"browser", source_used:"dom"}` |
| `browser.act` | same shape as gui.act, `type: enum(navigate,click,type,extract,scroll,select,key)` | same output shape as gui.act |
| `browser.verify` | `{session_id, action_id}` | `{verdict, evidence, source_used}` |

`expected_postcondition` is REQUIRED by both act tools (rejected with `{error: "missing required field: expected_postcondition"}` otherwise). `params` accepted by the browser adapter: navigate `{url, new_tab?}`; click/type `{index (1-based), text?, clear?}`; extract `{query|text, extract_links?, extract_images?}`; scroll `{down?, pages?, index?}`; select `{index, option}`; key `{keys}`.

---

## 14. Session log format (`logs/session-<id>.jsonl`)

One JSON object per line, always `{event, session_id, worker: "gateway"|"browser_adapter", timestamp: "%Y-%m-%dT%H:%M:%S%z", ...}`. Event types: `action.dispatched` (`action` dict, `expected_postcondition`), `verification.result` (`attempt, verdict, source_used, evidence`), `recovery.attempted` (`attempt, strategy, verdict`), `recovery.exhausted` (`attempts`). Plus screenshots `observe.png`/`act.png` written by the gateway. The control server tails these files and streams them to the UI as `gateway.log` events.

---

## 15. Verification & testing

### 15.1 Behavior that must be recreated and held invariant

1. No `expected_postcondition` → act tool error. 2. Dispatch failure → verdict always `fail` (`reason: "dispatch_failed"`), never a pass. 3. Type/key actions never re-dispatched on retry (text duplication). 4. Wrong-URL postconditions fail even when navigation succeeded. 5. Quoted strings in postconditions are mandatory evidence. 6. `gui.act` with `params.point` never invokes the model. 7. Screen-content hash short-circuits settle waits. 8. Screen loop stops polling after 2 consecutive capture failures. 9. opencode auto-restart caps at 5. 10. `opencode serve` spawned without `detached`. 11. STOP kills opencode + both MCP children and returns 409 for screen. 12. Default model preference `deepseek-v4-flash-free` > provider default.

### 15.2 Unit tests (89, no live desktop needed)

From `gateway/`: `PYTHONPATH=.. uv run pytest ../tests/unit -q`. Suites: `test_verifier.py` (fragment extraction, verdict rules, anchor detection, ax_text flattening, source selection, result assembly — including real-session regression cases like "the browser is on example.com"), `test_gateway_verifier.py` (FakeAdapter-based GatewayVerifier + LiveGateway: act-and-verify pass/fail, stale postcondition → 3-attempt escalate with `["retry","reobserve","escalate"]`, gui.verify re-check, grounding stub injection, type skips grounding), `test_recovery.py` (strategy ladder, budgets, JSONL append), `test_action_schema.py`, `test_gateway_stub.py` (stub mode contract), `test_model_client.py` (UI-TARS parser/normalizer incl. smart_resize mapping), `test_os_adapter_base.py` (ABC + tripwire that platform coupling stays in `os_adapter/`).

### 15.3 Smoke test (11/11)

`cd frontend && node scripts/smoke-test.js`. Spawns the control server on scratch ports (`SMOKE_PORT` 18991, `SMOKE_OPENCODE_PORT` 18992), then: control server healthy; opencode spawned at expected URL; MCP `cua-gateway` connected; SSE `hello`; session create; `prompt_async` task accepted (202) and completes with an assistant `finish`; `/api/screen.jpg` 200 with bytes; apps catalog >10; MCP names present; `/api/logs` 200. Prints PASS/FAIL lines and `N/11 checks passed`; non-zero exit on failure.

### 15.4 Live acceptance (manual, documented results)

"Open Calculator, type 7+8, report the display" completes with a screen-grounded answer within 3 minutes on `deepseek-v4-flash-free`. "Open Spotify, search for Dum Maro Dum, play first result" completes (~420 s pre-speed-work). Full gateway timing targets after M8: unchanged-screen observe ≤ ~311 ms; full act cycle ~0.8–1.0 s.

---

## 16. Runbook

**Build & run (all paths relative to repo root):**

```sh
# 1. deps
cd gateway && uv sync && cd ..
cd browser_adapter && uv sync && cd ..
cd frontend && npm install && npm run build && cd ..
# 2. run
./start.sh                      # or: cd frontend && node server/index.js
# 3. native app
cd native && ./build.sh         # -> native/build/CUA.app (double-click)
```

**Verify:** `cd gateway && PYTHONPATH=.. uv run pytest ../tests/unit -q` → 89 passed. `cd frontend && node scripts/smoke-test.js` → 11/11. UI: `open http://localhost:8787` (or CUA.app). MCP tools present in session when `opencode.json` at repo root registers both servers and `opencode serve` runs with cwd = repo root.

**STOP:** POST `/api/stop`, UI STOP button, or native toolbar STOP — kills opencode SIGTERM→SIGKILL + `pkill -9 -f cua-gateway|browser-adapter`, halts screen loop (`/api/screen.jpg` → 409). **START:** POST `/api/start` (or START button) — restores in <2 s. Quitting CUA.app stops all services; logs persist in `logs/control-server.out`.

**Troubleshooting (condensed; full text in README):**
- Recurring TCC prompts → grant Screen Recording + Accessibility to the responsible app (CUA Control / Terminal / node / Python) once per build; UI banner + Retry handles it. Rebuild = new bundle identity = re-grant.
- Screen shows wallpaper only → venv python lost Screen Recording (e.g. after venv rebuild); re-add the interpreter path in System Settings.
- Model produces empty sessions → ensure default model resolves to `deepseek-v4-flash-free`.
- `gui.*` tools unavailable → opencode serve must run with cwd = repo root; `mcp["cua-gateway"].status === "connected"` in `/api/status`.
- Grounding hangs on first `gui.act` → pre-download the UI-TARS model to the HF cache (it then loads offline in ~16 s; cold download adds 10+ min).

---

## 17. Roadblocks & solutions (chronological — the "how it actually went" record)

**R1 — Stale frontmost app (M7).** `NSWorkspace.frontmostApplication()` returned apps that had been frontmost minutes ago; in a long-lived MCP process with no run loop, workspace activation notifications never arrive, so the cache never updates. The planner therefore observed/clicked the wrong app. *Solution:* live `CGWindowListCopyWindowInfo` query (windows front-to-back; layer 0; ≥50px; blacklist Window Server/WindowManager/Control Center/Dock/SystemUIServer/loginwindow/Spotlight/TextInputMenuAgent), NSWorkspace kept only as fallback. Verified: adapter tracked Calculator/Spotify/TextEdit exactly matching System Events.

**R2 — pyobjc constant names (M7).** Only `k`-prefixed names exist at top level of `Quartz`/`ApplicationServices` (`kCGWindowListExcludeDesktopElements`, `kCGWindowListOptionOnScreenOnly`, `kCGNullWindowID`). Non-prefixed aliases do not exist — import the k-forms.

**R3 — Verification false negatives (M7, the #1 failure source).** Session logs showed 73% of actions failing (browser 100%, gateway ~50%) although the actions worked: the verifier substring-matched the planner's whole sentence against evidence ("the browser is on example.com" ⊄ "URL: https://example.com/"). *Solution:* `shared/verifier.py` fragment extraction (quotes mandatory; ≥50% of anchors; URLs/domains/proper nouns/digits; strict-token fallback for prose; `UNCERTAIN` on empty evidence). Plus action-type-aware browser verification: navigate URL-fallback only when the postcondition names no URL/domain (wrong-URL must fail); extract non-empty matches = success; type verified against live input value via XPath (input values are invisible in DOM text).

**R4 — browser-use 0.13.8 API breakage (M6).** Stock docs/older examples referenced removed APIs. The adapter was rewritten for 0.13.8: `dom_state.selector_map`, `element.get_all_children_text()`, `controller.registry.create_action_model()` dispatcher pattern (`am = model()(click={...}); controller.act(am, browser_session=...)`).

**R5 — 0-based click index rejected (M7).** browser-use click/input indexes are 1-based (`ge=1`); planners pass index 0 for the first element, and the validation errored — clicks were impossible, so the planner only ever navigated/extracted. *Solution:* clamp `index < 1 → 1` in `_click`; elements exposed as 1-based `index` in `browser.observe`.

**R6 — extract requires an extraction LLM (M6).** browser-use's stock `extract` tool errors with "Action extract requires page_extraction_llm" when no extraction model is configured. *Solution:* deterministic in-page JS extract (CSS selector first, text-content match fallback, cap 20, optional href/src).

**R7 — mlx-vlm hangs on HF hub with cached model (M6).** First `gui.act` (grounding) hung 10+ minutes probing the HF hub even though every file was cached. *Solution:* `_force_offline_if_cached()` — pin `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` when the repo dir exists in the HF cache; warmup thread at gateway start (cold: 10+ min; cached: ~16 s).

**R8 — Coordinate mapping for grounding (M2).** UI-TARS output is in the qwen2.5-vl `smart_resize`d image space, and Retina captures are 2× pixel density (3600×2338 image vs 1800×1169-point display). *Solution:* `smart_resize` replicated in-process (factor 28, min 3136, max 12845056) and verified against the runtime processor; `Screenshot` carries pixel dims + display dims; `UiTarsActionNormalizer.to_screen` maps model→image→screen points. Verified: model grounded a click 1 px from the true button center.

**R9 — Redacted screenshots after venv rebuild (M2).** A python process without Screen Recording gets a redacted frame (wallpaper + menu bar only). A venv rebuild (new interpreter path) silently dropped the TCC grant. *Solution:* add the interpreter to System Settings → Privacy & Security → Screen Recording (pane-driven via AX, since the blind process can't see the UI to self-diagnose).

**R10 — Stale `runningApplications()` after quit (M3).** An app was reported running after quitting. *Solution:* use `pgrep` as ground truth and retry `open` past LaunchServices bookkeeping.

**R11 — TextEdit welcome dialog (M3).** SIGKILL suppressed TextEdit's welcome dialog; subsequent launches opened it and derailed tests. *Solution:* graceful quit + savedState wipe is the reliable reset.

**R12 — Empty sessions on the free-tier default model (M10).** The provider's raw default flipped to `nemotron-3.5-lightning-free`, which produced empty sessions — zero tool calls for 9 minutes (an unbounded e2e run at 555 s aborted). *Solution:* control server default-model preference `["deepseek-v4-flash-free"]` (proven model, no image support needed — text-only AX/DOM evidence) before the provider default; time-bounded reruns.

**R13 — Recurring TCC permission-prompt loop (M11, the worst one).** In app-launched mode, macOS TCC attributes screen-capture/AX requests to the app's process tree. The loop: UI polling `/api/screen.jpg` every ~2.5 s + `opencode serve` spawned **detached** (broke the responsible-process chain; every respawn looked like a new app, so Deny never stuck) + unbounded auto-restart. Result: infinite prompt storm, desktop unusable. *Solution (structural):* (a) screen.js latches `permissionDenied` after **2 consecutive failures** with `resetPermission()`; control server stops the loop, `/api/screen.jpg` → 409 with an actionable message, `metaPayload.screenPermissionDenied`; settings re-enable re-arms. (b) opencode.js no longer detached (keeps the TCC chain). (c) auto-restart capped at **5 attempts**, then stands down until START. (d) LiveScreen banner with an `x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture` deep link + Retry; main.swift routes that scheme via `NSWorkspace.shared.open`. Smoke test 11/11 after the fix.

**R14 — Signing identity attempt abandoned (M11).** A self-signed "CUA Control Dev" keychain certificate was tried so TCC grants would survive rebuilds. `codesign` hung on a keychain-access prompt (would itself become a new prompt loop), and identity pairing/ACL was fragile. *Solution:* back to **ad-hoc signing** (`codesign --force --deep --sign -`); TCC grants are per-built-bundle — re-enable once after a rebuild, and the banner handles missing grants. No prompts at signing time.

**R15 — Per-action latency (M8).** Baseline: capture 330 ms + OCR Accurate ~1051 ms + fixed settle → ~2.3 s per act, ~719 ms per unchanged observe. *Solution:* OCR at the Fast recognition level (~83 ms, 12×), content-hash-cached OCR (`Screenshot.sha`; repeated observes of an unchanged screen are free → observe 311 ms), and **adaptive settle** (poll `screen_hash()`; exit the instant the screen changes after dispatch → act cycle 0.8–1.0 s). Verified live on Calculator.

**R16 — Control-server logs lost under the native app (M10).** No visibility into what the app-launched stack did. *Solution:* pipes appended to `logs/control-server.out` with launch banners; gateway JSONL + observe/act.png already persisted in `logs/`.

**R17 — SPA fallback / static serving traps (M6).** Path-traversal guard (`startsWith(DIST_DIR)`), directory → `index.html`, SPA fallback for client routes, `no-store` for HTML, `no-transform` on the SSE stream.

---

## 18. Performance characteristics (targets a recreation should hit)

| Metric | Verified value |
|---|---|
| Screen capture (CGDisplayCreateImage → PNG) | ~330 ms |
| OCR, Fast level | ~83 ms (vs ~1051 ms Accurate) |
| Observe, unchanged screen (cached OCR) | ~311 ms |
| Full act cycle (click + settle + verify) | ~0.8–1.0 s |
| `/api/stop` → everything dead | < 1 s |
| `/api/start` → healthy | < 2 s |
| opencode serve health | < 30 s cold (spawn + MCP) |
| Screen loop cadence | 1.2 s busy / 2.5 s idle |
| Unit tests | 89 pass |
| Smoke test | 11/11 |

**Tuning knobs:** `SETTLE_SECONDS` (gateway mcp_server.py), `SETTLE_SECONDS` (browser mcp_server.py), `MAX_TREE_NODES` (400), AX depth (60), screen JPEG quality 62 / max dimension 1500 (screen.js), screen poll intervals (index.js), restart cap (5, opencode.js), auto-approve set (`bash`, `edit`), default-model preference list (index.js `refreshMeta`).

---

*End of recreation spec. A faithful recreation = the directory layout of §4, the contracts of §5–§14, the invariants of §15.1, the tests of §15, and the roadblock fixes of §17 — especially R13 (permission-loop structure), R1 (live frontmost-app tracking), R3 (fragment verification), and R12 (default-model preference).*