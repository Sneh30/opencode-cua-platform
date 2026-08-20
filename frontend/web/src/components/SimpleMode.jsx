import React, { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../lib/api.js";

function sessionTitle(session) {
  const t = session?.title || "";
  return t.length > 80 ? t.slice(0, 80) + "…" : t || "Untitled task";
}

function agentOutput(messages) {
  if (!messages || !messages.length) return null;
  const ordered = [...messages].sort((a, b) => (a.info.time?.created || 0) - (b.info.time?.created || 0));
  const assistant = ordered.filter((m) => m.info.role === "assistant" && !m.info.error);
  if (!assistant.length) return null;
  const last = assistant[assistant.length - 1];
  const text = (last.parts || [])
    .filter((p) => p.type === "text" && p.text)
    .map((p) => p.text)
    .join("\n\n");
  return { text, model: last.info.modelID, time: last.info.time?.created };
}

function firstUserTask(messages) {
  if (!messages || !messages.length) return null;
  const ordered = [...messages].sort((a, b) => (a.info.time?.created || 0) - (b.info.time?.created || 0));
  const user = ordered.find((m) => m.info.role === "user");
  if (!user) return null;
  const text = (user.parts || [])
    .filter((p) => p.type === "text" && p.text)
    .map((p) => p.text)
    .join("\n");
  return text || null;
}

export default function SimpleMode({
  sessions,
  activeSessionId,
  messages,
  busy,
  models,
  defaultModel,
  onSelectSession,
  onNewSession,
  onRun,
  onAbort,
}) {
  const [task, setTask] = useState("");
  const [model, setModel] = useState("");
  const [historyOpen, setHistoryOpen] = useState(() => {
    try {
      return localStorage.getItem("cua-simple-history") !== "0";
    } catch {
      return true;
    }
  });
  const [runningNow, setRunningNow] = useState(false);
  const taRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem("cua-simple-history", historyOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [historyOpen]);

  useEffect(() => {
    if (!model && defaultModel) setModel(defaultModel);
  }, [defaultModel]);

  const activeMessages = activeSessionId ? messages[activeSessionId] || [] : [];
  const output = useMemo(() => agentOutput(activeMessages), [activeMessages]);
  const taskText = useMemo(() => firstUserTask(activeMessages), [activeMessages]);

  const submit = async () => {
    const text = task.trim();
    if (!text || busy) return;
    setRunningNow(true);
    try {
      await onRun(text, model);
    } finally {
      setRunningNow(false);
    }
  };

  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  const select = (id) => {
    onSelectSession(id);
    if (taRef.current) taRef.current.blur();
  };

  const busyNow = busy || runningNow;

  return (
    <div className="simple">
      {historyOpen ? (
        <aside className="simple-sidebar">
          <div className="simple-sidebar-head">
            <h2>History</h2>
            <button className="btn btn-mini" onClick={() => setHistoryOpen(false)} title="Collapse history">
              «
            </button>
          </div>
          <div className="simple-history">
            {sessions.length === 0 && <p className="muted small simple-hint">Your past tasks will appear here.</p>}
            {sessions.map((s) => (
              <button
                key={s.id}
                className={`simple-history-item ${s.id === activeSessionId ? "active" : ""}`}
                onClick={() => select(s.id)}
                title={s.title}
              >
                <span className="simple-history-title">{sessionTitle(s)}</span>
                <span className="simple-history-meta">{fmtTime(s.time?.created)}</span>
              </button>
            ))}
          </div>
          <div className="simple-sidebar-foot">
            <button className="btn btn-block" onClick={onNewSession} title="Start a fresh task">
              + New task
            </button>
          </div>
        </aside>
      ) : (
        <div className="simple-rail" title="Show history">
          <button className="btn btn-mini" onClick={() => setHistoryOpen(true)}>
            »
          </button>
        </div>
      )}

      <main className="simple-main">
        <div className="simple-toolbar">
          <select
            className="select simple-model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={busyNow}
            title="Agent model"
          >
            {models.length === 0 && <option value="">default</option>}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label || m.id}
              </option>
            ))}
          </select>
          <span className="dot simple-status-dot dot-on" />
          <span className="simple-status-text">{busyNow ? "agent is working…" : "idle"}</span>
          {busyNow && (
            <button className="btn btn-mini btn-stop" onClick={onAbort}>
              ■ stop
            </button>
          )}
        </div>

        <div className="simple-composer">
          <textarea
            ref={taRef}
            className="simple-input"
            placeholder="Tell the agent what to do on your Mac — it will see the screen, plan, click, type and verify. (⌘⏎ to run)"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={onKeyDown}
            rows={4}
            disabled={busyNow}
            autoFocus
          />
          <div className="simple-composer-foot">
            <span className="muted small">Enter a task like “Open Finder and create a folder named Test”</span>
            <button className="btn btn-primary" onClick={submit} disabled={busyNow || !task.trim()}>
              {busyNow ? "Working…" : "Run task"}
            </button>
          </div>
        </div>

        <div className="simple-outcome">
          {activeSessionId && taskText && (
            <div className="simple-taskline">
              <span className="muted small">task:</span> {taskText}
            </div>
          )}
          {output ? (
            <div className="simple-output">
              {output.text.split("\n").map((line, i) => (
                <p key={i}>{line || "\u00A0"}</p>
              ))}
              <div className="simple-output-meta muted small">
                {output.model && <span>{output.model}</span>}
                {output.time && <span>{fmtTime(output.time)}</span>}
              </div>
            </div>
          ) : (
            <div className="simple-empty muted">
              {busyNow ? (
                <>
                  <span className="typing">agent is working…</span>
                  <p className="small">Watch this space — the agent output will appear here when the task finishes.</p>
                </>
              ) : (
                <>
                  <p>No agent output yet.</p>
                  <p className="small">
                    {activeSessionId
                      ? "The result of the last task will appear here."
                      : "Describe a task above and press Run — the agent will operate your real desktop and report back here."}
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}