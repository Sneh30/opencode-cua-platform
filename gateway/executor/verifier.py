"""Gateway-side verification glue: AX state / OCR -> shared.verifier.

M3 (Roadmap §A.6): `GatewayVerifier.verify(observation, expected)` runs the
Architecture §12 loop's verify step — pick the best source for the active
app (AX tree when the per-app capability probe says trustworthy, else
Vision OCR of the screenshot), then let `shared.verifier` render a verdict.
The Browser Adapter (M5) reuses `shared.verifier` directly with DOM text.
"""

from __future__ import annotations

import logging
from pathlib import Path

from shared.action_schema import (
    AccessibilityState,
    Observation,
    ObservationSource,
    VerificationResult,
)
from shared.verifier import ax_text, best_available_source, verify

logger = logging.getLogger("cua_gateway.verifier")


class GatewayVerifier:
    def __init__(self, adapter):
        self._adapter = adapter

    def _resolve_app(self, observation: Observation):
        """The app to verify against: the observation's active app, or the
        current frontmost app, or None."""
        try:
            if observation.active_app:
                for app in self._adapter.list_running_apps():
                    if app.name == observation.active_app:
                        return app
            return self._adapter.active_app()
        except Exception:
            return None

    def _ax_state(self, app) -> AccessibilityState | None:
        try:
            if app is None:
                return None
            state = self._adapter.accessibility_state(app)
            return state
        except Exception:
            return None

    def _ocr(self, observation: Observation) -> str:
        ocr = getattr(self._adapter, "ocr_screenshot", None)
        if ocr is None or not observation.screenshot_ref:
            return ""
        if not Path(observation.screenshot_ref).exists():
            return ""
        try:
            from shared.action_schema import Screenshot

            shot = Screenshot(
                ref=observation.screenshot_ref,
                width=0,
                height=0,
                captured_at="",
            )
            return ocr(shot)
        except Exception:
            logger.exception("OCR failed")
            return ""

    def verify(
        self,
        observation: Observation,
        expected: str,
        force_source: str | None = None,
    ) -> VerificationResult:
        """Verdict for `expected` against the current screen state.

        `force_source` overrides source selection ("vision" = the
        Architecture §13 re-observe strategy, used on later recovery
        attempts when the AX tree may be misleading).
        """
        app = self._resolve_app(observation)
        state = self._ax_state(app)
        source = best_available_source(state, prefer=force_source or "auto")
        if source is ObservationSource.ACCESSIBILITY:
            return verify(expected, ax_text(state), source)
        actual = self._ocr(observation)
        return verify(expected, actual, ObservationSource.VISION)