#!/bin/bash
# First-run installer for the distributed opencode-operator.app.
#
# The app bundle ships the whole platform embedded in
# Contents/Resources/platform — including PREBUILT virtualenvs and the
# built frontend — plus vendored runtimes in Contents/Resources/vendor
# (node, uv, opencode, a managed CPython 3.14) and the GUI-agent model in
# Contents/Resources/models. On first launch (or whenever provisioning is
# incomplete/stale) the app runs this script to:
#
#   1. copy the platform into a writable install dir
#      (~/Library/Application Support/opencode-operator/platform)
#   2. install the vendored runtimes next to it (runtime/bin, runtime/python)
#   3. patch the shipped virtualenvs to the installed Python interpreter
#      (pyvenv.cfg home, bin/python symlinks, script shebangs)
#   4. resolve absolute paths in opencode.json / config/mcp.json so the MCP
#      servers launch straight from the venvs (no uv at runtime)
#
# Fully offline: nothing is downloaded. The GUI-agent model is NOT copied —
# it is referenced read-only inside the app bundle via CUA_MODEL_DIR.
#
# Idempotent: safe to re-run on every launch; the app gates it on a
# .provisioned marker + VERSION match + venv health check.
# Prints one line per step on stdout — the app streams these lines into its
# provisioning status view.
#
# Env (set by main.swift):
#   CUA_PLATFORM_SRC    embedded platform directory (read-only source)
#   CUA_INSTALL_DIR     writable install root (target)
#   CUA_BIN_DIR         vendored bin dir in the bundle (node, uv, opencode)
#   CUA_VENDOR_PYTHON   vendored managed-python root dir in the bundle
#   CUA_RUNTIME_BIN     install-dir bin target (…/runtime/bin)
#   CUA_RUNTIME_PYTHON  install-dir python target (…/runtime/python)
#   CUA_MODEL_SRC       bundled model dir (may be empty)

set -euo pipefail

SRC="${CUA_PLATFORM_SRC:?CUA_PLATFORM_SRC required}"
ROOT="${CUA_INSTALL_DIR:?CUA_INSTALL_DIR required}"
DEST="$ROOT/platform"
RUNTIME_BIN="${CUA_RUNTIME_BIN:-$ROOT/runtime/bin}"
RUNTIME_PY="${CUA_RUNTIME_PYTHON:-$ROOT/runtime/python}"

step() { echo "[provision] $1"; }

# ---------------------------------------------------------------------------
step "1/5 installing platform files into $DEST"
mkdir -p "$DEST" "$DEST/logs"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude 'logs' \
  --exclude 'native/build' \
  --exclude 'native/vendor-cache' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '*.egg-info' \
  "$SRC/" "$DEST/"

# ---------------------------------------------------------------------------
step "2/5 installing bundled runtimes (node, uv, opencode, Python 3.14)"
mkdir -p "$RUNTIME_BIN" "$RUNTIME_PY"
for bin in node uv opencode; do
  if [ -f "$CUA_BIN_DIR/$bin" ]; then
    rm -f "$RUNTIME_BIN/$bin"
    cp "$CUA_BIN_DIR/$bin" "$RUNTIME_BIN/$bin"
    chmod +x "$RUNTIME_BIN/$bin"
  fi
done
if [ -n "${CUA_VENDOR_PYTHON:-}" ] && [ -d "$CUA_VENDOR_PYTHON" ]; then
  rsync -a --delete "$CUA_VENDOR_PYTHON/" "$RUNTIME_PY/"
else
  echo "[provision] WARNING: no vendored Python in bundle; venv patching may fail" >&2
fi

PYBIN_DIR=""
for d in "$RUNTIME_PY"/cpython-3.14*/bin; do
  [ -x "$d/python3" ] && PYBIN_DIR="$d"
done
if [ -n "$PYBIN_DIR" ]; then
  # Resolve symlinked version aliases (uv ships cpython-3.14 -> cpython-3.14.x)
  # so the patched paths point at the physical install.
  PYBIN_DIR="$(cd "$PYBIN_DIR" && pwd -P)"
fi
PYBIN="$PYBIN_DIR/python3"
if [ -z "$PYBIN_DIR" ] || [ ! -x "$PYBIN" ]; then
  echo "[provision] ERROR: managed CPython 3.14 not found under $RUNTIME_PY" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
patch_venv() {
  local venv="$1"
  local cfg="$venv/pyvenv.cfg"
  if [ ! -f "$cfg" ]; then
    echo "[provision] WARNING: $cfg missing — skipping venv patch" >&2
    return 0
  fi
  # pyvenv.cfg: point home at the installed managed Python's bin dir.
  sed -i '' "s|^home = .*|home = $PYBIN_DIR|" "$cfg"

  # Interpreter symlinks -> installed python3.
  for link in python python3 python3.14; do
    if [ -L "$venv/bin/$link" ] || [ -e "$venv/bin/$link" ]; then
      ln -sf "$PYBIN" "$venv/bin/$link"
    fi
  done

  # Console-script shebangs -> installed python3.
  local f
  for f in "$venv"/bin/*; do
    case "$(basename "$f")" in
      python|python3|python3.14|activate|activate.csh|activate.fish|Activate.ps1) continue ;;
    esac
    if [ -f "$f" ] && [ "$(head -c 2 "$f")" = "#!" ]; then
      sed -i '' "1s|^#!.*|#!$PYBIN|" "$f"
    fi
  done
}

step "3/5 patching gateway + browser-adapter virtualenvs to the installed Python"
patch_venv "$DEST/gateway/.venv"
patch_venv "$DEST/browser_adapter/.venv"

# ---------------------------------------------------------------------------
step "4/5 resolving MCP server paths in opencode.json / config/mcp.json"
GATEWAY_BIN="$DEST/gateway/.venv/bin/cua-gateway"
BROWSER_BIN="$DEST/browser_adapter/.venv/bin/browser-adapter"
MODEL_DIR="${CUA_MODEL_SRC:-}"
for cfg_file in "$DEST/opencode.json" "$DEST/config/mcp.json"; do
  [ -f "$cfg_file" ] || continue
  sed -i '' \
    -e "s|@CUA_GATEWAY_BIN@|$GATEWAY_BIN|g" \
    -e "s|@BROWSER_ADAPTER_BIN@|$BROWSER_BIN|g" \
    -e "s|@CUA_MODEL_DIR@|$MODEL_DIR|g" \
    "$cfg_file"
done

# ---------------------------------------------------------------------------
step "5/5 finalizing"
touch "$DEST/.provisioned"
echo "[provision] done — platform installed at $DEST"
exit 0
