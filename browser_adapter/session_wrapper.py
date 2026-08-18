"""Browser Adapter session wrapper — thin wrapper over Browser-Use's BrowserSession.

Provides DOM-first observation (structured state) and manages the browser
lifecycle. Does NOT use Browser-Use's Agent — the Adapter calls Controller
actions directly, keeping OpenCode as the sole planner.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

from browser_use import BrowserSession

from shared.action_schema import (
    Observation,
    ObservationSource,
    Screenshot,
)

logger = logging.getLogger("browser_adapter.session")


class BrowserSessionWrapper:
    """Wraps BrowserSession to provide observe/act/verify primitives."""

    def __init__(
        self,
        headless: bool = True,
        viewport: dict[str, int] | None = None,
    ):
        self._headless = headless
        self._viewport = viewport or {"width": 1280, "height": 720}
        self._session: BrowserSession | None = None
        self._started = False

    async def start(self) -> None:
        """Start the browser session."""
        if self._started:
            return
        from browser_use.browser import BrowserProfile

        profile = BrowserProfile(
            headless=self._headless,
            viewport=self._viewport,
        )
        self._session = BrowserSession(browser_profile=profile)
        await self._session.start()
        self._started = True
        logger.info("Browser session started")

    async def stop(self) -> None:
        """Stop the browser session."""
        if self._session and self._started:
            await self._session.stop()
            self._started = False
            logger.info("Browser session stopped")

    async def __aenter__(self) -> "BrowserSessionWrapper":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    @property
    def session(self) -> BrowserSession | None:
        return self._session

    @property
    def is_started(self) -> bool:
        return self._started

    async def observe(self) -> Observation:
        """Return current browser state via DOM/accessibility.

        This is the DOM-first observation — no screenshot needed unless
        the caller explicitly requests one.
        """
        if not self._session:
            raise RuntimeError("session not started")

        state = await self._session.get_browser_state_summary()
        dom_text = self._dom_to_text(state)

        # Take a screenshot for the screenshot_ref (required by Observation schema)
        screenshot_path = await self._take_screenshot()

        return Observation(
            screenshot_ref=screenshot_path,
            accessibility_tree={
                "url": state.url,
                "title": state.title,
                "dom_text": dom_text[:5000],  # Truncate for size
                "tabs": [
                    {
                        "url": tab.url,
                        "title": tab.title,
                        "target_id": tab.target_id,
                    }
                    for tab in state.tabs
                ],
                "focused_element": state.focused_element,
            },
            active_app="browser",
            source_used=ObservationSource.DOM,
            extra={
                "url": state.url,
                "title": state.title,
                "viewport_width": self._viewport["width"],
                "viewport_height": self._viewport["height"],
            },
        )

    async def take_screenshot(self, output_path: str | Path | None = None) -> Screenshot:
        """Capture a screenshot and return a Screenshot object."""
        if not self._session:
            raise RuntimeError("session not started")

        if output_path is None:
            output_path = f"/tmp/browser_screenshot_{int(time.time() * 1000)}.png"
        else:
            output_path = str(output_path)

        await self._session.take_screenshot(output_path)

        return Screenshot(
            ref=output_path,
            width=self._viewport["width"],
            height=self._viewport["height"],
            captured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            display_width=self._viewport["width"],
            display_height=self._viewport["height"],
        )

    async def _take_screenshot(self) -> str:
        """Take a screenshot and return the path."""
        path = f"/tmp/browser_screenshot_{int(time.time() * 1000)}.png"
        await self._session.take_screenshot(path)
        return path

    def _dom_to_text(self, state) -> str:
        """Convert DOM state to searchable text for verification."""
        parts = []

        if state.url:
            parts.append(f"URL: {state.url}")
        if state.title:
            parts.append(f"Title: {state.title}")

        # Add interactive elements
        for element in state.selector_map.values():
            text_parts = []
            if element.tag_name:
                text_parts.append(element.tag_name)
            if element.attributes.get("id"):
                text_parts.append(f"id={element.attributes['id']}")
            if element.attributes.get("class"):
                text_parts.append(f"class={element.attributes['class']}")
            if element.attributes.get("placeholder"):
                text_parts.append(f"placeholder={element.attributes['placeholder']}")
            if element.attributes.get("type"):
                text_parts.append(f"type={element.attributes['type']}")
            if element.attributes.get("value"):
                text_parts.append(f"value={element.attributes['value']}")
            if element.text and element.text.strip():
                text_parts.append(element.text.strip())

            if text_parts:
                parts.append(" ".join(text_parts))

        return " | ".join(parts)

    async def get_dom_text(self) -> str:
        """Get the current DOM as searchable text."""
        if not self._session:
            raise RuntimeError("session not started")
        state = await self._session.get_browser_state_summary()
        return self._dom_to_text(state)