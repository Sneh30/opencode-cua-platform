import React from "react";

export default function PermissionModal({ queue, onRespond }) {
  if (!queue.length) return null;
  return (
    <div className="perm-backdrop">
      <div className="perm-modal">
        <h3 className="perm-title">⚠ Action requires approval</h3>
        {queue.map((p) => (
          <div key={p.id} className="perm-item">
            <div className="perm-type">
              <span className="chip">{p.type}</span>
              {p.pattern && <code className="perm-pattern">{Array.isArray(p.pattern) ? p.pattern.join(", ") : p.pattern}</code>}
            </div>
            <div className="perm-desc">{p.title}</div>
            <div className="perm-actions">
              <button className="btn btn-deny" onClick={() => onRespond(p, "deny")}>
                ✕ Deny
              </button>
              <button className="btn btn-run" onClick={() => onRespond(p, "allow")}>
                ✓ Allow
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
