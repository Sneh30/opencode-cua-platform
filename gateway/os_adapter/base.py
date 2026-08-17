"""OS Adapter — the sole platform-coupling boundary (Engineering Decision #11).

Everything above this layer is platform-agnostic. macOS-specific APIs
(screen capture, AXUIElement, CGEvent) live only in `macos_adapter.py`
and nothing below the adapter boundary is allowed elsewhere in the Gateway.

Target-hint *resolution* (natural language -> coordinates) is the GUI
executor's job (M2), not this layer's. `dispatch()` receives a normalized
`Action` whose `params` already carry resolved, dispatchable values:

- click / double_click / right_click: {"point": [x, y]}
- drag: {"from": [x, y], "to": [x, y]}
- scroll: {"delta": [dx, dy]}
- type: {"text": "..."}
- key: {"key": "cmd+shift+4"} (modifier+key name string, see keycodes)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from shared.action_schema import Action, ActionRecord, AccessibilityState, Screenshot


class OSAdapterError(Exception):
    """Base class for OS Adapter failures."""


class OSAdapterPermissionError(OSAdapterError):
    """Required macOS permission (Accessibility / Screen Recording) missing."""


@dataclass(frozen=True)
class AppInfo:
    pid: int
    name: str
    bundle_id: str = ""
    is_active: bool = False


@dataclass(frozen=True)
class AdapterCapabilities:
    """What the adapter can actually do on this machine, per permission."""

    accessibility_trusted: bool
    screen_recording_available: bool
    active_display_size: tuple[int, int] | None = None

    @property
    def can_observe_ax(self) -> bool:
        return self.accessibility_trusted

    @property
    def can_capture_screen(self) -> bool:
        return self.screen_recording_available


class OSAdapter(ABC):
    """Abstract interface. Implementations must not be importable on
    platforms they don't support (import at call site)."""

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Probe what this adapter can do (permission state included)."""

    # -- observation ---------------------------------------------------------

    @abstractmethod
    def capture_screen(self, output_path: str | Path) -> Screenshot:
        """Capture the main display to `output_path` (PNG)."""

    @abstractmethod
    def active_app(self) -> AppInfo | None:
        """The currently focused application, if any."""

    @abstractmethod
    def list_running_apps(self) -> list[AppInfo]:
        """Running GUI applications."""

    @abstractmethod
    def accessibility_state(self, app: AppInfo) -> AccessibilityState:
        """Serialized AX tree for one app, with the per-app capability probe
        (Architecture §12: is this app's AX tree trustworthy?)."""

    @abstractmethod
    def find_elements(
        self,
        app: AppInfo,
        *,
        role: str | None = None,
        title: str | None = None,
        description: str | None = None,
        max_depth: int = 40,
    ) -> list[tuple[object, object]]:
        """Locate AX elements matching the given attributes.

        Returns (element_handle, frame) pairs; `element_handle` is
        adapter-defined and safe to pass back to `bounds_of` / dispatch
        helpers in later milestones.
        """

    # -- dispatch (resolved values only) --------------------------------------

    @abstractmethod
    def dispatch(self, action: Action) -> ActionRecord:
        """Execute one normalized, already-resolved action."""

    @abstractmethod
    def move_mouse(self, point: tuple[int, int]) -> None:
        """Move the cursor without clicking (useful for hover states)."""