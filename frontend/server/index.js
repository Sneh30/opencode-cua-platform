"use strict";

/**
 * CUA Control Server — the frontend layer for the OpenCode Computer-Use
 * Platform.
 *
 * Responsibilities:
 *   - spawn and supervise `opencode serve` (cwd = platform repo root so the
 *     MCP servers in opencode.json register: CUA Gateway + Browser Adapter)
 *   - serve the web UI (frontend/dist) and a small JSON API
 *   - live screen capture (screencapture + sips, gateway-python fallback)
 *   - watch gateway JSONL session logs and screenshots in logs/
 *   - re-broadcast opencode SSE events + control events to browsers over
 *     a single /api/event stream
 *   - auto-approve routine permission requests (bash/edit) when enabled so
 *     sessions run hands-free
 *
 * Zero npm runtime dependencies.
 */

const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

const { OpencodeServer } = require("./opencode");
const { GatewayLogs } = require("./logs");
const screen = require("./screen");
const apps = require("./apps");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(__dirname, "..", "dist");
const LOGS_DIR = path.join(REPO_ROOT, "logs");
const CONTROL_PORT = Number(process.env.PORT) || 8787;
const OPENCODE_PORT = Number(process.env.OPENCODE_PORT) || 49152;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
  ".map": "application/json",
};

// ---------------------------------------------------------------------------
// SSE hub
// ---------------------------------------------------------------------------

const clients = new Set();

function broadcast(event) {
  const payload = `data: ${JSON.stringify(event)}\n\n`;
  for (const res of clients) {
    try {
      res.write(payload);
    } catch {
      clients.delete(res);
    }
  }
}

function isBusy() {
  return state.sessionStatus && state.sessionStatus[state.activeSession?.id]?.type === "busy";
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  opencode: null,
  logs: null,
  activeSession: null,
  sessionStatus: {},
  mcp: {},
  providers: null,
  models: [],
  defaultModel: null,
  modelHealth: new Map(), // modelID -> { successes, failures, emptySessions, totalLatency, lastUsed }
  autoApprove: true,
  autoApproveTypes: new Set(["bash", "edit"]),
  lastScreen: null,
  screenInterval: null,
  appsRunning: {},
  startedAt: Date.now(),
  screenCaptureEnabled: true,
  screenPermissionDenied: false,
};

// ---------------------------------------------------------------------------
// opencode lifecycle
// ---------------------------------------------------------------------------

async function ensureOpencode() {
  if (state.opencode && state.opencode.running) return state.opencode;
  state.opencode = new OpencodeServer({ repoRoot: REPO_ROOT, port: CONTROL_PORT });
  state.opencode.on("event", (evt) => handleOpencodeEvent(evt));
  state.opencode.on("log", (line) => broadcast({ type: "opencode.log", properties: { line } }));
  try {
    await state.opencode.start({ port: OPENCODE_PORT });
    await refreshMeta();
    return state.opencode;
  } catch (err) {
    state.opencode = null;
    throw err;
  }
}

async function refreshMeta() {
  if (!state.opencode?.running) return;
  try {
    state.mcp = await state.opencode.mcpStatus();
  } catch {
    state.mcp = {};
  }
  try {
    state.sessionStatus = await state.opencode.sessionStatus();
  } catch {
    state.sessionStatus = {};
  }
  try {
    const prov = await state.opencode.listProviders();
    state.providers = prov;
    const connected = new Set(prov.connected || []);
    const models = [];
    for (const p of prov.all || []) {
      if (!connected.has(p.id)) continue;
      for (const [modelId, m] of Object.entries(p.models || {})) {
        if (m.status === "deprecated" || m.experimental) continue;
        models.push({ providerID: p.id, modelID: modelId, name: m.name || modelId, limit: m.limit });
      }
    }
    state.models = models;
    const def = prov.default || {};
    // Prefer a model proven reliable for agentic GUI work over the
    // provider's raw default (free-tier defaults can hang or return
    // empty sessions — seen with nemotron-3.5-lightning-free).
    const preferred = ["deepseek-v4-flash-free"];
    state.defaultModel =
      models.find((m) => preferred.includes(m.modelID))
      || models.find((m) => m.providerID === "opencode" && m.modelID === def.opencode)
      || models[0] || null;
  } catch {
    /* providers optional */
  }
  broadcast({ type: "meta.updated", properties: metaPayload() });
}

// ============================================================
// Model Health Tracking + Fallback Chain
// ============================================================

const MODEL_FALLBACK_CHAIN = [
  // Priority order for GUI agent tasks
  "deepseek-v4-flash-free",
  "big-pickle",
  "nemotron-3-ultra-free",
  "hy3-free",
  "muse-spark-1.2-contributor-free",
  "x-preview-f-free",
  "mimo-v2.5-free",
];

const MODEL_HEALTH_WINDOW = 50; // track last N tasks per model

function getModelHealth(modelId) {
  if (!state.modelHealth.has(modelId)) {
    state.modelHealth.set(modelId, {
      successes: 0,
      failures: 0,
      emptySessions: 0,
      totalLatency: 0,
      taskCount: 0,
      lastUsed: 0,
    });
  }
  return state.modelHealth.get(modelId);
}

function recordModelSuccess(modelId, latencyMs) {
  const h = getModelHealth(modelId);
  h.successes++;
  h.taskCount++;
  h.totalLatency += latencyMs;
  h.lastUsed = Date.now();
  // Keep window bounded
  if (h.taskCount > MODEL_HEALTH_WINDOW) {
    // Approximate: scale down
    h.successes = Math.round(h.successes * 0.9);
    h.failures = Math.round(h.failures * 0.9);
    h.emptySessions = Math.round(h.emptySessions * 0.9);
    h.taskCount = Math.round(h.taskCount * 0.9);
    h.totalLatency = Math.round(h.totalLatency * 0.9);
  }
}

function recordModelFailure(modelId, isEmptySession = false) {
  const h = getModelHealth(modelId);
  h.failures++;
  h.taskCount++;
  if (isEmptySession) h.emptySessions++;
  h.lastUsed = Date.now();
}

function getModelScore(modelId) {
  const h = getModelHealth(modelId);
  if (h.taskCount < 3) return 100; // new models get benefit of doubt
  const successRate = h.successes / h.taskCount;
  const emptyRate = h.emptySessions / h.taskCount;
  const avgLatency = h.totalLatency / Math.max(1, h.successes);
  // Score: success rate (0-100) minus empty session penalty minus latency penalty
  let score = successRate * 100;
  score -= emptyRate * 50; // heavy penalty for empty sessions
  score -= Math.min(30, avgLatency / 1000); // latency penalty capped at 30
  return Math.max(0, score);
}

function selectBestModel(availableModels, preferredModelId = null) {
  // Filter to available models
  const available = availableModels.filter(m => state.models.some(am => am.modelID === m.modelID));
  if (available.length === 0) return null;

  // If preferred is available and healthy, use it
  if (preferredModelId) {
    const pref = available.find(m => m.modelID === preferredModelId);
    if (pref && getModelScore(pref.modelID) > 30) return pref;
  }

  // Score all available models and pick best
  let best = available[0];
  let bestScore = -1;
  for (const m of available) {
    const score = getModelScore(m.modelID);
    if (score > bestScore) {
      bestScore = score;
      best = m;
    }
  }
  return best;
}

function getHealthyFallback(currentModelId, availableModels) {
  // Return next best model excluding current
  const candidates = availableModels.filter(m => m.modelID !== currentModelId);
  return selectBestModel(candidates);
}

// Patch refreshMeta to use health-aware selection
const originalRefreshMeta = refreshMeta;
async function refreshMeta() {
  if (!state.opencode?.running) return;
  try {
    state.mcp = await state.opencode.mcpStatus();
  } catch {
    state.mcp = {};
  }
  try {
    state.sessionStatus = await state.opencode.sessionStatus();
  } catch {
    state.sessionStatus = {};
  }
  try {
    const prov = await state.opencode.listProviders();
    state.providers = prov;
    const connected = new Set(prov.connected || []);
    const models = [];
    for (const p of prov.all || []) {
      if (!connected.has(p.id)) continue;
      for (const [modelId, m] of Object.entries(p.models || {})) {
        if (m.status === "deprecated" || m.experimental) continue;
        models.push({ providerID: p.id, modelID: modelId, name: m.name || modelId, limit: m.limit });
      }
    }
    state.models = models;
    const def = prov.default || {};
    // Health-aware model selection with fallback chain
    state.defaultModel = selectBestModel(models, def.opencode);
  } catch {
    /* providers optional */
  }
  broadcast({ type: "meta.updated", properties: metaPayload() });
}

function handleOpencodeEvent(evt) {
  switch (evt.type) {
    case "session.status": {
      const { sessionID, status } = evt.properties;
      state.sessionStatus[sessionID] = status;
      broadcast({ type: evt.type, properties: evt.properties });
      // Model health tracking: when session goes idle, evaluate outcome
      if (status.type === "idle" && state.activeSession?.id === sessionID) {
        const sessionModel = state.activeSession.model;
        const duration = Date.now() - (state.activeSession.taskStart || Date.now());
        // Check if session produced tool calls (non-empty)
        // We'll need to check messages for tool calls; for now track duration
        // Short duration (< 10s) with no tool calls = likely empty session
        const isLikelyEmpty = duration < 10000; // heuristic
        if (sessionModel) {
          if (isLikelyEmpty) {
            recordModelFailure(sessionModel, true);
            console.log(`[model-health] Empty session detected for ${sessionModel} (${duration}ms)`);
          } else {
            recordModelSuccess(sessionModel, duration);
            console.log(`[model-health] Success for ${sessionModel} (${duration}ms)`);
          }
        }
        state.activeSession = null;
      }
      break;
    }
    case "session.idle":
    case "session.created":
    case "session.updated":
    case "session.deleted":
    case "session.error":
    case "message.updated":
    case "message.part.updated":
    case "message.part.removed":
    case "message.removed":
    case "todo.updated":
    case "file.edited":
    case "session.diff":
    case "permission.replied":
      broadcast({ type: evt.type, properties: evt.properties });
      break;
    case "permission.updated": {
      const p = evt.properties;
      broadcast({ type: evt.type, properties: p });
      maybeAutoApprove(p);
      break;
    }
    case "server.connected":
    case "installation.updated":
      break;
    default:
      broadcast({ type: evt.type, properties: evt.properties || {} });
  }
}

function maybeAutoApprove(perm) {
  if (!state.autoApprove) return;
  if (!state.autoApproveTypes.has(perm.type) && !perm.type.startsWith("bash:")) return;
  if (!state.opencode) return;
  state.opencode
    .replyPermission(perm.sessionID, perm.id, "allow", false)
    .then(() => {
      broadcast({
        type: "permission.auto",
        properties: { permissionID: perm.id, sessionID: perm.sessionID, title: perm.title },
      });
    })
    .catch(() => {});
}

// ---------------------------------------------------------------------------
// Screen capture loop
// ---------------------------------------------------------------------------

async function captureTick() {
  if (!state.screenCaptureEnabled || clients.size === 0) return;
  try {
    const shot = await screen.capture();
    if (shot.permission) {
      // Screen Recording grant missing for this launch context — stop
      // polling so TCC is not hammered with prompts every few seconds.
      stopScreenLoop();
      state.screenPermissionDenied = true;
      broadcast({
        type: "screen.permission",
        properties: { denied: true, error: shot.error || "screen recording permission needed" },
      });
      return;
    }
    if (shot.ok && state.lastScreen !== shot.at) {
      state.lastScreen = shot.at;
      broadcast({ type: "screen.updated", properties: { at: shot.at } });
    } else if (!shot.ok) {
      broadcast({ type: "screen.error", properties: { error: shot.error } });
    }
  } catch {
    /* ignore transient capture errors */
  }
}

function startScreenLoop() {
  if (state.screenInterval) return;
  screen.resetPermission();
  state.screenPermissionDenied = false;
  state.screenInterval = setInterval(captureTick, isBusy() ? 1200 : 2500);
  if (state.screenInterval.unref) state.screenInterval.unref();
  captureTick();
}

function stopScreenLoop() {
  if (state.screenInterval) {
    clearInterval(state.screenInterval);
    state.screenInterval = null;
  }
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj, null, 2);
  res.writeHead(code, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => {
      data += c;
      if (data.length > 2_000_000) {
        reject(new Error("body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        reject(new Error("invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}

function serveStatic(res, rel) {
  let file = path.join(DIST_DIR, rel === "/" ? "index.html" : rel);
  if (!file.startsWith(DIST_DIR)) {
    res.writeHead(403).end("forbidden");
    return;
  }
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
    file = path.join(file, "index.html");
  }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
    file = path.join(DIST_DIR, "index.html"); // SPA fallback
  }
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, {
    "Content-Type": MIME[ext] || "application/octet-stream",
    "Cache-Control": ext === ".html" ? "no-store" : "public, max-age=300",
  });
  fs.createReadStream(file).pipe(res);
}

function metaPayload() {
  const modelHealth = {};
  for (const [modelId, h] of state.modelHealth) {
    modelHealth[modelId] = {
      successes: h.successes,
      failures: h.failures,
      emptySessions: h.emptySessions,
      taskCount: h.taskCount,
      successRate: h.taskCount > 0 ? h.successes / h.taskCount : 0,
      avgLatency: h.successes > 0 ? h.totalLatency / h.successes : 0,
      score: getModelScore(modelId),
      lastUsed: h.lastUsed,
    };
  }
  return {
    healthy: state.opencode?.running,
    version: state.opencode?.health?.version || "",
    opencodeUrl: state.opencode?.url || null,
    mcp: state.mcp,
    models: state.models,
    defaultModel: state.defaultModel,
    modelHealth,
    autoApprove: state.autoApprove,
    busy: isBusy(),
    screen: screen.lastCaptureInfo(),
    screenPermissionDenied: !!state.screenPermissionDenied,
    logsDir: LOGS_DIR,
    startedAt: state.startedAt,
    gatewayLogs: state.logs ? state.logs.snapshot() : { files: {}, screenshots: {} },
  };
}

// ---------------------------------------------------------------------------
// API routes
// ---------------------------------------------------------------------------

async function handleApi(req, res, url) {
  const { pathname } = url;

  // -- status -----------------------------------------------------------
  if (pathname === "/api/status" && req.method === "GET") {
    return sendJson(res, 200, metaPayload());
  }

  // -- SSE event stream -------------------------------------------------
  if (pathname === "/api/event" && req.method === "GET") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.write(`data: ${JSON.stringify({ type: "hello", properties: metaPayload() })}\n\n`);
    clients.add(res);
    startScreenLoop();
    res.on("close", () => {
      clients.delete(res);
      if (clients.size === 0) stopScreenLoop();
    });
    return;
  }

  // -- apps -------------------------------------------------------------
  if (pathname === "/api/apps" && req.method === "GET") {
    const running = await apps.runningStatus();
    return sendJson(res, 200, {
      apps: apps.APPS.map((a) => ({ ...a, running: !!running[`${a.name}|${a.proc}`] })),
    });
  }

  if (pathname === "/api/apps/launch" && req.method === "POST") {
    const body = await readBody(req);
    const name = (body.name || "").trim();
    if (!name) return sendJson(res, 400, { error: "missing name" });
    const result = await apps.launch(name, { path: body.path });
    broadcast({ type: "app.launched", properties: { name, ok: result.ok, error: result.error } });
    return sendJson(res, result.ok ? 200 : 500, result);
  }

  // -- screen -----------------------------------------------------------
  if (pathname === "/api/screen.jpg" && req.method === "GET") {
    if (!state.screenCaptureEnabled) {
      return sendJson(res, 409, { error: "screen capture stopped" });
    }
    if (state.screenPermissionDenied) {
      return sendJson(res, 409, { error: "screen recording permission needed — enable Screen Recording for opencode-operator (and Terminal/python if listed) in System Settings" });
    }
    const shot = await screen.capture();
    const file = screen.screenFile();
    if (!file) return sendJson(res, 500, { error: shot.error || "no capture" });
    res.writeHead(200, {
      "Content-Type": "image/jpeg",
      "Cache-Control": "no-store",
      "X-Capture-At": String(shot.at),
    });
    return fs.createReadStream(file).pipe(res);
  }

  // -- gateway screenshots ----------------------------------------------
  const shotMatch = pathname.match(/^\/api\/gateway-screenshot\/([^/]+)$/);
  if (shotMatch && req.method === "GET") {
    const name = path.basename(shotMatch[1]);
    if (!/\.(png|jpe?g|webp)$/i.test(name)) return sendJson(res, 400, { error: "bad name" });
    const file = path.join(LOGS_DIR, name);
    if (!fs.existsSync(file)) return sendJson(res, 404, { error: "not found" });
    res.writeHead(200, {
      "Content-Type": name.endsWith(".png") ? "image/png" : "image/jpeg",
      "Cache-Control": "no-store",
    });
    return fs.createReadStream(file).pipe(res);
  }

  // -- gateway logs -----------------------------------------------------
  if (pathname === "/api/logs" && req.method === "GET") {
    return sendJson(res, 200, state.logs ? state.logs.snapshot() : { files: {}, screenshots: {} });
  }

  // -- lifecycle: STOP / START ---------------------------------------
  // STOP kills opencode + its MCP children (gateway screen capture,
  // browser adapter) instantly via process-group SIGKILL, and halts the
  // screen capture loop. START brings everything back. Both work even
  // when opencode is down, so they sit before the running-guard below.
  if (pathname === "/api/stop" && req.method === "POST") {
    stopScreenLoop();
    state.screenCaptureEnabled = false;
    state.activeSession = null;
    if (state.opencode) await state.opencode.stop();
    state.opencode = null;
    broadcast({ type: "meta.updated", properties: metaPayload() });
    console.log("STOP requested — opencode + screen capture halted");
    return sendJson(res, 200, { ok: true, stopped: true });
  }

  if (pathname === "/api/start" && req.method === "POST") {
    state.screenCaptureEnabled = true;
    startScreenLoop();
    try {
      await ensureOpencode();
      broadcast({ type: "meta.updated", properties: metaPayload() });
      console.log("START requested — opencode serving again");
      return sendJson(res, 200, { ok: true, healthy: true });
    } catch (err) {
      return sendJson(res, 500, { ok: false, error: err.message });
    }
  }

  // -- opencode passthrough ---------------------------------------------
  if (!state.opencode?.running) {
    return sendJson(res, 503, { error: "opencode server not running — retry in a moment" });
  }

  if (pathname === "/api/sessions" && req.method === "GET") {
    return sendJson(res, 200, await state.opencode.listSessions());
  }

  if (pathname === "/api/sessions" && req.method === "POST") {
    const body = await readBody(req);
    const s = await state.opencode.createSession(body.title);
    broadcast({ type: "session.created", properties: { info: s } });
    return sendJson(res, 200, s);
  }

  const sessionMatch = pathname.match(/^\/api\/session\/([^/]+)$/);
  const taskMatch = pathname.match(/^\/api\/session\/([^/]+)\/task$/);
  const abortMatch = pathname.match(/^\/api\/session\/([^/]+)\/abort$/);
  const messagesMatch = pathname.match(/^\/api\/session\/([^/]+)\/messages$/);
  const permMatch = pathname.match(/^\/api\/session\/([^/]+)\/permissions\/([^/]+)$/);

  if (sessionMatch && req.method === "GET") {
    return sendJson(res, 200, await state.opencode.getSession(sessionMatch[1]));
  }

  if (sessionMatch && req.method === "DELETE") {
    await state.opencode.deleteSession(sessionMatch[1]);
    return sendJson(res, 200, { ok: true });
  }

  if (taskMatch && req.method === "POST") {
    const id = taskMatch[1];
    const body = await readBody(req);
    const text = (body.text || "").trim();
    if (!text) return sendJson(res, 400, { error: "missing task text" });
    let model = body.model;
    if (model && !model.modelID) model = null;
    // Auto-select healthy model if none specified
    if (!model && state.defaultModel) {
      model = { providerID: state.defaultModel.providerID, modelID: state.defaultModel.modelID };
    }
    state.activeSession = { id, title: (body.title || text).slice(0, 60), model: model?.modelID, taskStart: Date.now() };
    await state.opencode.promptAsync(id, { text, model: model || null });
    broadcast({ type: "task.started", properties: { sessionID: id, text, model: model?.modelID } });
    return sendJson(res, 202, { ok: true });
  }

  if (abortMatch && req.method === "POST") {
    await state.opencode.abort(abortMatch[1]);
    return sendJson(res, 200, { ok: true });
  }

  if (messagesMatch && req.method === "GET") {
    return sendJson(res, 200, await state.opencode.listMessages(messagesMatch[1]));
  }

  if (permMatch && req.method === "POST") {
    const body = await readBody(req);
    const response = body.response === "deny" ? "deny" : "allow";
    await state.opencode.replyPermission(permMatch[1], permMatch[2], response, !!body.remember);
    return sendJson(res, 200, { ok: true });
  }

  // -- settings ---------------------------------------------------------
  if (pathname === "/api/settings" && req.method === "POST") {
    const body = await readBody(req);
    if (typeof body.autoApprove === "boolean") state.autoApprove = body.autoApprove;
    if (typeof body.screenCaptureEnabled === "boolean") {
      state.screenCaptureEnabled = body.screenCaptureEnabled;
      if (body.screenCaptureEnabled) {
        state.screenPermissionDenied = false;
        screen.resetPermission();
        startScreenLoop();
      } else {
        stopScreenLoop();
      }
    }
    broadcast({ type: "meta.updated", properties: metaPayload() });
    return sendJson(res, 200, { ok: true, autoApprove: state.autoApprove });
  }

  return sendJson(res, 404, { error: `no route: ${pathname}` });
}

// ---------------------------------------------------------------------------
// Main server
// ---------------------------------------------------------------------------

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url);
    } else if (req.method === "GET") {
      serveStatic(res, url.pathname);
    } else {
      res.writeHead(405).end();
    }
  } catch (err) {
    if (!res.headersSent) sendJson(res, 500, { error: err.message });
    else res.end();
  }
});

async function main() {
  fs.mkdirSync(LOGS_DIR, { recursive: true });
  state.logs = new GatewayLogs(LOGS_DIR);
  state.logs.on("append", ({ file, event }) => {
    broadcast({ type: "gateway.log", properties: { file, event } });
  });
  state.logs.on("screenshots", (shots) => {
    broadcast({ type: "gateway.screenshots", properties: shots });
  });
  state.logs.start();

  // heartbeat so browsers can detect control-server liveness
  setInterval(() => {
    if (clients.size === 0) return;
    broadcast({ type: "ping", properties: { t: Date.now() } });
  }, 20_000);

  server.listen(CONTROL_PORT, "127.0.0.1", async () => {
    console.log(`CUA Control Server: http://localhost:${CONTROL_PORT}`);
    try {
      await ensureOpencode();
      console.log(
        `opencode serve healthy (${state.opencode.health.version}) at ${state.opencode.url}`
      );
    } catch (err) {
      console.error(`opencode serve failed to start: ${err.message}`);
    }
  });

  const shutdown = async () => {
    console.log("shutting down...");
    state.logs.stop();
    if (state.opencode) await state.opencode.stop();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main();
