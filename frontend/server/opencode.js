"use strict";

const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const path = require("node:path");
const fs = require("node:fs");

const OPENCODE_BIN = process.env.OPENCODE_BIN || "opencode";

/**
 * Thin client for the opencode headless server HTTP API.
 *
 * The control server spawns `opencode serve` (cwd = platform repo root so the
 * MCP servers in opencode.json register) and talks to it over plain HTTP.
 * Zero npm dependencies: fetch + a hand-rolled SSE reader.
 */
class OpencodeServer extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.repoRoot platform repo root (also the opencode project dir)
   * @param {string} opts.port    control-server port (used for --cors)
   */
  constructor({ repoRoot, port }) {
    super();
    this.repoRoot = repoRoot;
    this.controlPort = port;
    this.proc = null;
    this.url = null;
    this.port = null;
    this.health = { healthy: false, version: "" };
    this._sseAbort = null;
    this._sseConnected = false;
    this._retryTimer = null;
    this._sseRetryDelay = 2000;
  }

  get running() {
    return this.proc !== null && this.proc.exitCode === null;
  }

  /**
   * Spawn `opencode serve` and wait until it reports healthy.
   */
  async start({ port }) {
    if (this.running) return this._waitHealthy();
    this._manuallyStopped = false;
    this.port = port;
    const args = [
      "serve",
      "--port", String(port),
      "--hostname", "127.0.0.1",
      "--cors", `http://localhost:${this.controlPort}`,
      "--print-logs",
    ];
    this.proc = spawn(OPENCODE_BIN, args, {
      cwd: this.repoRoot,
      env: { ...process.env },
      stdio: ["ignore", "pipe", "pipe"],
      // Own process group so stop() can SIGKILL opencode AND its children
      // (the CUA Gateway / Browser Adapter MCP servers — i.e. the screen
      // capture processes) in one instant kill.
      detached: true,
    });
    this.proc.stdout.on("data", (d) => {
      const line = String(d).trim();
      if (line) this.emit("log", `[opencode] ${line}`);
    });
    this.proc.stderr.on("data", (d) => {
      const line = String(d).trim();
      if (line) this.emit("log", `[opencode:err] ${line}`);
    });
    this.proc.on("error", (err) => {
      this.emit("log", `[opencode] spawn failed: ${err.message} (is 'opencode' on PATH?)`);
      this.proc = null;
      this._scheduleRestart();
    });
    this.proc.on("exit", (code, sig) => {
      this.emit("log", `[opencode] exited code=${code} sig=${sig}`);
      this.emit("exit", { code, sig });
      this.proc = null;
      this._sseConnected = false;
      if (this._sseAbort) {
        this._sseAbort.abort();
        this._sseAbort = null;
      }
      this._scheduleRestart();
    });
    this.url = `http://127.0.0.1:${port}`;
    try {
      await this._waitHealthy(30_000);
      this.emit("started", { url: this.url, version: this.health.version });
      this._startEventStream();
      return this.health;
    } catch (err) {
      this.emit("log", `[opencode] failed to become healthy: ${err.message}`);
      throw err;
    }
  }

  _scheduleRestart() {
    if (this._retryTimer) return;
    this.emit("log", `[opencode] restarting in ${this._sseRetryDelay}ms`);
    this._retryTimer = setTimeout(async () => {
      this._retryTimer = null;
      try {
        await this.start({ port: this.port });
      } catch {
        this._scheduleRestart();
      }
    }, this._sseRetryDelay);
  }

  async stop() {
    if (this._retryTimer) clearTimeout(this._retryTimer);
    this._retryTimer = null;
    this._manuallyStopped = true;
    const proc = this.proc;
    this.proc = null;
    this._sseConnected = false;
    if (this._sseAbort) {
      this._sseAbort.abort();
      this._sseAbort = null;
    }
    if (!proc) return;
    proc.removeAllListeners("exit");
    try {
      // SIGKILL the whole process group: opencode serve + every MCP
      // server it spawned (gateway screen capture, browser adapter).
      // This is the instant "everything off" the STOP button promises.
      process.kill(-proc.pid, "SIGKILL");
      this.emit("log", "[opencode] STOP: killed process group (SIGKILL)");
    } catch (err) {
      this.emit("log", `[opencode] STOP: group kill failed: ${err.message}`);
      try {
        proc.kill("SIGKILL");
      } catch {
        /* already dead */
      }
    }
  }

  async _waitHealthy(timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    let lastErr = null;
    while (Date.now() < deadline) {
      try {
        const res = await this._fetch("/global/health", { timeoutMs: 4000 });
        this.health = res;
        if (res && res.healthy) return res;
      } catch (err) {
        lastErr = err;
      }
      await sleep(500);
    }
    throw lastErr || new Error("health check timed out");
  }

  _startEventStream() {
    if (this._sseAbort || !this.url) return;
    const ctrl = new AbortController();
    this._sseAbort = ctrl;
    this._sseConnected = false;
    const read = async () => {
      try {
        const res = await fetch(`${this.url}/event`, {
          signal: ctrl.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) throw new Error(`event stream HTTP ${res.status}`);
        this._sseConnected = true;
        this._sseRetryDelay = 2000;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n")) !== -1) {
            const line = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 1);
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            try {
              const evt = JSON.parse(payload);
              if (evt && evt.type) this.emit("event", evt);
            } catch {
              /* ignore malformed lines */
            }
          }
        }
      } catch (err) {
        if (!ctrl.signal.aborted) {
          this.emit("log", `[opencode] event stream error: ${err.message}`);
        }
      } finally {
        this._sseConnected = false;
        if (this._sseAbort === ctrl) this._sseAbort = null;
        if (!ctrl.signal.aborted && this.running) {
          setTimeout(() => this._startEventStream(), this._sseRetryDelay);
        }
      }
    };
    read();
  }

  async _fetch(p, { method = "GET", body = null, timeoutMs = 30_000 } = {}) {
    if (!this.url) throw new Error("opencode server not running");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${this.url}${p}`, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: body === null ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
      const text = await res.text();
      let json = null;
      if (text) {
        try {
          json = JSON.parse(text);
        } catch {
          json = text;
        }
      }
      if (!res.ok) {
        const detail = json && json.data ? json.data.message : (text || res.statusText);
        throw new Error(`${method} ${p} -> ${res.status}: ${detail}`);
      }
      return json;
    } finally {
      clearTimeout(timer);
    }
  }

  async healthNow() {
    return this._fetch("/global/health", { timeoutMs: 4000 });
  }

  async listSessions() {
    return this._fetch("/session");
  }

  async createSession(title) {
    return this._fetch("/session", {
      method: "POST",
      body: { title: title || undefined },
    });
  }

  async getSession(id) {
    return this._fetch(`/session/${id}`);
  }

  async deleteSession(id) {
    return this._fetch(`/session/${id}`, { method: "DELETE" });
  }

  async sessionStatus() {
    return this._fetch("/session/status");
  }

  async listMessages(id, limit = 200) {
    return this._fetch(`/session/${id}/message?limit=${limit}`);
  }

  /**
   * Kick off a task without waiting for the response; progress arrives over
   * the shared SSE event stream. Returns the created message info.
   */
  async promptAsync(id, { text, model = null, agent = undefined }) {
    const body = {
      model: model ? { providerID: model.providerID, modelID: model.modelID } : null,
      parts: [{ type: "text", text }],
    };
    if (agent) body.agent = agent;
    return this._fetch(`/session/${id}/prompt_async`, {
      method: "POST",
      body,
      timeoutMs: 15_000,
    });
  }

  async abort(id) {
    return this._fetch(`/session/${id}/abort`, { method: "POST", body: {} });
  }

  async replyPermission(sessionId, permissionId, response, remember = false) {
    return this._fetch(`/session/${sessionId}/permissions/${permissionId}`, {
      method: "POST",
      body: { response, remember },
    });
  }

  async listProviders() {
    return this._fetch("/provider");
  }

  async mcpStatus() {
    return this._fetch("/mcp");
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

module.exports = { OpencodeServer };
