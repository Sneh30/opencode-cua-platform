const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const SERVER = path.join(__dirname, "..", "server", "index.js");
const PORT = Number(process.env.BE2E_PORT) || 19991;
const OPENCODE_PORT = Number(process.env.BE2E_OPENCODE_PORT) || 19992;

function get(url, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => resolve({ status: res.statusCode, body }));
    });
    req.setTimeout(timeoutMs, () => req.destroy());
    req.on("error", () => resolve({ status: 0, body: "" }));
  });
}

function post(url, body) {
  const data = JSON.stringify(body || {});
  return new Promise((resolve) => {
    const req = http.request(
      url,
      { method: "POST", headers: { "Content-Type": "application/json" } },
      (res) => {
        let b = "";
        res.on("data", (d) => (b += d));
        res.on("end", () => resolve({ status: res.statusCode, body: b }));
      }
    );
    req.on("error", () => resolve({ status: 0, body: "" }));
    req.setTimeout(15000, () => req.destroy());
    req.end(data);
  });
}

async function waitStatus(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await get(`http://127.0.0.1:${PORT}/api/status`, 3000);
    if (r.status === 200) {
      const b = JSON.parse(r.body);
      if (b.healthy) return b;
    }
    await new Promise((r2) => setTimeout(r2, 2000));
  }
  throw new Error("control server not healthy");
}

async function waitMcp(name, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await get(`http://127.0.0.1:${PORT}/api/status`, 3000);
    if (r.status === 200) {
      const b = JSON.parse(r.body);
      if (b.mcp?.[name]?.status === "connected") return true;
    }
    await new Promise((r2) => setTimeout(r2, 3000));
  }
  throw new Error(`${name} not connected`);
}

async function main() {
  console.log(`browser e2e: control :${PORT}, opencode :${OPENCODE_PORT}`);
  const child = spawn(process.execPath, [SERVER], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(PORT), OPENCODE_PORT: String(OPENCODE_PORT) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let serverLog = "";
  child.stdout.on("data", (d) => (serverLog += d));
  child.stderr.on("data", (d) => (serverLog += d));
  const kill = () => { try { child.kill("SIGTERM"); } catch {} };

  try {
    await waitStatus(60_000);
    console.log("control server healthy");
    await waitMcp("browser-adapter", 180_000);
    console.log("browser-adapter MCP connected");

    const s = await post(`http://127.0.0.1:${PORT}/api/sessions`, { title: "browser-e2e" });
    const session = JSON.parse(s.body);
    console.log("session", session.id);

    const task = await post(`http://127.0.0.1:${PORT}/api/session/${session.id}/task`, {
      text:
        "Follow these exact steps using the browser-adapter MCP tools. Step 1: call browser_act with " +
        'action={"type":"navigate","target_hint":"https://example.com","params":{"url":"https://example.com"}} ' +
        "Step 2: call browser_act with " +
        'action={"type":"extract","target_hint":"main heading","params":{"text":"h1"}} ' +
        "Step 3: reply with only the exact text extracted in step 2, nothing else.",
      model: null,
    });
    console.log("task start HTTP", task.status);

    const tools = new Set();
    let lastText = "";
    const want = "Example Domain";
    for (let attempt = 1; attempt <= 3 && !lastText.includes(want); attempt++) {
      if (attempt > 1) {
        console.log(`retry ${attempt - 1}: model produced inconclusive output, re-sending task`);
        await post(`http://127.0.0.1:${PORT}/api/session/${session.id}/task`, {
          text: "Continue the same steps: if you have not yet extracted the h1 text of https://example.com, call browser_act with action={\"type\":\"extract\",\"target_hint\":\"main heading\",\"params\":{\"text\":\"h1\"}} and then reply with only that extracted text.",
          model: null,
        });
      }
      const deadline = Date.now() + 4 * 60_000;
      let done = false;
      while (Date.now() < deadline && !done) {
        await new Promise((r) => setTimeout(r, 2000));
        const msgs = await get(`http://127.0.0.1:${PORT}/api/session/${session.id}/messages`, 8000);
        const list = JSON.parse(msgs.body);
        for (const m of list) {
          if (m.parts) {
            for (const p of m.parts) {
              if (p.type === "tool") tools.add(p.tool);
            }
            const textPart = m.parts.find((p) => p.type === "text" && p.text && p.text.trim());
            if (m.info?.role === "assistant" && m.info?.finish === "stop" && textPart) {
              lastText = textPart.text;
              done = true;
            }
          }
        }
      }
      if (!done) console.log(`attempt ${attempt} timed out`);
    }

    console.log("tools invoked:", [...tools].join(", "));
    console.log("final answer:", JSON.stringify(lastText.slice(0, 300)));
    const ok =
      lastText.includes(want) &&
      [...tools].some((t) => t.includes("browser_"));
    console.log(ok ? "BROWSER E2E PASS" : "BROWSER E2E FAIL");
    process.exitCode = ok ? 0 : 1;
  } catch (err) {
    console.error("FAIL:", err.message);
    console.log("--- server log tail ---\n" + serverLog.slice(-2000));
    process.exitCode = 1;
  } finally {
    kill();
  }
}

main();