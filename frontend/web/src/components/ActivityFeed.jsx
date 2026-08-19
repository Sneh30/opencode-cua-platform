import React, { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime, prettyJson } from "../lib/api.js";

function VerdictBadge({ verdict }) {
  if (!verdict) return null;
  const cls = verdict === "pass" ? "verdict-pass" : verdict === "fail" ? "verdict-fail" : "verdict-uncertain";
  return <span className={`badge ${cls}`}>verdict: {verdict}</span>;
}

function parseToolOutput(output) {
  try {
    const parsed = JSON.parse(output);
    if (parsed && typeof parsed === "object" && "verdict" in parsed) return parsed;
    if (parsed && typeof parsed === "object") return parsed;
  } catch {
    /* not json */
  }
  return null;
}

function ToolCard({ part }) {
  const [open, setOpen] = useState(false);
  const { tool, state } = part;
  const status = state.status;
  const parsed = useMemo(() => parseToolOutput(state.output), [state.output]);
  const isCua = tool.startsWith("gui.") || tool.startsWith("browser.");
  const input = state.input || {};
  const inputText = isCua ? JSON.stringify(input, null, 2) : prettyJson(input).slice(0, 3000);

  const statusIcon =
    status === "completed" ? "✓" : status === "error" ? "✕" : status === "running" ? "…" : "○";

  return (
    <div className={`tool-card tool-${status} ${isCua ? "tool-cua" : ""}`}>
      <div className="tool-head" onClick={() => setOpen(!open)}>
        <span className="tool-icon">{statusIcon}</span>
        <span className="tool-name">{tool}</span>
        {parsed?.verdict && <VerdictBadge verdict={parsed.verdict} />}
        {parsed?.recovery && parsed.recovery.attempts > 1 && (
          <span className="badge badge-warn">recovery ×{parsed.recovery.attempts}</span>
        )}
        {status === "completed" && parsed?.dispatched === false && <span className="badge badge-warn">not dispatched</span>}
        <span className="tool-toggle">{open ? "▾" : "▸"}</span>
      </div>
      {open && (
        <div className="tool-body">
          {inputText && (
            <>
              <div className="tool-label">input</div>
              <pre className="tool-json">{inputText}</pre>
            </>
          )}
          {state.output && (
            <>
              <div className="tool-label">output</div>
              <pre className="tool-json">{state.output.length > 4000 ? state.output.slice(0, 4000) + "\n… (truncated)" : state.output}</pre>
            </>
          )}
          {state.error && <div className="tool-error">{state.error}</div>}
        </div>
      )}
    </div>
  );
}

function PartView({ part }) {
  switch (part.type) {
    case "text":
      return <div className="part-text">{part.text}</div>;
    case "reasoning":
      return <div className="part-reasoning">💭 {part.text}</div>;
    case "tool":
      return <ToolCard part={part} />;
    case "step-start":
      return <div className="step-divider">— step —</div>;
    case "agent":
      return <div className="part-chip">agent: <b>{part.name}</b></div>;
    case "subtask":
      return <div className="part-chip">subtask → <b>{part.agent}</b>: {part.description}</div>;
    case "file":
      return <div className="part-chip">📎 {part.filename || part.url}</div>;
    case "patch":
      return <div className="part-chip">🧩 patch: {part.files?.join(", ")}</div>;
    case "snapshot":
      return <div className="part-chip">📸 snapshot</div>;
    case "retry":
      return <div className="part-chip part-error">⚠ retry attempt {part.attempt}: {part.error?.data?.message}</div>;
    case "compaction":
      return <div className="part-chip">🗜 context compacted</div>;
    default:
      return null;
  }
}

function MessageView({ message, isUser }) {
  const parts = message.parts || [];
  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-assistant"}`}>
      <div className="msg-head">
        <span className="msg-role">{isUser ? "you" : "agent"}</span>
        {message.info.modelID && !isUser && <span className="msg-model">{message.info.modelID}</span>}
        <span className="msg-time">{fmtTime(message.info.time?.created)}</span>
        {message.info.error && <span className="msg-error">error: {message.info.error.data?.message || JSON.stringify(message.info.error).slice(0, 200)}</span>}
      </div>
      <div className="msg-parts">
        {parts.map((p) => (
          <PartView key={p.id} part={p} />
        ))}
      </div>
    </div>
  );
}

export default function ActivityFeed({ sessionId, messages, busy }) {
  const scrollRef = useRef(null);
  const [stick, setStick] = useState(true);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (stick && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, busy, stick]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  if (!sessionId) {
    return (
      <section className="panel feed-panel">
        <div className="panel-head">
          <h2 className="panel-title">Agent Activity</h2>
        </div>
        <div className="feed-empty">
          <p className="muted">No active session.</p>
          <p className="muted small">
            Launch an app on the left, then type a task on the right — the agent will plan,
            click, type and verify on your real desktop.
          </p>
        </div>
      </section>
    );
  }

  const ordered = [...messages].sort((a, b) => (a.info.time?.created || 0) - (b.info.time?.created || 0));

  return (
    <section className="panel feed-panel">
      <div className="panel-head">
        <h2 className="panel-title">
          Agent Activity {busy && <span className="rec-dot">●</span>}
        </h2>
        <button className="btn btn-mini" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? "▾ show" : "▴ hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="feed-scroll" ref={scrollRef} onScroll={onScroll}>
          {ordered.length === 0 && <p className="muted small">Send a task to begin.</p>}
          {ordered.map((m) => (
            <MessageView key={m.info.id} message={m} isUser={m.info.role === "user"} />
          ))}
          {busy && <div className="typing">agent is working…</div>}
        </div>
      )}
    </section>
  );
}
