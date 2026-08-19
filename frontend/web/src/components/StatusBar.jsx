import React from "react";

function MCPChip({ name, status }) {
  const cls = status === "connected" ? "ok" : status === "disabled" ? "off" : status === "failed" ? "bad" : "warn";
  return (
    <span className={`chip chip-${cls}`} title={status === "failed" ? "MCP server failed to connect" : `MCP: ${name} ${status}`}>
      {name} · {status}
    </span>
  );
}

export default function StatusBar({ connected, healthy, version, gatewayOk, browserOk, mcp, busy, screenEnabled, onToggleScreen }) {
  return (
    <header className="statusbar">
      <div className="brand">
        <span className="logo">🎛️</span>
        <span className="brand-name">CUA Control</span>
        <span className="brand-sub">OpenCode Computer-Use Platform</span>
      </div>
      <div className="status-mid">
        <span className={`dot ${connected ? "dot-on" : "dot-off"}`} />
        <span className="status-text">{connected ? "control server connected" : "reconnecting…"}</span>
        {healthy && <span className="status-text muted">opencode {version}</span>}
        <MCPChip name="cua-gateway" status={gatewayOk ? "connected" : connected ? "failed" : "disabled"} />
        <MCPChip name="browser-adapter" status={browserOk ? "connected" : connected ? "failed" : "disabled"} />
      </div>
      <div className="status-right">
        {busy && <span className="badge badge-busy">RUNNING</span>}
        <button className={`btn btn-mini ${screenEnabled ? "" : "btn-dim"}`} onClick={() => onToggleScreen(!screenEnabled)} title="Toggle live screen capture">
          📷 {screenEnabled ? "screen on" : "screen off"}
        </button>
      </div>
    </header>
  );
}
