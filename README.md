# OpenCode Computer-Use Platform

Single orchestrator (OpenCode, unmodified) driving desktop GUI and browser workers through
MCP, with structural verification and recovery. Docs (source of truth):

- `docs/01-PRD.md` — product requirements
- `docs/02-Architecture.md` — component architecture
- `docs/03-Master-Prompt.md` — supervisor prompt delivered via MCP `instructions`
- `docs/04-Roadmap-and-Engineering-Decisions.md` — milestones + engineering decisions

## Status

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