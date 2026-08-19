"use strict";

/**
 * Smoke test for the CUA Control Server.
 *
 * Spawns the control server on a scratch port (with a scratch opencode port),
 * then exercises the API: health, SSE stream, session create, task run,
 * screen capture, apps. Prints a PASS/FAIL summary; exits non-zero on failure.
 *
 * Usage: node scripts/smoke-test.js
 */

const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = path.resolve(__dirname, "..", "..");
const SERVER = path.join(__dirname, "..", "server", "index.js");
const PORT = Number(process.env.SMOKE_PORT) || 18991;
const OPENCODE_PORT = Number(process.env.SMOKE_OPENCODE_PORT) || 18992;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

function get(url, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => resolve({ status: res.statusCode, body: data }));
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", reject);
  });
}

function post(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body || {});
    const req = http.request(url, { method: "POST", headers: { "Content-Type": "application/json" } }, (res) => {
      let out = "";
      res.on("data", (c) => (out += c));
      res.on("end", () => resolve({ status: res.statusCode, body: out }));
    });
    req.setTimeout(8000, () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(data);
  });
}

function readSse(url, timeoutMs, onEvent) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let buf = "";
      res.on("data", (c) => {
        buf += c.toString();
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            try {
              const evt = JSON.parse(payload);
              if (evt.type === "hello") {
                onEvent(evt);
                resolve(evt);
                req.destroy();
                return;
              }
            } catch {
              /* ignore */
            }
          }
        }
      });
      res.on("end", () => reject(new Error("stream ended before hello")));
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("sse timeout"));
      reject(new Error("sse timeout"));
    });
    req.on("error", (e) => {
      if (e.message !== "socket hang up") reject(e);
    });
  });
}

async function waitStatus(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await get(`http://127.0.0.1:${PORT}/api/status`, 3000);
      if (r.status === 200) {
        const body = JSON.parse(r.body);
        if (body.healthy) return body;
      }
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  throw new Error("control server did not become healthy");
}

async function waitMcp(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await get(`http://127.0.0.1:${PORT}/api/status`, 3000);
      const body = JSON.parse(r.body);
      if (body.mcp?.["cua-gateway"]?.status === "connected") return body;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("MCP servers did not connect");
}

async function main() {
  console.log(`control server on :${PORT}, opencode on :${OPENCODE_PORT}`);
  const child = spawn(process.execPath, [SERVER], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(PORT), OPENCODE_PORT: String(OPENCODE_PORT) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let serverLog = "";
  child.stdout.on("data", (d) => (serverLog += d));
  child.stderr.on("data", (d) => (serverLog += d));

  const kill = () => {
    try {
      child.kill("SIGTERM");
    } catch {}
  };

  try {
    const status = await waitStatus(60_000);
    check("control server up", !!status.healthy, `version ${status.version}`);
    check("opencode spawns", status.opencodeUrl === `http://127.0.0.1:${OPENCODE_PORT}`);

    let finalStatus = status;
    try {
      finalStatus = await waitMcp(120_000);
      check("MCP registered", true, "cua-gateway connected");
    } catch (err) {
      check("MCP registered", false, err.message);
    }
    const mcp = finalStatus.mcp || {};

    const hello = await readSse(`http://127.0.0.1:${PORT}/api/event`, 5000, () => {});
    check("SSE stream hello", !!hello, "received hello event");

    const s = await post(`http://127.0.0.1:${PORT}/api/sessions`, { title: "smoke-test" });
    let session;
    try {
      session = JSON.parse(s.body);
    } catch {
      session = {};
    }
    check("session create", s.status === 200 && !!session.id, `HTTP ${s.status} ${String(s.body).slice(0, 200)}`);

    const task = await post(`http://127.0.0.1:${PORT}/api/session/${session.id}/task`, {
      text: "reply with the single word: accepted",
      model: null,
    });
    check("task start (prompt_async)", task.status === 202, `HTTP ${task.status} ${String(task.body).slice(0, 200)}`);

    let done = false;
    let messageCount = 0;
    let lastErr = "";
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline && !done) {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        const msgs = await get(`http://127.0.0.1:${PORT}/api/session/${session.id}/messages`);
        const list = JSON.parse(msgs.body);
        messageCount = list.length;
        const last = list[list.length - 1];
        if (last?.info?.role === "assistant" && last?.info?.finish) done = true;
        else if (last?.info?.error) lastErr = last.info.error.data?.message || "assistant error";
      } catch (e) {
        lastErr = e.message;
      }
    }
    check("task completed", done, `messages=${messageCount}${lastErr ? " err=" + lastErr : ""}`);

    const screen = await get(`http://127.0.0.1:${PORT}/api/screen.jpg`, 20_000);
    check("screen capture", screen.status === 200, `${screen.status} ${(screen.body || "").length} bytes`);

    const apps = await get(`http://127.0.0.1:${PORT}/api/apps`);
    const appsJson = JSON.parse(apps.body);
    check("apps catalog", appsJson.apps?.length > 10, `${appsJson.apps?.length} apps`);

    const names = Object.keys(mcp);
    check("MCP registered", names.includes("cua-gateway") || names.includes("browser-adapter"), names.join(","));

    const log = await get(`http://127.0.0.1:${PORT}/api/logs`);
    check("logs endpoint", log.status === 200);
  } catch (err) {
    check("smoke run", false, err.message);
    console.log("\n--- server log tail ---\n" + serverLog.slice(-3000));
  } finally {
    kill();
  }

  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}

main();
