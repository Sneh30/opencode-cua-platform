# OpenCode Computer-Use Platform

Single orchestrator (OpenCode, unmodified) driving desktop GUI and browser workers through
MCP, with structural verification and recovery. Docs (source of truth):

- `docs/01-PRD.md` — product requirements
- `docs/02-Architecture.md` — component architecture
- `docs/03-Master-Prompt.md` — supervisor prompt delivered via MCP `instructions`
- `docs/04-Roadmap-and-Engineering-Decisions.md` — milestones + engineering decisions

## Status

**Milestone M0 (skeleton) in progress.** The Gateway is a stub: hardcoded `gui.*` tool
responses and a placeholder `instructions` string. Nothing real yet.

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