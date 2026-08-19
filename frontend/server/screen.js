"use strict";

const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const WORK = path.join(os.tmpdir(), "cua-control");
fs.mkdirSync(WORK, { recursive: true });

const SCREEN_JPG = path.join(WORK, "screen.jpg");

function run(cmd, args, timeoutMs = 15_000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: timeoutMs }, (err, stdout, stderr) => {
      resolve({ err: err ? err.message : null, stdout, stderr });
    });
  });
}

let lastCapture = { at: 0, bytes: 0, ok: false, error: null };
let inflight = null;

/**
 * Capture the main display as a downscaled JPEG.
 *
 * Uses /usr/sbin/screencapture (works when the responsible terminal has
 * Screen Recording permission), then resizes with sips. Falls back to the
 * CUA Gateway's own adapter (via the gateway venv python) if screencapture
 * fails — the venv interpreter is the one with the TCC grants.
 */
async function capture() {
  if (inflight) return inflight;
  inflight = (async () => {
    const raw = path.join(WORK, "raw.png");
    const now = Date.now();
    const res = await run("/usr/sbin/screencapture", ["-x", raw]);
    let err = res.err;
    if (!err && !fs.existsSync(raw)) err = "screencapture produced no file";
    if (!err && fs.statSync(raw).size === 0) err = "screencapture produced empty file";

    if (!err) {
      try {
        const jpg = path.join(WORK, "screen-full.jpg");
        const resize = await run("/usr/bin/sips", [
          "-s", "format", "jpeg",
          "-s", "formatOptions", "62",
          "-Z", "1500",
          raw, "--out", jpg,
        ]);
        if (!resize.err) {
          fs.renameSync(jpg, SCREEN_JPG);
          lastCapture = {
            at: now,
            bytes: fs.statSync(SCREEN_JPG).size,
            ok: true,
            error: null,
          };
        } else {
          err = resize.err;
        }
      } catch (e) {
        err = e.message;
      }
    }

    if (err) {
      const fallback = await captureViaGateway();
      if (fallback) {
        lastCapture = { at: now, bytes: fs.statSync(SCREEN_JPG).size, ok: true, error: null };
        err = null;
      } else {
        lastCapture = { at: now, bytes: 0, ok: false, error: err };
      }
    }

    fs.rmSync(raw, { force: true });
    fs.rmSync(path.join(WORK, "screen-full.jpg"), { force: true });
    return { ...lastCapture, path: SCREEN_JPG };
  })();
  try {
    return await inflight;
  } finally {
    inflight = null;
  }
}

/**
 * Fallback: ask the CUA Gateway's venv python (the process that already has
 * Screen Recording + Accessibility TCC grants) to capture the screen through
 * its own MacOSAdapter and emit a JPEG via sips.
 */
async function captureViaGateway() {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const venvPython = path.join(repoRoot, "gateway", ".venv", "bin", "python");
  const helper = path.join(__dirname, "capture_fallback.py");
  if (!fs.existsSync(venvPython) || !fs.existsSync(helper)) return null;
  const raw = path.join(WORK, "raw-gw.png");
  fs.rmSync(raw, { force: true });
  const res = await run(venvPython, [helper, raw], 20_000);
  if (res.err || !fs.existsSync(raw)) {
    fs.rmSync(raw, { force: true });
    return null;
  }
  try {
    const jpg = path.join(WORK, "screen-gw.jpg");
    const resize = await run("/usr/bin/sips", [
      "-s", "format", "jpeg",
      "-s", "formatOptions", "62",
      "-Z", "1500",
      raw, "--out", jpg,
    ]);
    fs.rmSync(raw, { force: true });
    if (resize.err) return null;
    fs.renameSync(jpg, SCREEN_JPG);
    return true;
  } catch {
    fs.rmSync(raw, { force: true });
    return null;
  }
}

function screenFile() {
  return fs.existsSync(SCREEN_JPG) ? SCREEN_JPG : null;
}

function lastCaptureInfo() {
  return { ...lastCapture, exists: fs.existsSync(SCREEN_JPG) };
}

module.exports = { capture, screenFile, lastCaptureInfo, WORK };
