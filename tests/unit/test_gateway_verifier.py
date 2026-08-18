"""GatewayVerifier + LiveGateway tests against a FakeAdapter.

No pyobjc, no display, no model: the fake adapter provides AX states and
OCR text deterministically, so source selection, verdicts, and the live
session store are exercised without any real OS interaction.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from gateway.executor.verifier import GatewayVerifier
from mcp_server import LiveGateway, _resolve_action
from shared.action_schema import (
    AXElement,
    AccessibilityState,
    Action,
    ActionRecord,
    ActionType,
    Observation,
    ObservationSource,
    Verdict,
)


class _App:
    def __init__(self, name):
        self.name = name
        self.pid = 1


class FakeAdapter:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.ax_state: AccessibilityState | None = None
        self.ocr_text = ""
        self.dispatched: list[Action] = []
        self.frontmost = _App("TextEdit")

    def capture_screen(self, output_path):
        from shared.action_schema import Screenshot

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")
        return Screenshot(ref=str(p), width=100, height=100, captured_at="now")

    def active_app(self):
        return self.frontmost

    def list_running_apps(self):
        return [self.frontmost]

    def accessibility_state(self, app):
        if self.ax_state is None:
            raise RuntimeError("no ax state")
        return self.ax_state

    def ocr_screenshot(self, screenshot):
        return self.ocr_text

    def dispatch(self, action: Action) -> ActionRecord:
        self.dispatched.append(action)
        return ActionRecord(action_id="fake-1", dispatched=True, raw_result={})


def _textedit_state(text_value: str, trustworthy: bool = True) -> AccessibilityState:
    return AccessibilityState(
        app_name="TextEdit",
        app_pid=1,
        root=AXElement(
            role="AXApplication",
            title="TextEdit",
            children=[
                AXElement(role="AXTextArea", value=text_value),
                AXElement(role="AXButton", title="New Document"),
            ],
        ),
        trustworthy=trustworthy,
    )


class TestGatewayVerifier:
    def test_uses_ax_when_trustworthy(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("m3-verify-ok")
        v = GatewayVerifier(adapter)
        obs = Observation(screenshot_ref="nope.png", active_app="TextEdit")
        r = v.verify(obs, "m3-verify-ok")
        assert r.verdict is Verdict.PASS
        assert r.source_used == "accessibility"

    def test_ax_miss_returns_fail(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("")
        r = GatewayVerifier(adapter).verify(Observation(screenshot_ref="x", active_app="TextEdit"), "m3-verify-ok")
        assert r.verdict is Verdict.FAIL
        assert r.source_used == "accessibility"

    def test_vision_fallback_when_untrustworthy(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("wrong", trustworthy=False)
        adapter.ocr_text = "TextEdit m3-verify-ok"
        shot = adapter.capture_screen(tmp_path / "shot.png")
        obs = Observation(screenshot_ref=shot.ref, active_app="TextEdit")
        r = GatewayVerifier(adapter).verify(obs, "m3-verify-ok")
        assert r.verdict is Verdict.PASS
        assert r.source_used == "vision"

    def test_vision_uncertain_when_no_text(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ocr_text = ""
        shot = adapter.capture_screen(tmp_path / "shot.png")
        r = GatewayVerifier(adapter).verify(Observation(screenshot_ref=shot.ref), "anything")
        assert r.verdict is Verdict.UNCERTAIN
        assert r.source_used == "vision"

    def test_no_ocr_method_no_crash(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ocr_screenshot = None  # instance attribute shadows the method
        adapter.ax_state = _textedit_state("m3-verify-ok", trustworthy=False)
        r = GatewayVerifier(adapter).verify(Observation(screenshot_ref="nope.png"), "m3-verify-ok")
        assert r.verdict is Verdict.UNCERTAIN


class TestLiveGateway:
    def test_act_with_point_dispatches_and_verifies(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("m3-verify-ok")
        gw = LiveGateway(adapter, tmp_path / "logs")
        action = {"type": "type", "target_hint": "the text area", "params": {"text": "m3-verify-ok"}}
        out = gw.act("s1", action, "m3-verify-ok")
        assert out["dispatched"] is True
        assert out["verdict"] == "pass"
        assert out["action_id"]

    def test_act_stale_postcondition_fails(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("no dialog here")
        gw = LiveGateway(adapter, tmp_path / "logs")
        action = {"type": "click", "target_hint": "New Document", "params": {"point": [1, 2]}}
        out = gw.act("s1", action, "Open Document")
        assert out["dispatched"] is True
        assert out["verdict"] == "fail"

    def test_verify_rechecks_stored_action(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("m3-verify-ok")
        gw = LiveGateway(adapter, tmp_path / "logs")
        action = {"type": "type", "target_hint": "text area", "params": {"text": "m3-verify-ok"}}
        out = gw.act("s1", action, "m3-verify-ok")
        adapter.ax_state = _textedit_state("")  # state changed since
        r = gw.verify("s1", out["action_id"])
        assert r["verdict"] == "fail"

    def test_verify_unknown_action(self, tmp_path):
        gw = LiveGateway(FakeAdapter(tmp_path), tmp_path / "logs")
        assert gw.verify("s1", "nope") is None

    def test_click_grounds_when_no_point(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        gw = LiveGateway(adapter, tmp_path / "logs")

        class StubClient:
            def infer(self, screenshot, instruction):
                return Action(type=ActionType.CLICK, target_hint=instruction, params={"point": [10, 20]})

        import gateway.executor.model_client as mc

        mc.UiTarsMLXClient = lambda: StubClient()
        gw.act("s1", {"type": "click", "target_hint": "the button"}, "anything")
        assert adapter.dispatched[-1].params["point"] == [10, 20]

    def test_type_action_skips_grounding(self, tmp_path):
        adapter = FakeAdapter(tmp_path)
        adapter.ax_state = _textedit_state("hi")
        gw = LiveGateway(adapter, tmp_path / "logs")
        out = gw.act("s1", {"type": "type", "target_hint": "area", "params": {"text": "hi"}}, "hi")
        assert adapter.dispatched[-1].params.get("text") == "hi"
        assert out["verdict"] == "pass"


class TestResolveAction:
    def test_builds_action(self):
        a = _resolve_action({"type": "click", "target_hint": "New Document", "params": {"point": [1, 2]}})
        assert a.type is ActionType.CLICK
        assert a.target_hint == "New Document"
        assert a.params == {"point": [1, 2]}