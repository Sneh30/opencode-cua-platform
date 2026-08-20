from shared.action_schema import (
    AXElement,
    AccessibilityState,
    ObservationSource,
    Verdict,
)
from shared.verifier import (
    ax_text,
    best_available_source,
    extract_fragments,
    has_verifiable_anchors,
    matched_fragments,
    normalize,
    verify,
    verify_text,
)


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

    # -- regression: natural-language postconditions from real sessions ------

    def test_prose_with_domain_passes_when_url_present(self):
        actual = "URL: https://example.com/ | Title: example.com | a Learn more"
        assert verify_text(actual, "the browser is on example.com") is Verdict.PASS

    def test_prose_with_url_passes_when_url_present(self):
        actual = "URL: https://example.com/ | Title: example.com | a Learn more"
        assert (
            verify_text(actual, "The page URL is https://example.com and the page has loaded")
            is Verdict.PASS
        )

    def test_prose_with_quote_and_domain_passes(self):
        actual = "URL: https://example.com/ | Title: Example Domain | a Learn more"
        assert (
            verify_text(
                actual,
                "The browser is at example.com and the page contains the heading 'Example Domain'",
            )
            is Verdict.PASS
        )

    def test_prose_with_quoted_word_passes_when_word_present(self):
        actual = "TextEdit Untitled 2 iPhone bold italic underline strikethrough"
        assert (
            verify_text(actual, 'The word "iPhone" is visible in the Untitled 2 document')
            is Verdict.PASS
        )

    def test_prose_with_proper_noun_passes(self):
        actual = "TextEdit Untitled 2 bold italic underline strikethrough"
        assert (
            verify_text(actual, "A new untitled TextEdit document window is open, replacing the Open dialog")
            is Verdict.PASS
        )

    def test_prose_with_proper_noun_and_digit_passes(self):
        actual = "TextEdit Untitled 2 bold italic underline strikethrough"
        assert (
            verify_text(actual, "The text insertion caret is placed inside the Untitled 2 text area")
            is Verdict.PASS
        )

    def test_prose_with_number_passes_when_digit_present(self):
        actual = "Calculator Calculator 2 Swap Rotate stack down Drop Delete Clear"
        assert verify_text(actual, "The calculator display shows the number 2") is Verdict.PASS

    def test_prose_still_fails_when_no_fragment_present(self):
        actual = "TextEdit Untitled 2 bold italic underline strikethrough"
        assert verify_text(actual, "The calculator display shows the number 7") is Verdict.FAIL

    def test_quoted_string_must_be_present(self):
        actual = "URL: https://example.com/ | Title: Example Domain | a Learn more"
        assert (
            verify_text(actual, "the page contains the text 'Welcome to the site'")
            is Verdict.FAIL
        )


class TestFragments:
    def test_extracts_anchors(self):
        frags = extract_fragments(
            "The browser is at example.com and the page contains the heading 'Example Domain'"
        )
        assert "example.com" in frags["domains"]
        assert "Example Domain" in frags["quotes"]

    def test_proper_noun_extraction_skips_sentence_start(self):
        frags = extract_fragments("A new untitled TextEdit document window is open")
        assert "textedit" in frags["proper"]
        assert "new" not in frags["proper"]

    def test_has_verifiable_anchors(self):
        assert has_verifiable_anchors("the browser is on example.com")
        assert has_verifiable_anchors('the page contains "Welcome"')
        assert not has_verifiable_anchors("the page loads")
        assert not has_verifiable_anchors("The page has loaded")

    def test_matched_fragments(self):
        actual = "URL: https://example.com/ | Title: example.com | a Learn more"
        assert matched_fragments(actual, "the browser is on example.com") == ["example.com"]

    def test_normalize(self):
        assert normalize("  Hello   World ") == "hello world"


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