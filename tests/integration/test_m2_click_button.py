"""M2 e2e: model-grounded 'click a specific button' (TextEdit New Document).

Runs the full loop: screenshot -> UI-TARS grounding (mlx-vlm) -> normalized
Action -> OS Adapter dispatch -> AX verification of the postcondition.

Skipped by default (needs the 4.87GB model + a real display); run with:
    CUA_E2E=1 uv run pytest -q tests/integration/test_m2_click_button.py
or directly:
    uv run python tests/integration/test_m2_click_button.py
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


def _wait_for(predicate, timeout=15.0, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.mark.skipif(not os.environ.get("CUA_E2E"), reason="requires CUA_E2E=1 (real model + display)")
def test_click_new_document_button():
    adapter = MacOSAdapter()
    subprocess.run(["open", "-a", "TextEdit"])
    if not _wait_for(lambda: any(a.name == "TextEdit" for a in adapter.list_running_apps())):
        pytest.skip("TextEdit did not launch")

    app = next(a for a in adapter.list_running_apps() if a.name == "TextEdit")
    if not _wait_for(
        lambda: bool(adapter.find_elements(app, role="AXButton", max_depth=4))
    ):
        pytest.skip("TextEdit dialog (with buttons) not visible — window may be on another Space")

    buttons = adapter.find_elements(app, role="AXButton", max_depth=4)
    labels = [adapter._attr(el, "AXTitle") for el, _ in buttons]
    if "New Document" not in labels:
        pytest.skip("'New Document' button not in visible dialog")
    idx = labels.index("New Document")
    frame = buttons[idx][1]
    ground_truth = (frame[0] + frame[2] // 2, frame[1] + frame[3] // 2)

    shot = adapter.capture_screen("/tmp/m2_e2e_shot.png")
    client = UiTarsMLXClient()
    action = client.infer(
        shot,
        "click the button labeled 'New Document' in the TextEdit dialog",
    )
    print("model action:", action)

    assert action.type is ActionType.CLICK
    px, py = action.params["point"]
    # model must have grounded inside the actual button (with margin for the
    # keypad being inside the dialog, allow generous tolerance)
    inside = (
        frame[0] - 20 <= px <= frame[0] + frame[2] + 20
        and frame[1] - 20 <= py <= frame[1] + frame[3] + 20
    )
    print(f"model point {px,py} vs ground truth button {frame} center {ground_truth}")
    assert inside, f"model clicked ({px},{py}), expected inside {frame}"

    record = adapter.dispatch(action)
    assert record.dispatched is True

    # postcondition: the Open dialog closes and a document window appears
    closed = _wait_for(
        lambda: not adapter.find_elements(app, role="AXButton", max_depth=4)
        or "New Document"
        not in [adapter._attr(el, "AXTitle") for el, _ in adapter.find_elements(app, role="AXButton", max_depth=4)]
    )
    assert closed, "dialog did not close after click"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))