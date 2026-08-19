import React, { useState } from "react";
import { api } from "../lib/api.js";

export default function AppLauncher({ apps }) {
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(null);

  const launch = async (name) => {
    setBusy(name);
    try {
      await api.launchApp(name);
    } catch {
      /* toast comes via SSE */
    } finally {
      setBusy(null);
    }
  };

  const launchCustom = async (e) => {
    e.preventDefault();
    const name = custom.trim();
    if (!name) return;
    await launch(name);
    setCustom("");
  };

  return (
    <section className="panel">
      <h2 className="panel-title">Launch App</h2>
      <div className="app-grid">
        {apps.map((a) => (
          <button
            key={a.name + "|" + a.proc}
            className={`app-tile ${a.running ? "app-running" : ""}`}
            onClick={() => launch(a.name)}
            title={`${a.desc}\n${a.running ? "Running — click to bring to front" : "Click to launch"}`}
            disabled={busy !== null}
          >
            <span className="app-icon">{a.icon}</span>
            <span className="app-name">{a.name}</span>
            <span className={`app-dot ${a.running ? "app-dot-on" : ""}`} />
          </button>
        ))}
      </div>
      <form className="custom-launch" onSubmit={launchCustom}>
        <input
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder="Other app name… (e.g. Xcode)"
          spellCheck={false}
        />
        <button type="submit" className="btn" disabled={!custom.trim()}>
          Launch
        </button>
      </form>
    </section>
  );
}
