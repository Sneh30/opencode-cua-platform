#!/bin/bash
# Build the self-contained opencode-operator distribution.
#
#   ./bundle.sh            -> native/bundle/opencode-operator.app
#                             native/dist/opencode-operator.dmg (+ loose .app copy)
#
# ONE application, zero external setup for the recipient:
#   - Contents/MacOS/opencode-operator     native launcher (Swift)
#   - Contents/Resources/platform          the whole platform, incl. PREBUILT
#                                          virtualenvs (managed CPython 3.14)
#                                          and the built control UI (dist/)
#   - Contents/Resources/vendor/bin        node, uv, opencode binaries
#   - Contents/Resources/vendor/python     managed CPython 3.14 install(s)
#   - Contents/Resources/models            UI-TARS-1.5-7B-4bit (~4.5 GB) —
#                                          the GUI-agent model lives INSIDE
#                                          the app; nothing downloads at runtime
#   - Contents/Resources/provision.sh      first-run installer (offline)
#
# On the recipient Mac: double-click -> provisioning copies the platform +
# runtimes to ~/Library/Application Support/opencode-operator, patches the
# venvs to the installed Python, resolves MCP paths, boots everything. No
# network, no Homebrew, no dev tools needed.
#
# Build-machine prerequisites: node+npm (frontend build), uv, opencode,
# swiftc, hdiutil, and the UI-TARS model in the HF cache (downloaded
# automatically if absent). Everything lands in native/vendor-cache first,
# so re-runs are incremental.
#
# Env overrides:
#   NODE_VERSION   (default v24.17.0)  node dist to vendor
#   OPENCODE_SRC   (default: opencode on PATH / ~/.opencode/bin)
#   UV_SRC         (default: uv on PATH / ~/.local/bin)
#   FORCE=1        wipe vendor-cache and rebuild everything

set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="opencode-operator"
ROOT="$(cd .. && pwd)"                 # platform repo root
CACHE="vendor-cache"
BUNDLE="bundle"
DIST="dist"
NODE_VERSION="${NODE_VERSION:-v24.17.0}"
MODEL_ID="mlx-community/UI-TARS-1.5-7B-4bit"
HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${MODEL_ID//\//--}"

log() { echo "[bundle] $*"; }
die() { echo "[bundle] ERROR: $*" >&2; exit 1; }

mkdir -p "$CACHE" "$BUNDLE" "$DIST"
[ "${FORCE:-0}" = "1" ] && rm -rf "$CACHE"/*

# ---------------------------------------------------------------------------
log "0/8 preflight"
command -v npm >/dev/null    || die "npm not found (needed to build the control UI)"
command -v uv >/dev/null     || die "uv not found (https://docs.astral.sh/uv/)"
command -v swiftc >/dev/null || die "swiftc not found (Xcode command line tools)"
command -v hdiutil >/dev/null || die "hdiutil not found"
ARCH="$(uname -m)"
[ "$ARCH" = "arm64" ] || die "this build targets Apple Silicon (mlx); got $ARCH"

OPENCODE_SRC="${OPENCODE_SRC:-$(command -v opencode || true)}"
[ -z "$OPENCODE_SRC" ] && OPENCODE_SRC="$HOME/.opencode/bin/opencode"
[ -x "$OPENCODE_SRC" ] || die "opencode binary not found (set OPENCODE_SRC)"

UV_SRC="${UV_SRC:-$(command -v uv || true)}"
[ -x "$UV_SRC" ] || die "uv binary not found (set UV_SRC)"

# ---------------------------------------------------------------------------
log "1/8 building the control UI (frontend/dist)"
(
  cd "$ROOT/frontend"
  [ -d node_modules ] || npm install --no-audit --no-fund
  npm run build
)

# ---------------------------------------------------------------------------
log "2/8 fetching a managed CPython 3.14 (relocatable, python-build-standalone)"
export UV_PYTHON_INSTALL_DIR="$PWD/$CACHE/python"
if [ ! -d "$CACHE/python" ] || ! ls "$CACHE/python"/cpython-3.14* >/dev/null 2>&1; then
  uv python install 3.14
fi
MANAGED_PY="$(ls -d "$CACHE/python"/cpython-3.14*/ | head -n1)"
MANAGED_PYBIN="${MANAGED_PY}bin/python3"
[ -x "$MANAGED_PYBIN" ] || die "managed CPython missing at $MANAGED_PYBIN"

# ---------------------------------------------------------------------------
log "3/8 building clean virtualenvs against the managed Python (gateway, browser adapter)"
if [ ! -x "$CACHE/venv-gateway/bin/cua-gateway" ]; then
  UV_PROJECT_ENVIRONMENT="$PWD/$CACHE/venv-gateway" \
    uv sync --project "$ROOT/gateway" --frozen --python "$MANAGED_PYBIN"
fi
if [ ! -x "$CACHE/venv-browser/bin/browser-adapter" ]; then
  UV_PROJECT_ENVIRONMENT="$PWD/$CACHE/venv-browser" \
    uv sync --project "$ROOT/browser_adapter" --frozen --python "$MANAGED_PYBIN"
fi

# ---------------------------------------------------------------------------
log "4/8 vendoring node ($NODE_VERSION), uv, opencode"
if [ ! -x "$CACHE/node/bin/node" ]; then
  TARBALL="$CACHE/node-$NODE_VERSION-darwin-arm64.tar.gz"
  curl -fL --retry 3 -o "$TARBALL" \
    "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-darwin-arm64.tar.gz"
  mkdir -p "$CACHE/node-tmp"
  tar -xzf "$TARBALL" -C "$CACHE/node-tmp" --strip-components=1
  rm -rf "$CACHE/node" && mv "$CACHE/node-tmp" "$CACHE/node"
  rm -f "$TARBALL"
fi
mkdir -p "$CACHE/vendor-bin"
cp "$CACHE/node/bin/node" "$CACHE/vendor-bin/node"
cp "$UV_SRC" "$CACHE/vendor-bin/uv"
cp "$OPENCODE_SRC" "$CACHE/vendor-bin/opencode"
chmod +x "$CACHE/vendor-bin/"*
"$CACHE/vendor-bin/node" --version >/dev/null || die "vendored node binary does not run"
"$CACHE/vendor-bin/opencode" --version >/dev/null 2>&1 || log "WARNING: vendored opencode --version failed (continuing)"

# ---------------------------------------------------------------------------
log "5/8 staging the embedded platform (code + prebuilt venvs + built UI)"
STAGE="$BUNDLE/platform"
rm -rf "$STAGE"
mkdir -p "$STAGE"
rsync -a \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude 'logs' \
  --exclude 'native/build' \
  --exclude 'native/bundle' \
  --exclude 'native/dist' \
  --exclude 'native/vendor-cache' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '*.egg-info' \
  --exclude '.DS_Store' \
  "$ROOT/" "$STAGE/"

# prebuilt virtualenvs (paths are patched on the target by provision.sh)
rm -rf "$STAGE/gateway/.venv" "$STAGE/browser_adapter/.venv"
rsync -a "$CACHE/venv-gateway/" "$STAGE/gateway/.venv/"
rsync -a "$CACHE/venv-browser/" "$STAGE/browser_adapter/.venv/"

# VERSION marker: drives re-provisioning when a newer app is launched
VERSION_ID="$(cd "$ROOT" && git describe --tags --always --dirty 2>/dev/null || echo "dev-$(date +%Y%m%d%H%M%S)")"
echo "$VERSION_ID ($(date -u +%Y-%m-%dT%H:%M:%SZ))" > "$STAGE/VERSION"

# MCP registration: placeholders resolved to absolute installed paths by
# provision.sh (dev repo keeps its own uv-run based config untouched).
"$CACHE/vendor-bin/node" -e '
const fs = require("fs");
for (const rel of ["opencode.json", "config/mcp.json"]) {
  const f = process.argv[1] + "/" + rel;
  if (!fs.existsSync(f)) continue;
  const j = JSON.parse(fs.readFileSync(f, "utf8"));
  if (j.mcp && j.mcp["cua-gateway"]) {
    j.mcp["cua-gateway"].command = ["@CUA_GATEWAY_BIN@"];
    j.mcp["cua-gateway"].environment = Object.assign({}, j.mcp["cua-gateway"].environment, {
      CUA_MODEL_DIR: "@CUA_MODEL_DIR@"
    });
  }
  if (j.mcp && j.mcp["browser-adapter"]) {
    j.mcp["browser-adapter"].command = ["@BROWSER_ADAPTER_BIN@"];
  }
  fs.writeFileSync(f, JSON.stringify(j, null, 2) + "\n");
}
console.log("[bundle] staged MCP configs point at venv entry points");
' "$STAGE"

# ---------------------------------------------------------------------------
log "6/8 compiling the launcher + assembling $APP_NAME.app"
APP="$BUNDLE/$APP_NAME.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp Info.plist "$APP/Contents/Info.plist"
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp provision.sh "$APP/Contents/Resources/provision.sh"
chmod +x "$APP/Contents/Resources/provision.sh"

echo "[bundle] swiftc main.swift…"
swiftc -O -o "$APP/Contents/MacOS/$APP_NAME" main.swift

echo "[bundle] embedding platform ($(du -sh "$STAGE" | cut -f1))…"
rsync -a --delete "$STAGE/" "$APP/Contents/Resources/platform/"

echo "[bundle] vendoring runtimes…"
mkdir -p "$APP/Contents/Resources/vendor/bin" "$APP/Contents/Resources/vendor/python"
cp "$CACHE/vendor-bin/node" "$CACHE/vendor-bin/uv" "$CACHE/vendor-bin/opencode" \
   "$APP/Contents/Resources/vendor/bin/"
rsync -a --delete "$CACHE/python/" "$APP/Contents/Resources/vendor/python/"

# ---------------------------------------------------------------------------
log "7/8 embedding the GUI-agent model ($MODEL_ID, ~4.5 GB) inside the app"
SNAPSHOT="$(ls -d "$HF_HUB"/snapshots/*/ 2>/dev/null | head -n1 || true)"
if [ -z "$SNAPSHOT" ]; then
  echo "[bundle] model not in HF cache — downloading once (build machine only)…"
  UV_PYTHON_INSTALL_DIR="$PWD/$CACHE/python" \
  UV_PROJECT_ENVIRONMENT="$PWD/$CACHE/venv-gateway" \
    uv run --project "$ROOT/gateway" --frozen \
    python "$ROOT/scripts/download_model.py" "$CACHE/model"
  SNAPSHOT="$CACHE/model/"
fi
mkdir -p "$APP/Contents/Resources/models/UI-TARS-1.5-7B-4bit"
echo "[bundle] copying model ($(du -sh "$SNAPSHOT" | cut -f1))…"
rsync -aL --delete "$SNAPSHOT" "$APP/Contents/Resources/models/UI-TARS-1.5-7B-4bit/"
[ -f "$APP/Contents/Resources/models/UI-TARS-1.5-7B-4bit/config.json" ] \
  || die "model copy incomplete (config.json missing)"

# ---------------------------------------------------------------------------
log "8/8 signing + packaging DMG"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

rm -rf "$DIST"
mkdir -p "$DIST"
cp -R "$APP" "$DIST/$APP_NAME.app"

DMG_SRC="$CACHE/dmg-src"
rm -rf "$DMG_SRC"
mkdir -p "$DMG_SRC"
cp -R "$APP" "$DMG_SRC/$APP_NAME.app"
ln -s /Applications "$DMG_SRC/Applications"
echo "[bundle] compressing DMG (several GB — be patient)…"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_SRC" -ov -format UDZO \
  "$DIST/$APP_NAME.dmg" >/dev/null
rm -rf "$DMG_SRC"

echo
echo "Built:"
echo "  $BUNDLE/$APP_NAME.app   (run in place)"
echo "  $DIST/$APP_NAME.app     (loose copy)"
echo "  $DIST/$APP_NAME.dmg     (distributable)"
du -sh "$DIST/$APP_NAME.app" "$DIST/$APP_NAME.dmg"
