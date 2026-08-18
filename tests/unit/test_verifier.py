from shared.action_schema import (
    AXElement,
    AccessibilityState,
    ObservationSource,
    Verdict,
)
from shared.verifier import ax_text, best_available_source, verify, verify_text


class TestVerifyText:
    def test_pass_substring(self):
        assert verify_text("a file named report.pdf exists", "report.pdf") is Verdict.PASS

    def test_pass_case_insensitive(self):
        assert verify_text("New Document button", "new document") is Verdict.PASS

    def test_fail(self):
        assert verify_text("TextEdit document", "calculator") is Verdict.FAIL

    def test_uncertain_empty_expected(self):
        assert verify_text("anything", "   ") is Verdict.UNCERTAIN

    def test_uncertain_empty_actual(self):
        assert verify_text("", "report.pdf") is Verdict.UNCERTAIN


class TestAxText:
    def test_flattens_titles_descriptions_values(self):
        state = AccessibilityState(
            app_name="TextEdit",
            app_pid=1,
            root=AXElement(
                role="AXApplication",
                title="TextEdit",
                children=[
                    AXElement(role="AXTextField", description="text area", value="m3-verify-ok"),
                    AXElement(role="AXButton", title="New Document"),
                ],
            ),
            trustworthy=True,
        )
        text = ax_text(state)
        assert "TextEdit" in text
        assert "m3-verify-ok" in text
        assert "New Document" in text
        assert "text area" in text

    def test_empty_without_root(self):
        assert ax_text(AccessibilityState("x", 1, None, False)) == ""
        assert ax_text(None) == ""


class TestBestAvailableSource:
    def state(self, trustworthy=True, with_root=True):
        return AccessibilityState(
            app_name="x",
            app_pid=1,
            root=AXElement(role="AXApplication", title="x") if with_root else None,
            trustworthy=trustworthy,
        )

    def test_auto_prefers_trustworthy_ax(self):
        assert best_available_source(self.state(True)) is ObservationSource.ACCESSIBILITY

    def test_auto_falls_back_to_vision_when_untrustworthy(self):
        assert best_available_source(self.state(False)) is ObservationSource.VISION

    def test_auto_falls_back_to_vision_when_no_root(self):
        assert best_available_source(self.state(True, with_root=False)) is ObservationSource.VISION

    def test_auto_falls_back_when_tree_has_no_text(self):
        state = AccessibilityState("x", 1, AXElement(role="AXApplication"), True)
        assert best_available_source(state) is ObservationSource.VISION

    def test_prefer_vision(self):
        assert best_available_source(self.state(True), prefer="vision") is ObservationSource.VISION

    def test_prefer_accessibility_untrustworthy_falls_back(self):
        assert (
            best_available_source(self.state(False), prefer="accessibility")
            is ObservationSource.VISION
        )


class TestVerify:
    def test_result_assembly_pass(self):
        r = verify("report.pdf", "a file named report.pdf exists", ObservationSource.ACCESSIBILITY)
        assert r.verdict is Verdict.PASS
        assert r.source_used == "accessibility"
        assert r.evidence["match"] is True
        assert r.evidence["expected"] == "report.pdf"

    def test_result_assembly_fail(self):
        r = verify("calculator", "TextEdit document", ObservationSource.VISION)
        assert r.verdict is Verdict.FAIL
        assert r.source_used == "vision"
        assert r.evidence["match"] is False

    def test_result_uncertain_when_no_evidence(self):
        r = verify("anything", "", ObservationSource.VISION)
        assert r.verdict is Verdict.UNCERTAIN