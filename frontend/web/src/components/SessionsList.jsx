import React, { useState } from "react";
import { api } from "../lib/api.js";

export default function SessionsList({ sessions, activeId, onSelect, onNew }) {
  const [confirmDelete, setConfirmDelete] = useState(null);

  const remove = async (e, s) => {
    e.stopPropagation();
    if (confirmDelete === s.id) {
      setConfirmDelete(null);
      try {
        await api.deleteSession(s.id);
      } catch {
        /* ignore */
      }
    } else {
      setConfirmDelete(s.id);
      setTimeout(() => setConfirmDelete(null), 2500);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2 className="panel-title">Sessions</h2>
        <button className="btn btn-mini" onClick={onNew} title="Start a fresh session">
          ＋ New
        </button>
      </div>
      <div className="session-list">
        {sessions.length === 0 && <p className="muted small">No sessions yet — run your first task.</p>}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`session-item ${s.id === activeId ? "session-active" : ""}`}
            onClick={() => onSelect(s.id)}
          >
            <div className="session-title">{s.title || s.id}</div>
            <div className="session-meta">
              <span>{new Date(s.time.created).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
              <button
                className="icon-btn"
                onClick={(e) => remove(e, s)}
                title={confirmDelete === s.id ? "Click again to delete" : "Delete session"}
              >
                {confirmDelete === s.id ? "✓" : "🗑"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
