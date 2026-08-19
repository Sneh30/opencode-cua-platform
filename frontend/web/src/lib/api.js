async function j(url, opts) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
  return body;
}

export const api = {
  status: () => j("/api/status"),
  apps: () => j("/api/apps"),
  launchApp: (name, path) => j("/api/apps/launch", { method: "POST", body: JSON.stringify({ name, path }) }),
  sessions: () => j("/api/sessions"),
  createSession: (title) => j("/api/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  session: (id) => j(`/api/session/${id}`),
  messages: (id) => j(`/api/session/${id}/messages`),
  task: (id, text, model) =>
    j(`/api/session/${id}/task`, { method: "POST", body: JSON.stringify({ text, model }) }),
  abort: (id) => j(`/api/session/${id}/abort`, { method: "POST", body: "{}" }),
  deleteSession: (id) => j(`/api/session/${id}`, { method: "DELETE" }),
  replyPermission: (sid, pid, response, remember = false) =>
    j(`/api/session/${sid}/permissions/${pid}`, {
      method: "POST",
      body: JSON.stringify({ response, remember }),
    }),
  logs: () => j("/api/logs"),
  settings: (patch) => j("/api/settings", { method: "POST", body: JSON.stringify(patch) }),
};

export function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtClock(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

export function prettyJson(obj) {
  try {
    return JSON.stringify(typeof obj === "string" ? JSON.parse(obj) : obj, null, 2);
  } catch {
    return String(obj);
  }
}
