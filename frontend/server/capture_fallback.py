#!/usr/bin/env python3
"""Fallback screen capture for the CUA control server.

Uses the CUA Gateway's own MacOSAdapter (same code path the gateway uses for
observe/act screenshots) so capture works whenever the gateway's venv python
has Screen Recording permission — even if the control server's own process
does not.

Usage: capture_fallback.py <output.png>
"""

import sys

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gateway.os_adapter import MacOSAdapter  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: capture_fallback.py <output.png>", file=sys.stderr)
        sys.exit(2)
    out = Path(sys.argv[1])
    adapter = MacOSAdapter()
    shot = adapter.capture_screen(out)
    print(f"captured {shot.ref} {shot.width}x{shot.height}")


if __name__ == "__main__":
    main()
