import React, { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api } from "./lib/api.js";
import StatusBar from "./components/StatusBar.jsx";
import AppLauncher from "./components/AppLauncher.jsx";
import LiveScreen from "./components/LiveScreen.jsx";
import ActivityFeed from "./components/ActivityFeed.jsx";
import TaskComposer from "./components/TaskComposer.jsx";
import GatewayLog from "./components/GatewayLog.jsx";
import SessionsList from "./components/SessionsList.jsx";
import PermissionModal from "./components/PermissionModal.jsx";

function initState() {
  return {
    connected: false,
    meta: null,
    apps: [],
    sessions: [],
    activeSessionId: null,
    messages: {},
    permissionQueue: [],
    toasts: [],
    gatewayLogs: {},
    gatewayShots: {},
    screenAt: 0,
    screenError: null,
    busy: false,
    opencodeLogs: [],
    screenEnabled: true,
    screenPermissionDenied: false,
    lifecycleBusy: false,
  };
}

function upsertMessage(state, info) {
  const sid = info.sessionID;
  const msgs = state.messages[sid] || [];
  const idx = msgs.findIndex((m) => m.info.id === info.id);
  if (idx === -1) {
    return { ...state, messages: { ...state.messages, [sid]: [...msgs, { info, parts: [] }] } };
  }
  const next = msgs.slice();
  next[idx] = { ...next[idx], info };
  return { ...state, messages: { ...state.messages, [sid]: next } };
}

function upsertPart(state, sid, part) {
  const msgs = state.messages[sid] || [];
  const idx = msgs.findIndex((m) => m.info.id === part.messageID);
  if (idx === -1) return state;
  const msg = msgs[idx];
  const existing = msg.parts.findIndex((p) => p.id === part.id);
  let parts;
  if (existing === -1) {
    parts = [...msg.parts, part];
  } else {
    const cur = msg.parts[existing];
    let merged = part;
    if (part.type === "text" && part.delta && cur.type === "text") {
      merged = { ...cur, text: (cur.text || "") + part.delta, time: part.time || cur.time };
    } else if (part.type === "reasoning" && part.delta && cur.type === "reasoning") {
      merged = { ...cur, text: (cur.text || "") + part.delta };
    }
    parts = msg.parts.slice();
    parts[existing] = merged;
  }
  const next = msgs.slice();
  next[idx] = { ...msg, parts };
  return { ...state, messages: { ...state.messages, [sid]: next } };
}

function reducer(state, action) {
  switch (action.type) {
    case "connected":
      return { ...state, connected: true };
    case "disconnected":
      return { ...state, connected: false };
    case "hello":
    case "meta.updated": {
      const meta = action.properties;
      return {
        ...state,
        meta,
        busy: meta.busy ?? state.busy,
        screenPermissionDenied: !!meta.screenPermissionDenied,
        gatewayShots: meta.gatewayLogs?.screenshots || state.gatewayShots,
        gatewayLogs: meta.gatewayLogs?.files || state.gatewayLogs,
        mcpMeta: undefined,
      };
    }
    case "session.created":
    case "session.updated": {
      const info = action.properties.info;
      const sessions = state.sessions.filter((s) => s.id !== info.id);
      return { ...state, sessions: [info, ...sessions] };
    }
    case "session.deleted": {
      const info = action.properties.info;
      return {
        ...state,
        sessions: state.sessions.filter((s) => s.id !== info.id),
        activeSessionId: state.activeSessionId === info.id ? null : state.activeSessionId,
      };
    }
    case "session.status": {
      const { sessionID, status } = action.properties;
      const busy = status.type === "busy" && state.activeSessionId === sessionID;
      return { ...state, busy: busy || state.busy };
    }
    case "session.idle": {
      const busy = state.busy && state.activeSessionId === action.properties.sessionID ? false : state.busy;
      return { ...state, busy };
    }
    case "task.started":
      return {
        ...state,
        busy: true,
        activeSessionId: action.properties.sessionID || state.activeSessionId,
        sessionBusyId: action.properties.sessionID,
      };
    case "session.error": {
      const err = action.properties.error;
      const msg = err?.data?.message || "session error";
      return { ...state, toasts: [...state.toasts, { id: Date.now(), kind: "error", text: msg }] };
    }
    case "message.updated":
      return upsertMessage(state, action.properties.info);
    case "message.part.updated": {
      const p = action.properties;
      let next = upsertPart(state, p.sessionID, p.part);
      if (p.part.type === "tool" && p.part.tool?.startsWith("gui.") && p.part.state?.status === "completed") {
        next = { ...next, screenAt: Date.now() };
      }
      return next;
    }
    case "message.part.removed": {
      const { sessionID, partID } = action.properties;
      const msgs = state.messages[sessionID] || [];
      const next = msgs.map((m) => ({ ...m, parts: m.parts.filter((p) => p.id !== partID) }));
      return { ...state, messages: { ...state.messages, [sessionID]: next } };
    }
    case "message.removed": {
      const { sessionID, messageID } = action.properties;
      const msgs = state.messages[sessionID] || [];
      return { ...state, messages: { ...state.messages, [sessionID]: msgs.filter((m) => m.info.id !== messageID) } };
    }
    case "permission.updated": {
      const p = action.properties;
      const queue = state.permissionQueue.filter((x) => x.id !== p.id);
      return { ...state, permissionQueue: [...queue, p] };
    }
    case "permission.replied": {
      const { permissionID } = action.properties;
      return { ...state, permissionQueue: state.permissionQueue.filter((x) => x.id !== permissionID) };
    }
    case "permission.auto":
      return {
        ...state,
        toasts: [...state.toasts.slice(-6), { id: Date.now(), kind: "auto", text: `Auto-approved: ${action.properties.title}` }],
      };
    case "gateway.log": {
      const { file, event } = action.properties;
      const files = { ...state.gatewayLogs };
      files[file] = [...(files[file] || []), event];
      return { ...state, gatewayLogs: files };
    }
    case "gateway.screenshots":
      return { ...state, gatewayShots: action.properties };
    case "screen.updated":
      return { ...state, screenAt: action.properties.at, screenError: null };
    case "screen.error":
      return { ...state, screenError: action.properties.error };
    case "screen.permission":
      return { ...state, screenPermissionDenied: true, screenError: null };
    case "screen.toggled":
      return { ...state, screenEnabled: action.enabled };
    case "lifecycle":
      return { ...state, lifecycleBusy: action.busy };
    case "toast":
      return {
        ...state,
        toasts: [...state.toasts.slice(-6), { id: Date.now(), kind: action.kind || "error", text: action.text }],
      };
    case "app.launched":
      return {
        ...state,
        toasts: [
          ...state.toasts.slice(-6),
          {
            id: Date.now(),
            kind: action.properties.ok ? "ok" : "error",
            text: action.properties.ok
              ? `Launched ${action.properties.name}`
              : `Failed to launch ${action.properties.name}: ${action.properties.error}`,
          },
        ],
      };
    case "opencode.log":
      return {
        ...state,
        opencodeLogs: [...state.opencodeLogs.slice(-300), action.properties.line],
      };
    case "apps":
      return { ...state, apps: action.apps };
    case "sessions":
      return { ...state, sessions: action.sessions };
    case "set-active-session":
      return { ...state, activeSessionId: action.id };
    case "load-messages":
      return { ...state, messages: { ...state.messages, [action.sessionId]: action.msgs } };
    case "toast-dismiss":
      return { ...state, toasts: state.toasts.filter((t) => t.id !== action.id) };
    default:
      return state;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, null, initState);
  const [toastShown, setToastShown] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;

  const refreshSessions = async () => {
    try {
      const list = await api.sessions();
      dispatch({ type: "sessions", sessions: list });
    } catch {
      /* server restarting */
    }
  };

  const refreshApps = async () => {
    try {
      const data = await api.apps();
      dispatch({ type: "apps", apps: data.apps });
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    const es = new EventSource("/api/event");
    es.onopen = () => dispatch({ type: "connected" });
    es.onerror = () => dispatch({ type: "disconnected" });
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        if (event.type === "ping" || event.type === "hello") return;
        dispatch({ type: event.type, properties: event.properties || {} });
      } catch {
        /* ignore */
      }
    };
    refreshSessions();
    refreshApps();
    const appTimer = setInterval(refreshApps, 8000);
    const sessTimer = setInterval(refreshSessions, 15000);
    return () => {
      es.close();
      clearInterval(appTimer);
      clearInterval(sessTimer);
    };
  }, []);

  useEffect(() => {
    if (state.meta?.mcp && state.meta.mcp["cua-gateway"]) {
      const st = state.meta.mcp["cua-gateway"].status;
      const toastKey = `mcp-gw-${st}`;
      if (st === "failed") {
        setToastShown(true);
      }
    }
  }, [state.meta?.mcp]);

  const loadSession = async (id) => {
    if (stateRef.current.messages[id]?.length) return;
    try {
      const msgs = await api.messages(id);
      dispatch({ type: "load-messages", sessionId: id, msgs });
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    if (state.activeSessionId) loadSession(state.activeSessionId);
  }, [state.activeSessionId]);

  const activeSession = state.sessions.find((s) => s.id === state.activeSessionId) || null;

  const runTask = async (text, model) => {
    let sid = state.activeSessionId;
    if (!sid) {
      const s = await api.createSession(text.slice(0, 60));
      sid = s.id;
      dispatch({ type: "session.created", properties: { info: s } });
      dispatch({ type: "set-active-session", id: sid });
    }
    await api.task(sid, text, model);
    loadSession(sid);
  };

  const replyPermission = async (perm, response) => {
    await api.replyPermission(perm.sessionID, perm.id, response, false);
    dispatch({ type: "permission.replied", properties: { permissionID: perm.id } });
  };

  const toggleAutoApprove = async (v) => {
    await api.settings({ autoApprove: v });
  };

  const toggleScreen = async (v) => {
    dispatch({ type: "screen.toggled", enabled: v });
    await api.settings({ screenCaptureEnabled: v });
  };

  const stopEverything = async () => {
    dispatch({ type: "lifecycle", busy: true });
    try {
      await api.stop();
      dispatch({ type: "screen.toggled", enabled: false });
    } catch (err) {
      dispatch({ type: "toast", text: `Stop failed: ${err.message}`, kind: "error" });
    } finally {
      dispatch({ type: "lifecycle", busy: false });
    }
  };

  const startEverything = async () => {
    dispatch({ type: "lifecycle", busy: true });
    try {
      await api.start();
      dispatch({ type: "screen.toggled", enabled: true });
    } catch (err) {
      dispatch({ type: "toast", text: `Start failed: ${err.message}`, kind: "error" });
    } finally {
      dispatch({ type: "lifecycle", busy: false });
    }
  };

  const dismissToast = (id) => dispatch({ type: "toast-dismiss", id });

  const mcp = state.meta?.mcp || {};
  const gatewayOk = mcp["cua-gateway"]?.status === "connected";
  const browserOk = mcp["browser-adapter"]?.status === "connected";

  return (
    <div className="app">
      <StatusBar
        connected={state.connected}
        healthy={!!state.meta?.healthy}
        version={state.meta?.version}
        gatewayOk={gatewayOk}
        browserOk={browserOk}
        mcp={mcp}
        busy={state.busy}
        screenEnabled={state.screenEnabled}
        lifecycleBusy={state.lifecycleBusy}
        onToggleScreen={toggleScreen}
        onStop={stopEverything}
        onStart={startEverything}
      />

      <div className="toasts">
        {state.toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => dismissToast(t.id)}>
            {t.text}
          </div>
        ))}
      </div>

      <PermissionModal
        queue={state.permissionQueue}
        onRespond={replyPermission}
      />

      <div className="layout">
        <aside className="col col-left">
          <AppLauncher apps={state.apps} />
          <SessionsList
            sessions={state.sessions}
            activeId={state.activeSessionId}
            onSelect={(id) => dispatch({ type: "set-active-session", id })}
            onNew={() => dispatch({ type: "set-active-session", id: null })}
          />
        </aside>

        <main className="col col-center">
          <LiveScreen
            at={state.screenAt}
            error={state.screenError}
            shots={state.gatewayShots}
            busy={state.busy}
            enabled={state.screenEnabled}
            permissionDenied={state.screenPermissionDenied}
            onRetry={() => toggleScreen(true)}
          />
          <ActivityFeed
            sessionId={state.activeSessionId}
            messages={state.messages[state.activeSessionId] || []}
            busy={state.busy}
          />
        </main>

        <aside className="col col-right">
          <TaskComposer
            session={activeSession}
            busy={state.busy}
            models={state.meta?.models || []}
            defaultModel={state.meta?.defaultModel}
            autoApprove={state.meta?.autoApprove ?? true}
            onAutoApprove={toggleAutoApprove}
            onRun={runTask}
            onAbort={async () => {
              if (state.activeSessionId) await api.abort(state.activeSessionId);
            }}
          />
          <GatewayLog files={state.gatewayLogs} shots={state.gatewayShots} />
        </aside>
      </div>
    </div>
  );
}
