"""M2 e2e: two model-grounded clicks on the TextEdit menu bar (File -> New).

Runs the full loop twice: screenshot -> UI-TARS grounding (mlx-vlm) ->
normalized Action -> OS Adapter dispatch -> verification via OCR of the
*next* capture (vision is the single source of truth for the model).

Why the menu bar? macOS 26 Space quirk: the TextEdit startup dialog is
reported on-screen by CGWindowList/AX but never appears in the captured
framebuffer (window lives on another Space), so the dialog-button variant
fails regardless of the pipeline. Menu-bar items are always rendered in
the capture of the active Space.

Skipped by default (needs the 4.87GB model + a real display); run with:
    CUA_E2E=1 uv run pytest -q tests/integration/test_m2_menu_clicks.py -s
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from gateway.executor.model_client import UiTarsMLXClient
from gateway.os_adapter import MacOSAdapter
from shared.action_schema import ActionType


def _ocr(texts: list[str]) -> list[str]:
    import Vision
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_("/tmp/m2_e2e_shot.png"), None
    )
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    handler.performRequests_error_([req], None)
    texts.extend(obs.topCandidates_(1)[0].string() for obs in req.results() or [])


def _textedit_window_count() -> int:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
    )

    return sum(
        1
        for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        if w.get("kCGWindowOwnerName") == "TextEdit"
    )


@pytest.mark.skipif(not os.environ.get("CUA_E2E"), reason="requires CUA_E2E=1 (real model + display)")
def test_file_menu_new_document():
    adapter = MacOSAdapter()
    subprocess.run(["open", "-a", "TextEdit"])
    client = UiTarsMLXClient()
    windows_before = _textedit_window_count()

    visible = []
    _ocr(visible)
    if "File" not in visible:
        pytest.skip("TextEdit not frontmost / File menu not visible in capture")

    for step, task, expect_after in (
        (
            "open",
            "Click the 'File' menu in the menu bar at the top of the screen.",
            "New",
        ),
        (
            "new",
            "A menu is open below the File menu. Click the menu item labeled 'New' to create a new document.",
            None,
        ),
    ):
        shot = adapter.capture_screen("/tmp/m2_e2e_shot.png")
        action = client.infer(shot, task)
        print(f"[{step}] model action:", action)
        assert action.type is ActionType.CLICK

        record = adapter.dispatch(action)
        assert record.dispatched is True
        time.sleep(1.0)

        if expect_after:
            after = []
            _ocr(after)
            assert expect_after in after, f"after {step}: {expect_after!r} not in capture OCR: {after}"

    # postcondition: a new TextEdit document window exists on-screen
    time.sleep(1.0)
    after = _textedit_window_count()
    print(f"postcondition: TextEdit windows {windows_before} -> {after}")
    assert after > windows_before, "no new TextEdit window appeared after File > New"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))