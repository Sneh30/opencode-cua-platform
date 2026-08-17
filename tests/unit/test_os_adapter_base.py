import pathlib
import uuid

import pytest

from gateway.os_adapter.base import (
    AdapterCapabilities,
    AppInfo,
    OSAdapter,
    OSAdapterError,
    OSAdapterPermissionError,
)
from shared.action_schema import Action, ActionRecord, ActionType

GATEWAY_DIR = pathlib.Path(__file__).resolve().parents[2] / "gateway"
MACOS_IMPORTS = [
    "Quartz",
    "AppKit",
    "ApplicationServices",
    "HIServices",
    "CoreGraphics",
    "pyobjc",
    "objc",
]


def test_base_has_no_macos_imports():
    """ED #11: platform coupling lives only in os_adapter implementations."""
    src = (GATEWAY_DIR / "os_adapter" / "base.py").read_text()
    for mod in MACOS_IMPORTS:
        assert f"import {mod}" not in src and f"from {mod}" not in src


def test_macos_imports_confined_to_os_adapter_directory():
    """ED #11 tripwire: no pyobjc-family imports outside gateway/os_adapter/."""
    offenders = []
    for py in GATEWAY_DIR.rglob("*.py"):
        if "os_adapter" in py.parts:
            continue
        text = py.read_text()
        for mod in MACOS_IMPORTS:
            if f"import {mod}" in text or f"from {mod}" in text:
                offenders.append(f"{py.relative_to(GATEWAY_DIR)} imports {mod}")
    assert not offenders, offenders


class FakeAdapter(OSAdapter):
    """Minimal in-memory implementation used to pin the abstract contract."""

    def __init__(self):
        self.dispatched = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(accessibility_trusted=True, screen_recording_available=True)

    def capture_screen(self, output_path):
        pathlib.Path(output_path).write_bytes(b"PNG")
        return None

    def active_app(self):
        return AppInfo(pid=1, name="Fake")

    def list_running_apps(self):
        return [AppInfo(pid=1, name="Fake")]

    def accessibility_state(self, app):
        return None

    def find_elements(self, app, *, role=None, title=None, description=None, max_depth=40):
        return []

    def dispatch(self, action: Action) -> ActionRecord:
        self.dispatched.append(action)
        return ActionRecord(action_id=f"a-{uuid.uuid4().hex[:8]}", dispatched=True)

    def move_mouse(self, point):
        return None


def test_adapter_contract_is_implementable():
    adapter = FakeAdapter()
    assert adapter.capabilities().can_observe_ax is True
    assert adapter.capabilities().can_capture_screen is True


def test_adapter_dispatch_accepts_normalized_actions():
    adapter = FakeAdapter()
    action = Action(
        type=ActionType.CLICK,
        target_hint="OK button",
        params={"point": [100, 200]},
    )
    record = adapter.dispatch(action)
    assert record.dispatched is True
    assert adapter.dispatched == [action]


def test_error_hierarchy():
    assert issubclass(OSAdapterPermissionError, OSAdapterError)
    with pytest.raises(OSAdapterError):
        raise OSAdapterPermissionError("no permission")