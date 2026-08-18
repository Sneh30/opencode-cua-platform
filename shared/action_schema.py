"""Normalized data shapes for the Computer-Use Platform.

Everything in the Gateway and the Browser Adapter speaks in these types
(Roadmap §A.6 item 2). They mirror the JSON contracts from Architecture §8:
`gui.observe` / `gui.act` / `gui.verify` inputs and outputs, with the browser
namespace reusing the same shapes.

No dependencies on purpose — plain dataclasses, serializable via `.to_dict()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG = "drag"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"


class BrowserActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    EXTRACT = "extract"
    SCROLL = "scroll"
    SELECT = "select"
    KEY = "key"


class ObservationSource(str, Enum):
    VISION = "vision"
    ACCESSIBILITY = "accessibility"
    DOM = "dom"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class Action:
    """One bounded, single-step action (Architecture §8 `gui.act` input).

    At the executor level, `target_hint` is a natural-language target the
    executor resolves (AX tree first, vision fallback). At the OS-adapter
    level, `params` carries the resolved, dispatchable values
    (e.g. {"point": [x, y], "text": "..."}).
    """

    type: ActionType
    target_hint: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "target_hint": self.target_hint,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            type=ActionType(data["type"]),
            target_hint=data.get("target_hint", ""),
            params=data.get("params", {}),
        )


@dataclass(frozen=True)
class Screenshot:
    """A captured screen image, referenced by path.

    `width`/`height` are the image file's pixel dimensions (may exceed the
    logical display size on Retina displays — e.g. 3600x2338 image for an
    1800x1169 point display). `display_width`/`display_height` are the
    logical point dimensions of the display the capture came from, so
    downstream code can map image pixels <-> screen points.
    """

    ref: str
    width: int
    height: int
    captured_at: str
    display_width: int | None = None
    display_height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "screenshot_ref": self.ref,
            "width": self.width,
            "height": self.height,
            "captured_at": self.captured_at,
            "display_width": self.display_width,
            "display_height": self.display_height,
        }


@dataclass(frozen=True)
class AXElement:
    """A single node of the accessibility tree (serializable).

    `value` carries the element's `kAXValueAttribute` (e.g. a text field's
    contents) when present — the verifier (shared/verifier.py) searches
    titles, descriptions, and values.
    """

    role: str
    title: str = ""
    description: str = ""
    value: str = ""
    frame: tuple[int, int, int, int] | None = None
    children: list["AXElement"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "title": self.title,
            "description": self.description,
            "value": self.value,
            "frame": list(self.frame) if self.frame else None,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass(frozen=True)
class AccessibilityState:
    """AX tree for one app, plus the per-app capability probe result
    (Architecture §12: `best_available_source()` needs to know whether the
    app's AX tree is usable before preferring it over vision)."""

    app_name: str
    app_pid: int
    root: AXElement | None
    trustworthy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "app_pid": self.app_pid,
            "root": self.root.to_dict() if self.root else None,
            "trustworthy": self.trustworthy,
        }


@dataclass(frozen=True)
class Observation:
    """Output shape of `gui.observe` / `browser.observe`."""

    screenshot_ref: str
    accessibility_tree: dict[str, Any] | None = None
    active_app: str = ""
    source_used: ObservationSource = ObservationSource.VISION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "screenshot_ref": self.screenshot_ref,
            "accessibility_tree": self.accessibility_tree,
            "active_app": self.active_app,
            "source_used": self.source_used.value,
            **self.extra,
        }


@dataclass(frozen=True)
class ActionRecord:
    """Result of `gui.act` / `browser.act`."""

    action_id: str
    dispatched: bool
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "dispatched": self.dispatched,
            "raw_result": self.raw_result,
        }


@dataclass(frozen=True)
class VerificationResult:
    """Output shape of `gui.verify` / `browser.verify`."""

    verdict: Verdict
    evidence: dict[str, Any] = field(default_factory=dict)
    source_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "source_used": self.source_used,
        }