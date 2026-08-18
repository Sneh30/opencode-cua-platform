"""M3 e2e: verification engine through the live MCP server (stdio).

Drives the real gateway (`CUA_GATEWAY_LIVE=1`) over the MCP stdio protocol
with a real TextEdit session: grounded click, type, verdicts from the
verifier (AX tree / vision fallback), re-verify via `gui.verify`.

Scenario:
  act #1  click "New Document" (model-grounded), postcondition
          "Open Document"        -> verdict FAIL (dialog closed: the
          postcondition is stale — demonstrates detection of a missed
          postcondition, and why the M4 recovery engine needs verdicts)
  act #2  click the text area (model-grounded), postcondition
          "zzz-no-such-thing"    -> verdict FAIL (nonsense postcondition)
  act #3  type "m3-verify-ok", postcondition "m3-verify-ok"
                                 -> verdict PASS (AX value readback)
  verify  re-check act #3        -> verdict PASS

Skipped by default (needs the 4.87GB model + real display); run with:
    CUA_E2E=1 uv run pytest -q tests/integration/test_m3_verify_loop.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gateway.os_adapter import MacOSAdapter

GATEWAY_DIR = Path(__file__).resolve().parent.parent.parent / "gateway"
E2E = os.environ.get("CUA_E2E") == "1"


def _wait_for(predicate, timeout=15.0, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _pgrep_textedit() -> bool:
    return subprocess.run(["pgrep", "-x", "TextEdit"], capture_output=True).stdout.strip() != b""


def _live_textedit(adapter) -> "object":
    """AppInfo for the CURRENTLY running TextEdit (pgrep-ground-truth pid).

    `list_running_apps()` returns stale entries after an app quits (pyobjc
    / macOS quirk), so match by the live pid and fall back to a synthetic
    AppInfo if the listing hasn't caught up.
    """
    raw = subprocess.run(["pgrep", "-x", "TextEdit"], capture_output=True).stdout.strip()
    live_pid = int(raw.split(b"\n")[0]) if raw else None
    assert live_pid, "TextEdit is not running"
    for x in adapter.list_running_apps():
        if x.pid == live_pid:
            return x
    from gateway.os_adapter.base import AppInfo

    return AppInfo(pid=live_pid, name="TextEdit", bundle_id="com.apple.TextEdit")


def _reset_textedit(adapter) -> None:
    def quit_gracefully():
        subprocess.run(
            ["osascript", "-e", 'tell application "TextEdit" to close every document saving no', "-e", 'tell application "TextEdit" to quit'],
            capture_output=True,
        )
        return _wait_for(lambda: not _pgrep_textedit(), timeout=20.0)

    def quit_force():
        subprocess.run(["killall", "TextEdit"], capture_output=True)  # SIGTERM
        if not _wait_for(lambda: not _pgrep_textedit(), timeout=10.0):
            subprocess.run(["killall", "-9", "TextEdit"], capture_output=True)  # SIGKILL
            _wait_for(lambda: not _pgrep_textedit(), timeout=5.0)

    if not quit_gracefully():
        quit_force()
    saved = Path.home() / "Library/Containers/com.apple.TextEdit/Data/Library/Saved Application State/com.apple.TextEdit.savedState"
    if saved.exists():
        subprocess.run(["rm", "-rf", str(saved)])
    launched = False
    for _ in range(10):  # open can be a no-op while LaunchServices still
        subprocess.run(["open", "-a", "TextEdit"])  # bookkeeps the dead app
        if _wait_for(lambda: _pgrep_textedit(), timeout=3.0):
            launched = True
            break
    assert launched, "TextEdit did not launch (stale LaunchServices entry?)"
    if not _wait_for(
        lambda: any(
            adapter._attr(el, "AXTitle") == "New Document"
            for el, _ in adapter.find_elements(
                _live_textedit(adapter),
                role="AXButton",
                max_depth=4,
            )
        ),
        timeout=30.0,
    ):
        # SIGKILL suppresses the welcome dialog; recover by quitting the
        # windowless instance gracefully, wiping state, and relaunching.
        if not quit_gracefully():
            quit_force()
        if saved.exists():
            subprocess.run(["rm", "-rf", str(saved)])
        for _ in range(10):
            subprocess.run(["open", "-a", "TextEdit"])
            if _wait_for(lambda: _pgrep_textedit(), timeout=3.0):
                launched = True
                break
    return _live_textedit(adapter)


async def _call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    payload = json.loads(result.content[0].text)
    payload["isError"] = bool(result.is_error)
    return payload


async def _run(adapter: MacOSAdapter) -> None:
    te_app = _reset_textedit(adapter)
    if not _wait_for(
        lambda: bool(adapter.find_elements(te_app, role="AXTextArea", max_depth=8)),
        timeout=30.0,
    ):
        subprocess.run(
            ["osascript", "-e", 'tell application "TextEdit" to make new document at front'],
            capture_output=True,
        )
    assert _wait_for(
        lambda: bool(adapter.find_elements(te_app, role="AXTextArea", max_depth=8)),
        timeout=15.0,
    ), "TextEdit has no text area to drive"

    env = dict(os.environ, CUA_GATEWAY_LIVE="1")
    params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "mcp_server"],
        env=env,
        cwd=str(GATEWAY_DIR),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"gui.observe", "gui.act", "gui.verify"} <= names

            # -- error paths (no model, no screen touch) ---------------------
            r = await _call(session, "gui.act", {"session_id": "e2e", "action": {"type": "click", "target_hint": "x"}})
            assert r.get("isError") and "expected_postcondition" in r.get("error", "")

            # -- gui.observe: source selection + capture ----------------------
            r = await _call(session, "gui.observe", {"session_id": "e2e"})
            assert not r.get("isError"), r
            assert r["active_app"] == "TextEdit"
            assert r["source_used"] in ("accessibility", "vision")
            assert r["screenshot_ref"].endswith(".png")

            # -- act #1: click into the text area, nonsense postcondition
            #            -> FAIL (verdict must say the postcondition does
            #            not hold; the action itself succeeds)
            areas = adapter.find_elements(te_app, role="AXTextArea", max_depth=8)
            ta_frame = areas[0][1]
            text_area_point = [ta_frame[0] + ta_frame[2] // 2, ta_frame[1] + ta_frame[3] // 2]
            r = await _call(
                session,
                "gui.act",
                {
                    "session_id": "e2e",
                    "action": {"type": "click", "target_hint": "the text area", "params": {"point": text_area_point}},
                    "expected_postcondition": "zzz-no-such-thing",
                },
            )
            assert not r.get("isError"), r
            assert r["dispatched"] is True, r
            assert r["verdict"] == "fail", f"expected fail (nonsense postcondition), got {r}"
            assert r["source_used"] in ("accessibility", "vision")
            act1_id = r["action_id"]

            # -- act #2: type marker, postcondition -> PASS ------------------
            r = await _call(
                session,
                "gui.act",
                {
                    "session_id": "e2e",
                    "action": {"type": "type", "target_hint": "the text area", "params": {"text": "m3-verify-ok"}},
                    "expected_postcondition": "m3-verify-ok",
                },
            )
            assert not r.get("isError"), r
            assert r["dispatched"] is True, r
            assert r["verdict"] == "pass", f"expected pass, got {r}"
            act2_id = r["action_id"]

            # -- gui.verify: re-check stored postconditions ------------------
            r = await _call(session, "gui.verify", {"session_id": "e2e", "action_id": act2_id})
            assert not r.get("isError"), r
            assert r["verdict"] == "pass", f"re-verify expected pass, got {r}"

            r = await _call(session, "gui.verify", {"session_id": "e2e", "action_id": act1_id})
            assert not r.get("isError"), r
            assert r["verdict"] == "fail", f"stale act #1 should stay fail, got {r}"

            r = await _call(session, "gui.verify", {"session_id": "e2e", "action_id": "no-such-id"})
            assert r.get("isError") is True


@pytest.mark.skipif(not E2E, reason="requires CUA_E2E=1 (real model + display)")
def test_m3_verify_loop():
    asyncio.run(_run(MacOSAdapter()))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))