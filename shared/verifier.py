"""Verification engine — shared by the Gateway and (in M5) the Browser Adapter.

Architecture §12: `verify(observation, expected_postcondition)` is the
structural check that turns "an action was attempted" into "an action
succeeded". It is pure logic with zero OS dependencies so both MCP servers
can import it (Roadmap §A.6, M3 milestone row).

State-source selection (Architecture §12 `best_available_source()`):
accessibility tree first, but only when the per-app capability probe says
the app's AX tree is trustworthy (some macOS apps expose incomplete or
misleading trees). Vision (OCR of the screenshot) is the fallback.

Expected-postcondition matching is a case-insensitive substring match.
This is deliberately simple and predictable — planners write checkable
phrases ("a file named report.pdf exists"), and substring matching is the
most legible failure mode. Regex support is a possible later enhancement.
"""

from __future__ import annotations

from shared.action_schema import (
    AccessibilityState,
    Observation,
    ObservationSource,
    VerificationResult,
    Verdict,
)


def ax_text(state: AccessibilityState | None) -> str:
    """Flatten an AX tree into one searchable string.

    Concatenates titles and descriptions of every node, space-separated.
    Empty string when there is no usable tree.
    """
    if state is None or state.root is None:
        return ""
    parts: list[str] = []

    def walk(node) -> None:
        for t in (node.title, node.description, node.value):
            if t:
                parts.append(str(t))
        for child in node.children:
            walk(child)

    walk(state.root)
    return " ".join(parts)


def best_available_source(
    state: AccessibilityState | None,
    prefer: str = "auto",
) -> ObservationSource:
    """Pick the observation source per Architecture §12.

    `prefer` mirrors the `gui.observe` input: "auto" (default),
    "vision", or "accessibility". "auto" uses the AX tree only if the
    per-app capability probe (`AccessibilityState.trustworthy`) says it
    is usable; otherwise falls back to vision.
    """
    if prefer == "vision":
        return ObservationSource.VISION
    ax_usable = bool(state and state.trustworthy and state.root and ax_text(state))
    if prefer == "accessibility":
        return ObservationSource.ACCESSIBILITY if ax_usable else ObservationSource.VISION
    return ObservationSource.ACCESSIBILITY if ax_usable else ObservationSource.VISION


def verify_text(actual: str, expected: str) -> Verdict:
    """Verdict for one expected-postcondition string against actual text.

    - PASS: `expected` (case-insensitive) is a substring of `actual`.
    - UNCERTAIN: either side is empty/blank — no text evidence to judge.
    - FAIL: otherwise.
    """
    if not expected or not expected.strip():
        return Verdict.UNCERTAIN
    if not actual or not actual.strip():
        return Verdict.UNCERTAIN
    if expected.strip().lower() in actual.lower():
        return Verdict.PASS
    return Verdict.FAIL


def verify(
    expected_postcondition: str,
    actual_text: str,
    source: ObservationSource,
) -> VerificationResult:
    """Assemble a `VerificationResult` (Architecture §8 `gui.verify` output)."""
    verdict = verify_text(actual_text, expected_postcondition)
    return VerificationResult(
        verdict=verdict,
        evidence={
            "expected": expected_postcondition,
            "actual_text": actual_text[:400],
            "actual_text_length": len(actual_text),
            "match": verdict is Verdict.PASS,
        },
        source_used=source.value,
    )


def observation_text_source(
    observation: Observation,
    state: AccessibilityState | None,
) -> tuple[str, ObservationSource]:
    """Text evidence for an observation: AX text when the tree is
    trustworthy, else an empty string (caller supplies vision/OCR)."""
    source = best_available_source(state, prefer="auto")
    if source is ObservationSource.ACCESSIBILITY:
        return ax_text(state), source
    return "", ObservationSource.VISION