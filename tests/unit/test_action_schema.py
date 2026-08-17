import pytest

from shared.action_schema import (
    Action,
    ActionRecord,
    ActionType,
    BrowserActionType,
    Observation,
    ObservationSource,
    Screenshot,
    Verdict,
    VerificationResult,
)


def test_action_roundtrip():
    action = Action(type=ActionType.CLICK, target_hint="OK button", params={"point": [10, 20]})
    restored = Action.from_dict(action.to_dict())
    assert restored == action


def test_action_from_dict_requires_valid_type():
    with pytest.raises(ValueError):
        Action.from_dict({"type": "hover"})


def test_action_covers_architecture_section8_types():
    assert {t.value for t in ActionType} == {
        "click",
        "double_click",
        "right_click",
        "drag",
        "type",
        "key",
        "scroll",
    }


def test_browser_action_types_match_architecture_section8():
    assert {t.value for t in BrowserActionType} == {
        "navigate",
        "click",
        "type",
        "extract",
        "scroll",
        "select",
        "key",
    }


def test_observation_shapes_match_gui_observe_contract():
    obs = Observation(
        screenshot_ref="logs/screens/s1.png",
        accessibility_tree={"root": "..."},
        active_app="Calculator",
        source_used=ObservationSource.ACCESSIBILITY,
    )
    d = obs.to_dict()
    assert d["screenshot_ref"] == "logs/screens/s1.png"
    assert d["accessibility_tree"] == {"root": "..."}
    assert d["active_app"] == "Calculator"
    assert d["source_used"] == "accessibility"


def test_action_record_shapes_match_gui_act_contract():
    rec = ActionRecord(action_id="a1", dispatched=True, raw_result={"ok": True})
    assert rec.to_dict() == {"action_id": "a1", "dispatched": True, "raw_result": {"ok": True}}


def test_verification_result_shapes_match_gui_verify_contract():
    res = VerificationResult(verdict=Verdict.UNCERTAIN, evidence={"stub": True}, source_used="none")
    d = res.to_dict()
    assert d["verdict"] == "uncertain"
    assert d["evidence"] == {"stub": True}
    assert d["source_used"] == "none"


def test_screenshot_is_serializable():
    s = Screenshot(ref="r", width=1920, height=1080, captured_at="t")
    assert s.to_dict()["width"] == 1920