import React, { useEffect, useRef, useState } from "react";
import { fmtTime } from "../lib/api.js";

export default function LiveScreen({ at, error, shots, busy, enabled }) {
  const [imgAt, setImgAt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!enabled) return;
    const tick = () => setImgAt(Date.now());
    tick();
    pollRef.current = setInterval(tick, busy ? 1300 : 2600);
    return () => clearInterval(pollRef.current);
  }, [enabled, busy, at]);

  const src = imgAt ? `/api/screen.jpg?t=${imgAt}` : null;
  const [obs, act] = [shots["observe.png"], shots["act.png"]];

  return (
    <section className={`panel screen-panel ${expanded ? "screen-expanded" : ""}`}>
      <div className="panel-head">
        <h2 className="panel-title">Live Desktop {busy && <span className="rec-dot" title="Agent is working on your screen">●</span>}</h2>
        <div className="panel-actions">
          <button className="btn btn-mini" onClick={() => setExpanded(!expanded)}>
            {expanded ? "◤ shrink" : "⤢ expand"}
          </button>
        </div>
      </div>
      <div className="screen-wrap">
        {enabled ? (
          src ? (
            <img key={src} src={src} alt="live desktop" className="screen-img" />
          ) : (
            <div className="screen-placeholder">capturing…</div>
          )
        ) : (
          <div className="screen-placeholder">screen capture disabled</div>
        )}
        {error && <div className="screen-error">capture error: {error}</div>}
        <div className="screen-timestamp">
          {at ? `captured ${fmtTime(at)}` : "no capture yet"} {busy && "· agent active"}
        </div>
      </div>
      {(obs || act) && (
        <div className="grounding-strip">
          <span className="strip-label">grounding shots</span>
          {obs && (
            <div className="shot">
              <img src={obs.path} alt="observe" />
              <span className="shot-label">observe {fmtTime(obs.at)}</span>
            </div>
          )}
          {act && (
            <div className="shot">
              <img src={act.path} alt="act" />
              <span className="shot-label">act {fmtTime(act.at)}</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
