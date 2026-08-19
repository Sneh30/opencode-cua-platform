"use strict";

const { execFile } = require("node:child_process");

/**
 * macOS application catalog + launcher.
 *
 * Launching uses `open -a` (no automation permissions needed). Running-state
 * detection uses `pgrep -x` on the app's process name.
 */

const CATALOG = [
  { name: "TextEdit", proc: "TextEdit", icon: "📝", desc: "Plain text / rich text editor" },
  { name: "Calculator", proc: "Calculator", icon: "🧮", desc: "Basic + scientific calculator" },
  { name: "Notes", proc: "Notes", icon: "🗒️", desc: "Quick notes app" },
  { name: "Calendar", proc: "Calendar", icon: "📅", desc: "Calendar & events" },
  { name: "Reminders", proc: "Reminders", icon: "✅", desc: "To-do lists" },
  { name: "Safari", proc: "Safari", icon: "🧭", desc: "Apple's web browser" },
  { name: "Chrome", proc: "Google Chrome", icon: "🌐", desc: "Google Chrome browser" },
  { name: "Finder", proc: "Finder", icon: "📁", desc: "File manager" },
  { name: "Terminal", proc: "Terminal", icon: "💻", desc: "Command line terminal" },
  { name: "Mail", proc: "Mail", icon: "✉️", desc: "Email client" },
  { name: "Messages", proc: "Messages", icon: "💬", desc: "iMessage client" },
  { name: "Preview", proc: "Preview", icon: "🖼️", desc: "PDF & image viewer" },
  { name: "Music", proc: "Music", icon: "🎵", desc: "Apple Music" },
  { name: "Photos", proc: "Photos", icon: "🖼️", desc: "Photo library" },
  { name: "Maps", proc: "Maps", icon: "🗺️", desc: "Apple Maps" },
  { name: "Notes", proc: "Notes", icon: "🗒️", desc: "Quick notes" },
  { name: "Stocks", proc: "Stocks", icon: "📈", desc: "Stock tracker" },
  { name: "Weather", proc: "Weather", icon: "🌤️", desc: "Weather app" },
  { name: "Podcasts", proc: "Podcasts", icon: "🎙️", desc: "Podcast player" },
  { name: "Numbers", proc: "Numbers", icon: "🔢", desc: "Spreadsheets" },
  { name: "Pages", proc: "Pages", icon: "📄", desc: "Word processing" },
  { name: "Keynote", proc: "Keynote", icon: "📊", desc: "Presentations" },
  { name: "Freeform", proc: "Freeform", icon: "🎨", desc: "Whiteboard canvas" },
  { name: "VSCode", proc: "Code", icon: "🧩", desc: "Visual Studio Code" },
  { name: "iTerm2", proc: "iTerm2", icon: "🖥️", desc: "Terminal replacement" },
  { name: "Slack", proc: "Slack", icon: "💼", desc: "Team chat" },
  { name: "Discord", proc: "Discord", icon: "🎮", desc: "Community chat" },
  { name: "Zoom", proc: "zoom.us", icon: "📹", desc: "Video meetings" },
  { name: "Spotify", proc: "Spotify", icon: "🎧", desc: "Music streaming" },
  { name: "Notion", proc: "Notion", icon: "📚", desc: "Notes & docs" },
  { name: "System Settings", proc: "System Settings", icon: "⚙️", desc: "macOS settings" },
  { name: "Activity Monitor", proc: "Activity Monitor", icon: "📊", desc: "Process monitor" },
];

const seen = new Set();
const APPS = [];
for (const a of CATALOG) {
  if (seen.has(a.name + "|" + a.proc)) continue;
  seen.add(a.name + "|" + a.proc);
  APPS.push(a);
}

function run(cmd, args, timeoutMs = 8000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: timeoutMs }, (err, stdout, stderr) => {
      resolve({ err: err ? err.message : null, stdout, stderr });
    });
  });
}

async function isRunning(procName) {
  const { err } = await run("/usr/bin/pgrep", ["-x", procName], 3000);
  return err === null;
}

async function runningStatus() {
  const out = {};
  await Promise.all(
    APPS.map(async (a) => {
      out[a.name + "|" + a.proc] = await isRunning(a.proc);
    })
  );
  return out;
}

async function launch(name, { path: appPath = null } = {}) {
  const args = appPath ? [appPath] : ["-a", name];
  const { err, stderr } = await run("/usr/bin/open", args, 15_000);
  if (err) return { ok: false, error: stderr || err };
  return { ok: true };
}

async function activate(name) {
  return launch(name);
}

module.exports = { APPS, isRunning, runningStatus, launch, activate };
