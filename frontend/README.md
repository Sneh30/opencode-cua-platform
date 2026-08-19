# Frontend — Computer-Use control UI

React + Vite panel and a zero-dependency Node control server for the OpenCode
Computer-Use Platform. Launch an app, pick a model, type a task — the agent
drives the real desktop (GUI via `cua-gateway`) and the browser
(`browser-adapter`) while you watch live.

## Quick start

From the repo root:

```sh
./start.sh
```

This builds `frontend/dist` if stale, starts the control server (which spawns
`opencode serve` with the platform `opencode.json`), and opens the browser at
`http://localhost:8787`.

## Manual

```sh
cd frontend
npm install          # once
npm run build        # web UI -> dist/
node server/index.js # control server (PORT=8787, OPENCODE_PORT=49152)
```

## What it does

- **Sessions** — start a session, send a task, abort, delete; model picker
  lists the providers/models opencode serves (default = opencode's default)
- **Live screen** — the desktop capture the agent sees, streamed as
  downscaled JPEGs over SSE; grounding shots from the gateway (`logs/`) are
  shown as a strip when available
- **Activity feed** — every agent tool call as a card: gateway `gui.*`
  observe/act/verify with verdict + recovery badges, browser `browser.*`,
  bash/edit; running states update in place
- **Permissions** — interactive modal for approve/deny; "auto-approve
  bash/edit" toggle in the composer
- **Status** — control server, opencode serve, and MCP (cua-gateway,
  browser-adapter) connection chips
- **Gateway log** — live tail of `logs/<session>.jsonl` session events

## Server API

| Route | Purpose |
| --- | --- |
| `GET /api/status` | serve health, session count, MCP status, capture source |
| `GET /api/event` | SSE hub (`hello`, `meta.updated`, `session.updated`, `screen.updated`, `message.updated`, `gateway.log`, …) |
| `GET/POST /api/sessions` | list / create session |
| `GET /api/session/{id}` | session detail (messages, state, model) |
| `POST /api/session/{id}/task` | `prompt_async` → 202 `{sessionID}` |
| `POST /api/session/{id}/abort` | abort the run |
| `POST /api/session/{id}/permissions/{pid}` | allow/deny a permission |
| `GET /api/session/{id}/messages` | message stream for a session |
| `GET /api/apps`, `POST /api/apps/launch` | app catalog / launch |
| `GET /api/screen.jpg` | current capture (source: screencapture, fallback gateway venv) |
| `GET /api/gateway-screenshot/{file}` | grounding shots from `logs/` |
| `GET /api/logs` | gateway session log lines |
| `POST /api/settings` | `{autoApprove, screenCaptureEnabled}` |

## Tests

```sh
node frontend/scripts/smoke-test.js
```

11 checks: serve health, MCP connected (both), real LLM task through
`prompt_async` + SSE (messages flow), screen capture, abort, JSONL log
stream, session lifecycle. Env overrides: `SMOKE_PORT` (18991),
`SMOKE_OPENCODE_PORT` (18992). Takes ~2–3 min.

```sh
node frontend/scripts/browser-e2e.js
```

Full browser loop through opencode: the planner drives `browser.act`
(navigate + extract) on example.com via the browser-adapter MCP server and
must answer with the h1 text. Retries up to 3× when the (free-tier) planner
produces inconclusive output. Env overrides: `BE2E_PORT` (19991),
`BE2E_OPENCODE_PORT` (19992). Takes ~3–10 min.

## Notes

- Requires a Screen Recording grant for the process running the control
  server (or the gateway venv python, used as fallback).
- Model-grounding latency: local UI-TARS 7B on Apple silicon takes ~10 min on
  first load+infer (16s load once the model is in the HF cache), ~2 min per
  subsequent step — watch the live screen; the gateway enforces the
  retry/verify/recover loop server-side.
- Weak planner models can stall after `gui.observe` without calling
  `gui.act`; pick a stronger model in the composer for GUI tasks.
