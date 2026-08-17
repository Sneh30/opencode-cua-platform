# cua-gateway

CUA Gateway — the MCP server that gives OpenCode `gui.*` tools for desktop GUI
control. **Milestone M0 status: stub only** (hardcoded responses, placeholder
`instructions`).

Run the server (MCP stdio):

```sh
uv sync
uv run cua-gateway
```

Test:

```sh
uv run --extra dev pytest
```