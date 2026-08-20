"""Verification engine — shared by the Gateway and the Browser Adapter.

Architecture §12: `verify(observation, expected_postcondition)` is the
structural check that turns "an action was attempted" into "an action
succeeded". It is pure logic with zero OS dependencies so both MCP servers
can import it (Roadmap §A.6, M3 milestone row).

State-source selection (Architecture §12 `best_available_source()`):
accessibility tree first, but only when the per-app capability probe says
the app's AX tree is trustworthy (some macOS apps expose incomplete or
misleading trees). Vision (OCR of the screenshot) is the fallback.

Expected-postcondition matching: planners write natural-language sentences
("the browser is on example.com") whose *content-bearing fragments* — quoted
strings, URLs, domain names, capitalized proper nouns, digits — must appear
in the evidence text. Exact substring matching of the full sentence is a
fragile false-negative machine (the URL is there, but the sentence "the
browser is on example.com" is not a substring of "URL: https://example.com/").
So we extract those fragments and judge on whether the *content* is present:

  PASS       exact normalized substring, or enough content fragments found
  UNCERTAIN  either side is blank — no evidence to judge
  FAIL       otherwise

`has_verifiable_anchors()` reports whether a postcondition is checkable at
all. Servers use it to decide when a clean dispatch is the best available
signal (a prose-only, unanchored postcondition cannot be textually judged).
"""

from __future__ import annotations

import re

from shared.action_schema import (
    AccessibilityState,
    Observation,
    ObservationSource,
    VerificationResult,
    Verdict,
)

# Words that carry no verification value inside a prose postcondition.
# They are excluded from fragment extraction so they never masquerade as
# content the evidence must contain.
_STOPWORDS = frozenset(
    (
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
        "by", "can", "could", "did", "do", "does", "for", "from", "has",
        "have", "had", "in", "is", "it", "its", "may", "might", "must",
        "not", "of", "on", "or", "should", "that", "the", "then", "these",
        "this", "those", "to", "was", "were", "when", "will", "with",
        "would", "page", "pages", "browser", "window", "windows", "screen",
        "document", "documents", "button", "buttons", "text", "area",
        "element", "elements", "content", "contains", "contain",
        "containing", "visible", "appears", "appear", "appearing", "loaded",
        "load", "loading", "open", "opens", "opening", "closed", "close",
        "word", "new", "clicked", "click", "select", "selected",
        "selectable", "type", "typed", "typing", "enter", "entered", "show",
        "shows", "showing", "displayed", "display", "shown", "user", "users",
        "using", "left", "right", "top", "bottom", "middle", "center",
        "status", "message", "messages", "item", "items", "field", "fields",
        "box", "input", "inputs", "value", "values", "label", "labels",
        "title", "titles", "heading", "headings", "subheading", "then",
        "now", "here", "there", "still", "successfully", "successful",
        "done", "finished", "complete", "completed", "correct", "properly",
        "correctly", "into", "onto", "upon", "during", "within", "before",
        "after", "between", "also", "only", "just", "same", "each", "every",
        "both", "either", "neither", "over", "under", "some", "any", "all",
        "very", "much", "more", "most", "less", "least", "about", "around",
        "across", "along", "against", "among", "behind", "below", "beneath",
        "beside", "beyond", "inside", "outside", "through", "throughout",
        "toward", "towards", "via", "using", "up", "down", "out", "off",
        "other", "another", "etc", "eg", "i.e.", "such", "like", "per",
    )
)

_URL_RE = re.compile(r"https?://[^\s'\"]+", re.IGNORECASE)
_QUOTE_RE = re.compile(r"\"([^\"]+)\"|'([^']+)'")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b", re.IGNORECASE
)


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace — the comparison canonical form."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_fragments(expected: str) -> dict[str, list[str]]:
    """Content-bearing fragments of an expected postcondition.

    Returns dict with keys: quotes, urls, domains, proper, numeric.
    `proper` = capitalized words that are not the sentence's first word
    (a sentence-initial "The"/"A" is prose, not content). `numeric` =
    short standalone digit tokens ("2", "h1", "25") — the concrete value
    a postcondition usually refers to.
    """
    quotes: list[str] = []
    for match in _QUOTE_RE.finditer(expected):
        fragment = (match.group(1) or match.group(2) or "").strip()
        if fragment:
            quotes.append(fragment)
    urls = [url.strip("\"'.,") for url in _URL_RE.findall(expected)]

    domains: list[str] = []
    for domain in _DOMAIN_RE.findall(expected):
        cleaned = domain.strip("\"'.,()")
        # Skip domains already captured inside an http(s) URL.
        if cleaned and not any(cleaned in url for url in urls):
            domains.append(cleaned)

    proper: list[str] = []
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", expected)
    for index, token in enumerate(tokens):
        if index == 0:
            continue  # sentence-initial word is prose
        if token[0].isupper() and len(token) >= 2 and token.lower() not in _STOPWORDS:
            proper.append(token.lower())

    numeric: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", expected):
        low = token.lower()
        if low in _STOPWORDS:
            continue
        if len(token) <= 6 and re.search(r"[0-9]", token) and (
            len(token) >= 2 or token.isdigit()
        ):
            numeric.append(low)

    return {
        "quotes": quotes,
        "urls": urls,
        "domains": domains,
        "proper": proper,
        "numeric": numeric,
    }


def _fragment_list(fragments: dict[str, list[str]]) -> list[str]:
    """All content fragments, deduplicated, in priority order."""
    merged = (
        fragments["quotes"]
        + fragments["urls"]
        + fragments["domains"]
        + fragments["proper"]
        + fragments["numeric"]
    )
    return list(dict.fromkeys(normalize(f) for f in merged if normalize(f)))


def has_verifiable_anchors(expected: str) -> bool:
    """True when `expected` contains content we can search the evidence for.

    A postcondition made only of prose ("the page loads") has no anchors and
    cannot be textually judged — callers fall back to dispatch success.
    """
    return bool(_fragment_list(extract_fragments(expected)))


def verify_text(actual: str, expected: str) -> Verdict:
    """Verdict for one expected-postcondition string against actual text.

    - PASS: the expected text (normalized) is a substring of `actual`, OR
      enough of the postcondition's content fragments are present in it.
    - UNCERTAIN: either side is empty/blank — no text evidence to judge.
    - FAIL: otherwise.
    """
    if not expected or not expected.strip():
        return Verdict.UNCERTAIN
    if not actual or not actual.strip():
        return Verdict.UNCERTAIN

    actual_norm = normalize(actual)
    expected_norm = normalize(expected)
    if expected_norm in actual_norm:
        return Verdict.PASS

    fragments = extract_fragments(expected)
    anchors = _fragment_list(fragments)
    if anchors:
        # Quoted strings are the most deliberate content references — all of
        # them must be present.
        quotes = [normalize(q) for q in fragments["quotes"]]
        if quotes and any(q not in actual_norm for q in quotes):
            return Verdict.FAIL
        found = sum(1 for anchor in anchors if anchor in actual_norm)
        if found >= 1 and found / len(anchors) >= 0.5:
            return Verdict.PASS
        return Verdict.FAIL

    # No content fragments: pure prose. Fall back to significant-token
    # overlap (strict — a clean dispatch fallback is the servers' job).
    tokens = [
        t
        for t in re.findall(r"[a-z0-9]{3,}", expected_norm)
        if t not in _STOPWORDS
    ]
    if not tokens:
        return Verdict.FAIL
    found = sum(1 for t in tokens if t in actual_norm)
    return Verdict.PASS if found >= max(1, (len(tokens) + 1) // 2) else Verdict.FAIL


def matched_fragments(actual: str, expected: str) -> list[str]:
    """Which content fragments of `expected` are present in `actual`."""
    if not expected or not expected.strip() or not actual or not actual.strip():
        return []
    actual_norm = normalize(actual)
    return [a for a in _fragment_list(extract_fragments(expected)) if a in actual_norm]


def ax_text(state: AccessibilityState | None) -> str:
    """Flatten an AX tree into one searchable string.

    Concatenates titles, descriptions, and values of every node,
    space-separated. Empty string when there is no usable tree.
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


def verify(
    expected_postcondition: str,
    actual_text: str,
    source: ObservationSource | str,
) -> VerificationResult:
    """Assemble a `VerificationResult` (Architecture §8 `gui.verify` output)."""
    verdict = verify_text(actual_text, expected_postcondition)
    source_used = source.value if isinstance(source, ObservationSource) else source
    return VerificationResult(
        verdict=verdict,
        evidence={
            "expected": expected_postcondition,
            "actual_text": actual_text[:400],
            "actual_text_length": len(actual_text),
            "match": verdict is Verdict.PASS,
            "matched_fragments": matched_fragments(actual_text, expected_postcondition),
        },
        source_used=source_used,
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