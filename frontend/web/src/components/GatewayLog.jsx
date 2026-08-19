import React, { useEffect, useRef, useState } from "react";
import { fmtTime, prettyJson } from "../lib/api.js";

function verdictClass(v) {
  return v === "pass" ? "verdict-pass" : v === "fail" ? "verdict-fail" : v === "uncertain" ? "verdict-uncertain" : "";
}

function LogRow({ evt }) {
  const [open, setOpen] = useState(false);
  const kind = evt.event || "unknown";
  const cls =
    kind === "verification.result"
      ? "row-verify"
      : kind.startsWith("recovery")
        ? "row-recovery"
        : kind === "action.dispatched"
          ? "row-action"
          : "";
  const verdict = evt.verdict;
  return (
    <div className={`log-row ${cls}`} onClick={() => setOpen(!open)}>
      <div className="log-line">
        <span className="log-time">{fmtTime(new Date(evt.timestamp).getTime())}</span>
        <span className="log-event">{kind}</span>
        {evt.worker && <span className="log-worker">{evt.worker}</span>}
        {verdict && <span className={`badge ${verdictClass(verdict)}`}>{verdict}</span>}
        {evt.attempt && <span className="log-attempt">attempt {evt.attempt}</span>}
        {evt.strategy && <span className="log-strategy">→ {evt.strategy}</span>}
        {evt.action?.type && <span className="log-action-type">{evt.action.type}</span>}
      </div>
      {open && (
        <pre className="log-json">
          {prettyJson({
            ...evt,
            evidence: evt.evidence ? String(evt.evidence).slice(0, 600) : undefined,
          })}
        </pre>
      )}
    </div>
  );
}

export default function GatewayLog({ files, shots }) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef(null);
  const stickRef = useRef(true);

  const entries = Object.entries(files || {});
  const total = entries.reduce((n, [, arr]) => n + arr.length, 0);

  useEffect(() => {
    if (stickRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [total]);

  return (
    <section className="panel log-panel">
      <div className="panel-head">
        <h2 className="panel-title">Gateway Session Log</h2>
        <button className="btn btn-mini" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? "▾ show" : "▴ hide"}
        </button>
      </div>
      {!collapsed && (
        <div
          className="log-scroll"
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.target;
            stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
        >
          {total === 0 && (
            <p className="muted small">
              No gateway events yet. They appear here as the agent acts:
              action.dispatched → verification.result → recovery…
            </p>
          )}
          {entries.map(([file, arr]) => (
            <div key={file}>
              <div className="log-file">{file}</div>
              {arr.map((evt, i) => (
                <LogRow key={i} evt={evt} />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
