import pytest

from gateway.executor.model_client import (
    ModelClientError,
    UiTarsActionNormalizer,
    UnsupportedModelAction,
    parse_box,
    parse_model_output,
    smart_resize,
)
from shared.action_schema import ActionType


def test_smart_resize_matches_mlx_processor():
    # verified at runtime: 3600x2338 screenshot -> (2352, 3612)
    assert smart_resize(2338, 3600) == (2352, 3612)
    # non-retina 1x display
    assert smart_resize(1169, 1800) == (1176, 1792)


class TestParseModelOutput:
    def test_thought_prefix_click(self):
        out = parse_model_output(
            "Thought: The 7 button is in the keypad.\n"
            "Action: click(start_box='<|box_start|>(500,1200)<|box_end|>')"
        )
        assert out["name"] == "click"
        assert out["args"]["start_box"] == "<|box_start|>(500,1200)<|box_end|>"

    def test_bare_call(self):
        out = parse_model_output("click(start_box='<|box_start|>(1,2)<|box_end|>')")
        assert out["name"] == "click"

    def test_legacy_format(self):
        out = parse_model_output("Action: click(start_box='(197,525)')")
        assert out["name"] == "click"
        assert out["args"]["start_box"] == "(197,525)"

    def test_hotkey(self):
        out = parse_model_output("Action: hotkey(key='cmd c')")
        assert out["name"] == "hotkey"
        assert out["args"]["key"] == "cmd c"

    def test_type_with_escaped_content(self):
        out = parse_model_output(r"Action: type(content='hello\\n')")
        assert out["name"] == "type"

    def test_malformed_raises(self):
        with pytest.raises(ModelClientError):
            parse_model_output("I don't know what to do.")


class TestParseBox:
    def test_tagged(self):
        assert parse_box("<|box_start|>(100,200)<|box_end|>") == (100, 200)

    def test_plain(self):
        assert parse_box("(197,525)") == (197, 525)

    def test_none(self):
        assert parse_box("nope") is None


class TestNormalization:
    # image 3600x2338 on an 1800x1169 logical display (Retina 2x)
    def make(self):
        return UiTarsActionNormalizer(3600, 2338, 1800, 1169)

    def test_click_maps_to_screen_points(self):
        n = self.make()
        # model coords are in resized space (2352x3612)
        act = n.normalize("click", {"start_box": "<|box_start|>(1806,1176)<|box_end|>"}, "click X")
        assert act.type is ActionType.CLICK
        # 1806/3612*3600 -> image px 1800 -> /3600*1800 = 900
        # 1176/2352*2338 -> image px 1169 -> /2338*1169 = 584
        assert act.params["point"] == [900, 584]
        assert act.target_hint == "click X"

    def test_left_double_and_right_single(self):
        n = self.make()
        d = n.normalize("left_double", {"start_box": "<|box_start|>(1,2)<|box_end|>"}, "hint")
        assert d.type is ActionType.DOUBLE_CLICK
        r = n.normalize("right_single", {"start_box": "<|box_start|>(1,2)<|box_end|>"}, "hint")
        assert r.type is ActionType.RIGHT_CLICK

    def test_drag(self):
        n = self.make()
        d = n.normalize(
            "drag",
            {
                "start_box": "<|box_start|>(0,0)<|box_end|>",
                "end_box": "<|box_start|>(3612,2352)<|box_end|>",
            },
            "hint",
        )
        assert d.type is ActionType.DRAG
        assert d.params["from"] == [0, 0]
        assert d.params["to"] == [1800, 1169]

    def test_type(self):
        n = self.make()
        t = n.normalize("type", {"content": "hello"}, "hint")
        assert t.type is ActionType.TYPE
        assert t.params["text"] == "hello"

    def test_hotkey_normalized(self):
        n = self.make()
        h = n.normalize("hotkey", {"key": "cmd c"}, "hint")
        assert h.type is ActionType.KEY
        assert h.params["key"] == "cmd+c"
        a = n.normalize("hotkey", {"key": "arrowleft"}, "hint")
        assert a.params["key"] == "left"

    def test_scroll_with_point_and_direction(self):
        n = self.make()
        s = n.normalize(
            "scroll",
            {"start_box": "<|box_start|>(1,2)<|box_end|>", "direction": "down"},
            "hint",
        )
        assert s.type is ActionType.SCROLL
        assert s.params["delta"] == (0, 3)
        assert isinstance(s.params["point"], list)

    def test_wait_and_finished_unsupported(self):
        n = self.make()
        with pytest.raises(UnsupportedModelAction):
            n.normalize("wait", {}, "hint")
        with pytest.raises(UnsupportedModelAction):
            n.normalize("finished", {"content": "done"}, "hint")

    def test_unknown_action_raises(self):
        n = self.make()
        with pytest.raises(ModelClientError):
            n.normalize("selfie", {}, "hint")

    def test_click_without_box_raises(self):
        n = self.make()
        with pytest.raises(ModelClientError):
            n.normalize("click", {}, "hint")
