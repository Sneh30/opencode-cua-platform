import React, { useState } from "react";

export default function TaskComposer({ session, busy, models, defaultModel, autoApprove, onAutoApprove, onRun, onAbort }) {
  const [text, setText] = useState("");
  const [model, setModel] = useState(() =>
    defaultModel ? `${defaultModel.providerID}/${defaultModel.modelID}` : ""
  );
  const [sending, setSending] = useState(false);

  const modelOptions = useModelOptions(models, defaultModel);

  const submit = async (e) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || busy || sending) return;
    setSending(true);
    try {
      const [providerID, modelID] = model.split("/");
      await onRun(trimmed, providerID && modelID ? { providerID, modelID } : null);
      setText("");
    } catch (err) {
      alert(`Failed to start task: ${err.message}`);
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="panel composer-panel">
      <div className="panel-head">
        <h2 className="panel-title">Task</h2>
        {session && <span className="session-badge">{session.title}</span>}
      </div>

      <form onSubmit={submit} className="composer">
        <textarea
          className="task-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            session
              ? "Continue the session… e.g. \"now open System Settings and check the display resolution\""
              : "Describe what you want the agent to do on your Mac…\n\ne.g. \"Open TextEdit, type a short note about the weather, save it to ~/Desktop/note.txt\"\n\ne.g. \"In Safari, open the Hacker News homepage and list the top 5 story titles\""
          }
          rows={6}
          spellCheck={false}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(e);
          }}
        />
        <div className="composer-row">
          <label className="model-label">
            model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className="auto-label" title="Auto-approve routine shell & file-edit permission requests so tasks run hands-free">
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={(e) => onAutoApprove(e.target.checked)}
            />
            auto-approve
          </label>
        </div>
        <div className="composer-row">
          {busy || sending ? (
            <button type="button" className="btn btn-stop" onClick={onAbort}>
              ⏹ Stop
            </button>
          ) : (
            <button type="submit" className="btn btn-run" disabled={!text.trim()}>
              ▶ Run task {model ? `· ${model.split("/")[1]}` : ""}
            </button>
          )}
          <span className="hint">⌘⏎ to run</span>
        </div>
      </form>
    </section>
  );
}

function useModelOptions(models, defaultModel) {
  const all = models.map((m) => `${m.providerID}/${m.modelID}`);
  if (defaultModel) {
    const d = `${defaultModel.providerID}/${defaultModel.modelID}`;
    if (!all.includes(d)) all.unshift(d);
  }
  if (!all.length) all.push("default");
  return all;
}
