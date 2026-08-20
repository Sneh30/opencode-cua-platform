#!/usr/bin/env python3
"""Provision the GUI-agent model (UI-TARS) into a local directory.

Used by the distributed app's first-run provisioning so the model is
bundled/pre-downloaded and the gateway can run with CUA_MODEL_DIR set —
no Hugging Face round-trip at runtime.

Usage:
    python scripts/download_model.py [target_dir]
    # target_dir defaults to $CUA_MODEL_DIR or ~/.cache/cua/models/UI-TARS-1.5-7B-4bit
    # model defaults to $CUA_GATEWAY_MODEL or mlx-community/UI-TARS-1.5-7B-4bit

Idempotent: skips the download when the target already contains a
config.json (the marker mlx-vlm needs to load a local dir).
"""

import os
import sys
from pathlib import Path

DEFAULT_MODEL = "mlx-community/UI-TARS-1.5-7B-4bit"
DEFAULT_TARGET = Path.home() / ".cache" / "cua" / "models" / "UI-TARS-1.5-7B-4bit"


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        os.environ.get("CUA_MODEL_DIR", str(DEFAULT_TARGET))
    )
    model_id = os.environ.get("CUA_GATEWAY_MODEL", DEFAULT_MODEL)

    if (target / "config.json").is_file():
        print(f"[model] already present at {target}; nothing to do")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    print(f"[model] downloading {model_id} -> {target} (this is a ~5 GB checkpoint; be patient)")
    sys.stdout.flush()

    from huggingface_hub import snapshot_download

    snapshot_download(model_id, local_dir=str(target), max_workers=4)

    if not (target / "config.json").is_file():
        print(f"[model] ERROR: download finished but {target}/config.json is missing", file=sys.stderr)
        return 1
    print(f"[model] done: {model_id} provisioned at {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())