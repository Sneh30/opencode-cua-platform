"""macOS OS Adapter — screen capture, AXUIElement reads, CGEvent dispatch.

The only module allowed to touch macOS-specific APIs (Engineering
Decision #11). Everything above this layer is platform-agnostic.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import Quartz
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXValueGetValue,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXPositionAttribute,
    kAXRoleAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueCGPointType,
    kAXValueCGSizeType,
    kAXErrorSuccess,
)
from Quartz import (
    CGDisplayBounds,
    CGDisplayCreateImage,
    CGMainDisplayID,
    CGPoint,
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
    CGEventCreateScrollWheelEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    CGEventSetIntegerValueField,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseDragged,
    kCGEventLeftMouseUp,
    kCGEventMouseMoved,
    kCGEventRightMouseDown,
    kCGEventRightMouseUp,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
    kCGMouseButtonRight,
    kCGMouseEventClickState,
    kCGScrollEventUnitLine,
)
from AppKit import (
    NSApplicationActivationPolicyRegular,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSWorkspace,
)

from .base import (
    AdapterCapabilities,
    AppInfo,
    OSAdapter,
    OSAdapterError,
    OSAdapterPermissionError,
)
from .keycodes import parse_key_combo
from shared.action_schema import (
    AXElement,
    AccessibilityState,
    Action,
    ActionRecord,
    ActionType,
    Screenshot,
)


class MacOSAdapter(OSAdapter):
    """Concrete macOS implementation (pyobjc)."""

    def capabilities(self) -> AdapterCapabilities:
        ax_trusted = bool(AXIsProcessTrusted())
        screen_ok = False
        size: tuple[int, int] | None = None
        try:
            cg = CGDisplayCreateImage(CGMainDisplayID())
            if cg is not None:
                screen_ok = True
                bounds = CGDisplayBounds(CGMainDisplayID())
                size = (int(bounds.size.width), int(bounds.size.height))
        except Exception:
            pass
        return AdapterCapabilities(
            accessibility_trusted=ax_trusted,
            screen_recording_available=screen_ok,
            active_display_size=size,
        )

    # -- observation ---------------------------------------------------------

    def capture_screen(self, output_path: str | Path) -> Screenshot:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cg = CGDisplayCreateImage(CGMainDisplayID())
        if cg is None:
            raise OSAdapterPermissionError(
                "screen capture failed — Screen Recording permission missing?"
            )
        rep = NSBitmapImageRep.alloc().initWithCGImage_(cg)
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
        if data is None or not data.writeToFile_atomically_(str(output_path), True):
            raise OSAdapterError(f"PNG write failed at {output_path}")
        bounds = CGDisplayBounds(CGMainDisplayID())
        return Screenshot(
            ref=str(output_path),
            width=int(bounds.size.width),
            height=int(bounds.size.height),
            captured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )

    def active_app(self) -> AppInfo | None:
        ws = NSWorkspace.sharedWorkspace()
        app = ws.frontmostApplication()
        if app is None:
            return None
        return AppInfo(
            pid=int(app.processIdentifier()),
            name=str(app.localizedName() or ""),
            bundle_id=str(app.bundleIdentifier() or ""),
            is_active=True,
        )

    def list_running_apps(self) -> list[AppInfo]:
        ws = NSWorkspace.sharedWorkspace()
        apps = []
        for app in ws.runningApplications():
            if app.activationPolicy() == NSApplicationActivationPolicyRegular:
                apps.append(
                    AppInfo(
                        pid=int(app.processIdentifier()),
                        name=str(app.localizedName() or ""),
                        bundle_id=str(app.bundleIdentifier() or ""),
                    )
                )
        return apps

    def accessibility_state(self, app: AppInfo) -> AccessibilityState:
        if not AXIsProcessTrusted():
            raise OSAdapterPermissionError(
                "Accessibility permission missing — grant it in System Settings → Privacy & Security"
            )
        root = AXUIElementCreateApplication(app.pid)
        try:
            ax_root = self._ax_element(root, app.pid, depth=0)
        except OSAdapterError:
            ax_root = None
        trustworthy = self._probe_trustworthiness(root)
        return AccessibilityState(
            app_name=app.name,
            app_pid=app.pid,
            root=ax_root,
            trustworthy=trustworthy,
        )

    def _probe_trustworthiness(self, ax_element) -> bool:
        err, children = AXUIElementCopyAttributeValue(ax_element, kAXChildrenAttribute, None)
        if err != kAXErrorSuccess:
            return False
        if children is None or len(children) == 0:
            return False
        return True

    def _ax_element(self, ax_element, pid: int, depth: int) -> AXElement | None:
        if depth > 60:
            return None
        role = self._attr(ax_element, kAXRoleAttribute)
        if role is None:
            return None
        title = self._attr(ax_element, kAXTitleAttribute) or ""
        desc = self._attr(ax_element, kAXDescriptionAttribute) or ""
        frame = self._frame(ax_element)
        node = AXElement(role=str(role), title=str(title), description=str(desc), frame=frame)
        err, children = AXUIElementCopyAttributeValue(ax_element, kAXChildrenAttribute, None)
        if err == kAXErrorSuccess and children is not None:
            for child in children:
                child_node = self._ax_element(child, pid, depth + 1)
                if child_node is not None:
                    node.children.append(child_node)
        return node

    def _attr(self, element, attribute: str):
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
        if err != kAXErrorSuccess:
            return None
        return value

    def _frame(self, element) -> tuple[int, int, int, int] | None:
        err, pos_value = AXUIElementCopyAttributeValue(element, kAXPositionAttribute, None)
        if err != kAXErrorSuccess or pos_value is None:
            return None
        err2, size_value = AXUIElementCopyAttributeValue(element, kAXSizeAttribute, None)
        if err2 != kAXErrorSuccess or size_value is None:
            return None
        e1, point = AXValueGetValue(pos_value, kAXValueCGPointType, None)
        e2, size = AXValueGetValue(size_value, kAXValueCGSizeType, None)
        if not e1 or not e2:
            return None
        return (int(point.x), int(point.y), int(size.width), int(size.height))

    def find_elements(
        self,
        app: AppInfo,
        *,
        role: str | None = None,
        title: str | None = None,
        description: str | None = None,
        max_depth: int = 40,
    ) -> list[tuple[object, object]]:
        root = AXUIElementCreateApplication(app.pid)
        matches: list[tuple[object, object]] = []
        self._find(root, role, title, description, 0, max_depth, matches)
        return matches

    def _find(self, element, role, title, description, depth, max_depth, matches) -> None:
        if depth > max_depth:
            return
        err, children = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute, None)
        if err != kAXErrorSuccess or children is None:
            return
        for child in children:
            if role is not None:
                r = self._attr(child, kAXRoleAttribute)
                if r != role:
                    self._find(child, role, title, description, depth + 1, max_depth, matches)
                    continue
            if title is not None:
                t = self._attr(child, kAXTitleAttribute)
                if t != title:
                    self._find(child, role, title, description, depth + 1, max_depth, matches)
                    continue
            if description is not None:
                d = self._attr(child, kAXDescriptionAttribute)
                if d != description:
                    self._find(child, role, title, description, depth + 1, max_depth, matches)
                    continue
            matches.append((child, self._frame(child)))
            self._find(child, role, title, description, depth + 1, max_depth, matches)

    # -- dispatch --------------------------------------------------------------

    def dispatch(self, action: Action) -> ActionRecord:
        if not AXIsProcessTrusted():
            raise OSAdapterPermissionError(
                "Accessibility permission missing — required for input dispatch"
            )
        t = action.type
        if t == ActionType.CLICK:
            self._click(self._point(action, "point"))
        elif t == ActionType.DOUBLE_CLICK:
            self._double_click(self._point(action, "point"))
        elif t == ActionType.RIGHT_CLICK:
            self._right_click(self._point(action, "point"))
        elif t == ActionType.DRAG:
            self._drag(self._point(action, "from"), self._point(action, "to"))
        elif t == ActionType.SCROLL:
            self._scroll(action.params.get("delta", (0, 3)))
        elif t == ActionType.TYPE:
            self.type_text(action.params.get("text", ""))
        elif t == ActionType.KEY:
            self._key_combo(action.params.get("key", ""))
        else:
            raise OSAdapterError(f"unsupported action type: {t}")
        return ActionRecord(
            action_id=f"ax-{uuid.uuid4().hex[:12]}",
            dispatched=True,
            raw_result={"type": t.value, "params": dict(action.params)},
        )

    def _point(self, action: Action, key: str) -> tuple[int, int]:
        p = action.params.get(key)
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise OSAdapterError(f"action.params[{key!r}] must be [x, y]")
        return (int(p[0]), int(p[1]))

    def _post(self, event) -> None:
        CGEventPost(kCGHIDEventTap, event)

    def _mouse_event(self, etype, point, button, click_state=1):
        ev = CGEventCreateMouseEvent(None, etype, CGPoint(point[0], point[1]), button)
        CGEventSetIntegerValueField(ev, kCGMouseEventClickState, click_state)
        return ev

    def _click(self, point) -> None:
        self.move_mouse(point)
        self._post(self._mouse_event(kCGEventLeftMouseDown, point, kCGMouseButtonLeft))
        self._post(self._mouse_event(kCGEventLeftMouseUp, point, kCGMouseButtonLeft))

    def _double_click(self, point) -> None:
        self.move_mouse(point)
        self._post(self._mouse_event(kCGEventLeftMouseDown, point, kCGMouseButtonLeft))
        self._post(self._mouse_event(kCGEventLeftMouseUp, point, kCGMouseButtonLeft))
        self._post(self._mouse_event(kCGEventLeftMouseDown, point, kCGMouseButtonLeft, click_state=2))
        self._post(self._mouse_event(kCGEventLeftMouseUp, point, kCGMouseButtonLeft, click_state=2))

    def _right_click(self, point) -> None:
        self.move_mouse(point)
        self._post(self._mouse_event(kCGEventRightMouseDown, point, kCGMouseButtonRight))
        self._post(self._mouse_event(kCGEventRightMouseUp, point, kCGMouseButtonRight))

    def _drag(self, start, end) -> None:
        self.move_mouse(start)
        self._post(self._mouse_event(kCGEventLeftMouseDown, start, kCGMouseButtonLeft))
        steps = 12
        for i in range(1, steps + 1):
            x = start[0] + (end[0] - start[0]) * i / steps
            y = start[1] + (end[1] - start[1]) * i / steps
            self._post(self._mouse_event(kCGEventLeftMouseDragged, (int(x), int(y)), kCGMouseButtonLeft))
        self._post(self._mouse_event(kCGEventLeftMouseUp, end, kCGMouseButtonLeft))

    def _scroll(self, delta) -> None:
        dx, dy = int(delta[0]), int(delta[1])
        ev = CGEventCreateScrollWheelEvent(
            None, kCGScrollEventUnitLine, 2, -dy, -dx
        )
        self._post(ev)

    def move_mouse(self, point) -> None:
        ev = CGEventCreateMouseEvent(
            None, kCGEventMouseMoved, CGPoint(point[0], point[1]), kCGMouseButtonLeft
        )
        self._post(ev)

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch == "\n":
                self._key_combo("return")
                continue
            if ch == "\t":
                self._key_combo("tab")
                continue
            ev = CGEventCreateKeyboardEvent(None, 0, True)
            CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
            self._post(ev)
            ev_up = CGEventCreateKeyboardEvent(None, 0, False)
            CGEventKeyboardSetUnicodeString(ev_up, len(ch.encode("utf-16-le")) // 2, ch)
            self._post(ev_up)

    def _key_combo(self, combo: str) -> None:
        keycode, flags = parse_key_combo(combo)
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        CGEventSetFlags(down, flags)
        self._post(down)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        CGEventSetFlags(up, flags)
        self._post(up)