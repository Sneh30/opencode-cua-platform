"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { EventEmitter } = require("node:events");

/**
 * Watches the platform `logs/` directory: structured JSONL session logs from
 * the Gateway / Browser Adapter, plus the screenshots the gateway writes
 * (observe.png / act.png). Emits append + screenshot events.
 */
class GatewayLogs extends EventEmitter {
  constructor(logsDir) {
    super();
    this.logsDir = logsDir;
    this.files = new Map(); // filename -> { size, entries: [] }
    this.screenshots = {}; // name -> mtime
    this._watcher = null;
    this._pollTimer = null;
    this._pollInterval = 1500;
  }

  start() {
    this._scan();
    try {
      this._watcher = fs.watch(this.logsDir, { persistent: false }, () => this._scan());
    } catch {
      /* watch may fail on some filesystems; fall back to polling */
    }
    this._pollTimer = setInterval(() => {
      this._scanScreenshots();
      if (!this._watcher) this._scan();
    }, this._pollInterval);
    if (this._pollTimer.unref) this._pollTimer.unref();
  }

  stop() {
    if (this._watcher) this._watcher.close();
    if (this._pollTimer) clearInterval(this._pollTimer);
  }

  _scan() {
    let names = [];
    try {
      names = fs.readdirSync(this.logsDir).filter((n) => /^session-.*\.jsonl$/.test(n));
    } catch {
      return;
    }
    for (const name of names) this._scanFile(name);
    this._scanScreenshots();
  }

  _scanFile(name) {
    const full = path.join(this.logsDir, name);
    let stat;
    try {
      stat = fs.statSync(full);
    } catch {
      return;
    }
    const rec = this.files.get(name) || { size: 0, entries: [] };
    if (stat.size === rec.size) {
      this.files.set(name, rec);
      return;
    }
    if (stat.size < rec.size) {
      // rotated/truncated — start over
      rec.size = 0;
      rec.entries = [];
    }
    try {
      const fd = fs.openSync(full, "r");
      fs.readSync(fd, Buffer.alloc(0), 0, 0, stat.size); // no-op, keeps types happy
      const chunk = Buffer.alloc(stat.size - rec.size);
      fs.readSync(fd, chunk, 0, chunk.length, rec.size);
      fs.closeSync(fd);
      for (const line of chunk.toString("utf8").split("\n")) {
        if (!line.trim()) continue;
        let evt;
        try {
          evt = JSON.parse(line);
        } catch {
          continue;
        }
        rec.entries.push(evt);
        this.emit("append", { file: name, event: evt });
      }
      rec.size = stat.size;
    } catch {
      /* file vanished mid-read */
    }
    this.files.set(name, rec);
  }

  _scanScreenshots() {
    const shots = {};
    for (const name of ["observe.png", "act.png"]) {
      const full = path.join(this.logsDir, name);
      try {
        const st = fs.statSync(full);
        shots[name] = { at: st.mtimeMs, size: st.size, path: `/api/gateway-screenshot/${name}?v=${Math.round(st.mtimeMs)}` };
      } catch {
        /* not present yet */
      }
    }
    const changed =
      Object.keys(shots).some((k) => this.screenshots[k] !== shots[k]?.at) ||
      Object.keys(this.screenshots).some((k) => !shots[k]);
    this.screenshots = shots;
    if (changed) this.emit("screenshots", shots);
    return shots;
  }

  snapshot() {
    return {
      files: Object.fromEntries(
        [...this.files.entries()].map(([name, rec]) => [name, rec.entries])
      ),
      screenshots: this._scanScreenshots(),
    };
  }
}

module.exports = { GatewayLogs };
