import React from "react";

function MCPChip({ name, status, connected }) {
  // status: "connected" | "failed" | "connecting" | "disabled" | null
  let cls, label;
  if (!connected) {
    cls = "warn";
    label = "disabled";
  } else if (status === "connected") {
    cls = "ok";
    label = "connected";
  } else if (status === "failed" || status === "disconnected") {
    cls = "bad";
    label = "failed";
  } else {
    cls = "warn";
    label = "connecting…";
  }
  return (
    <span className={`chip chip-${cls}`} title={`MCP: ${name} ${label}`}>
      {name} · {label}
    </span>
  );
}

export default function StatusBar({
  connected,
  healthy,
  version,
  gatewayOk,
  browserOk,
  mcp,
  busy,
  screenEnabled,
  lifecycleBusy,
  onToggleScreen,
  onStop,
  onStart,
}) {
  // Derive explicit MCP statuses from meta; null = not yet known
  const gwStatus = mcp?.["cua-gateway"]?.status ?? null;
  const brStatus = mcp?.["browser-adapter"]?.status ?? null;

  return (
    <header className="statusbar">
      <div className="brand">
        <span className="logo">🎛️</span>
        <span className="brand-name">opencode-operator</span>
        <span className="brand-sub">OpenCode Computer-Use Platform</span>
      </div>
      <div className="status-mid">
        <span className={`dot ${connected ? "dot-on" : "dot-off"}`} />
        <span className="status-text">{connected ? (healthy ? "running" : "stopped") : "reconnecting…"}</span>
        {healthy && <span className="status-text muted">opencode {version}</span>}
        <MCPChip name="cua-gateway" status={gwStatus} connected={connected} />
        <MCPChip name="browser-adapter" status={brStatus} connected={connected} />
      </div>
      <div className="status-right">
        {busy && <span className="badge badge-busy">RUNNING</span>}
        <button className={`btn btn-mini ${screenEnabled ? "" : "btn-dim"}`} onClick={() => onToggleScreen(!screenEnabled)} title="Toggle live screen capture">
          📷 {screenEnabled ? "screen on" : "screen off"}
        </button>
        {healthy ? (
          <button
            className="btn btn-mini btn-stop"
            onClick={onStop}
            disabled={lifecycleBusy}
            title="Stop opencode and screen capture NOW (kills the whole process group instantly)"
          >
            {lifecycleBusy ? "stopping…" : "⏹ STOP"}
          </button>
        ) : (
          <button
            className="btn btn-mini btn-start"
            onClick={onStart}
            disabled={lifecycleBusy}
            title="Start opencode + screen capture"
          >
            {lifecycleBusy ? "starting…" : "▶ START"}
          </button>
        )}
      </div>
    </header>
  );
}