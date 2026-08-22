"""Browser action dispatch — maps browser.act to Browser-Use Controller actions.

Architecture §7: The Browser Adapter exposes single-action primitives
(navigate, click, type, extract, scroll, select, key) backed by
Browser-Use's Controller/Tools registry and BrowserSession DOM state.
Explicitly does NOT use Browser-Use's bundled Agent (which runs its own
LLM planning loop).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from browser_use import BrowserSession
from browser_use.controller import Controller
from browser_use.browser import BrowserProfile

from shared.action_schema import Action, ActionType, BrowserActionType, ActionRecord

logger = logging.getLogger("browser_adapter.dispatch")


class BrowserDispatchError(Exception):
    """Raised when an action cannot be dispatched."""


class BrowserDispatcher:
    """Thin dispatcher from normalized browser actions to Browser-Use Controller."""

    def __init__(self, browser_session: BrowserSession, controller: Controller):
        self._session = browser_session
        self._controller = controller
        self._action_model = None

    def _model(self):
        if self._action_model is None:
            self._action_model = self._controller.registry.create_action_model()
        return self._action_model

    async def dispatch(self, action: Action) -> ActionRecord:
        """Execute one normalized browser action via Controller registry."""
        try:
            if action.type == BrowserActionType.NAVIGATE:
                return await self._navigate(action)
            elif action.type == BrowserActionType.CLICK:
                return await self._click(action)
            elif action.type == BrowserActionType.TYPE:
                return await self._type(action)
            elif action.type == BrowserActionType.EXTRACT:
                return await self._extract(action)
            elif action.type == BrowserActionType.SCROLL:
                return await self._scroll(action)
            elif action.type == BrowserActionType.SELECT:
                return await self._select(action)
            elif action.type == BrowserActionType.KEY:
                return await self._key(action)
            else:
                raise BrowserDispatchError(f"unknown browser action type: {action.type}")
        except Exception as exc:
            logger.exception("dispatch failed for action %s", action.type)
            return ActionRecord(
                action_id="",
                dispatched=False,
                raw_result={"error": str(exc)},
            )

    async def _navigate(self, action: Action) -> ActionRecord:
        url = action.params.get("url") or action.target_hint
        if not url:
            raise BrowserDispatchError("navigate requires 'url' in params or target_hint")
        new_tab = bool(action.params.get("new_tab", False))
        am = self._model()(navigate={"url": url, "new_tab": new_tab})
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"navigated_to": url, "result": str(result)},
        )

    async def _click(self, action: Action) -> ActionRecord:
        index = action.params.get("index")
        if index is None:
            # Try to find element by target_hint text matching
            index = await self._find_element_index(action.target_hint)
            if index is None:
                raise BrowserDispatchError(
                    "click requires 'index' in params (1-based element index from browser.observe) "
                    "or a target_hint that matches an element"
                )
        index = int(index)
        if index < 1:
            index = 1
        am = self._model()(click={"index": index})
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"clicked_index": index, "result": str(result)},
        )

    async def _find_element_index(self, target_hint: str) -> int | None:
        """Find element index by matching target_hint against element text/attributes.
        
        Returns 1-based index or None if not found.
        """
        if not target_hint:
            return None
        try:
            state = await self._session.get_browser_state_summary()
            selector_map = state.dom_state.selector_map
            hint_lower = target_hint.lower()
            
            # First pass: exact text match
            for idx, element in selector_map.items():
                text = (element.get_all_text() or "").strip().lower()
                if text and hint_lower in text:
                    return idx
            
            # Second pass: attribute match (placeholder, aria-label, title, alt)
            for idx, element in selector_map.items():
                attrs = [
                    element.attributes.get("placeholder", "").lower(),
                    element.attributes.get("aria-label", "").lower(),
                    element.attributes.get("title", "").lower(),
                    element.attributes.get("alt", "").lower(),
                    element.attributes.get("value", "").lower(),
                ]
                if any(hint_lower in attr for attr in attrs if attr):
                    return idx
            
            # Third pass: tag name match for common elements
            tag_keywords = {
                "input": "input",
                "button": "button",
                "link": "a",
                "link": "link",
                "search": "input",
                "button": "button",
                "submit": "button",
                "search box": "input",
                "text field": "input",
                "textbox": "input",
                "dropdown": "select",
                "menu": "select",
            }
            for keyword, tag in tag_keywords.items():
                if keyword in target_hint.lower():
                    for idx, element in selector_map.items():
                        if element.tag_name == tag:
                            return idx
            
            return None
        except Exception as exc:
            logger.warning("_find_element_index failed: %s", exc)
            return None

    async def _type(self, action: Action) -> ActionRecord:
        index = action.params.get("index")
        text = action.params.get("text", "")
        clear = action.params.get("clear", True)
        if index is None:
            # Try to find element by target_hint text matching
            index = await self._find_element_index(action.target_hint)
            if index is None:
                raise BrowserDispatchError(
                    "type requires 'index' in params (1-based element index from browser.observe) "
                    "or a target_hint that matches an element"
                )
        am = self._model()(input={"index": int(index), "text": text, "clear": clear})
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"typed_index": index, "text": text, "result": str(result)},
        )

    async def _extract(self, action: Action) -> ActionRecord:
        """Deterministic DOM extraction (no extraction LLM required).

        `query`/`text` is tried as a CSS selector first; if it matches
        nothing it is matched against element text content. Returns up to
        20 matches as text (optionally with href/src when the flags are
        set). This avoids browser-use's LLM-gated extract, which errors
        with 'Action extract requires page_extraction_llm' when no
        extraction model is configured.
        """
        params = action.params or {}
        query = params.get("query") or params.get("text") or action.target_hint
        if not query:
            raise BrowserDispatchError("extract requires 'query'/'text' in params or target_hint")
        extract_links = bool(params.get("extract_links", False))
        extract_images = bool(params.get("extract_images", False))
        try:
            page = await self._session.must_get_current_page()
            raw = await page.evaluate(self._EXTRACT_JS, query, extract_links, extract_images)
        except Exception as exc:
            logger.exception("extract failed for query %s", query)
            return ActionRecord(
                action_id="",
                dispatched=False,
                raw_result={"query": query, "error": str(exc)},
            )
        try:
            matches = json.loads(raw)
        except Exception:
            matches = raw
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"query": query, "matches": matches},
        )

    _EXTRACT_JS = r"""
(query, extractLinks, extractImages) => {
  const cap = 20;
  const out = [];
  const firstLink = (el) => { const a = el.tagName === "A" ? el : el.querySelector("a"); return a ? a.href : null; };
  const firstImg = (el) => { const i = el.tagName === "IMG" ? el : el.querySelector("img"); return i ? i.src : null; };
  const push = (el) => {
    const text = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 2000);
    const entry = { text };
    if (extractLinks) entry.href = firstLink(el);
    if (extractImages) entry.src = firstImg(el);
    out.push(entry);
  };
  let els = [];
  try { els = Array.from(document.querySelectorAll(query)); } catch (e) { els = []; }
  if (els.length === 0) {
    els = Array.from(document.querySelectorAll("h1,h2,h3,h4,p,li,a,button,span,td,th,div,img"))
      .filter((el) => (el.textContent || "").trim().toLowerCase().includes(query.toLowerCase()));
  }
  els.slice(0, cap).forEach(push);
  return JSON.stringify(out);
}
"""

    async def _scroll(self, action: Action) -> ActionRecord:
        down = action.params.get("down", True)
        pages = action.params.get("pages", 1.0)
        index = action.params.get("index")
        params = {"down": down, "pages": pages}
        if index is not None:
            params["index"] = int(index)
        am = self._model()(scroll=params)
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"scroll_params": params, "result": str(result)},
        )

    async def _select(self, action: Action) -> ActionRecord:
        index = action.params.get("index")
        option = action.params.get("option")
        if index is None or option is None:
            raise BrowserDispatchError("select requires 'index' and 'option' in params")
        am = self._model()(select_dropdown={"index": int(index), "text": option})
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"selected_index": index, "option": option, "result": str(result)},
        )

    async def _key(self, action: Action) -> ActionRecord:
        keys = action.params.get("keys")
        if not keys:
            raise BrowserDispatchError("key requires 'keys' in params")
        am = self._model()(send_keys={"keys": keys})
        result = await self._controller.act(am, browser_session=self._session)
        return ActionRecord(
            action_id="",
            dispatched=True,
            raw_result={"keys": keys, "result": str(result)},
        )

    async def read_element_value(self, index: int) -> str:
        """Live `value`/text of the interactive element at `index`.

        The DOM state's serialized text does not include input values, so
        type verification reads the element directly. Returns "" when the
        element is gone or lives in another frame.
        """
        try:
            state = await self._session.get_browser_state_summary()
            element = state.dom_state.selector_map.get(int(index))
            if element is None:
                return ""
            xpath = element.xpath
            page = await self._session.must_get_current_page()
            return str(
                await page.evaluate(
                    """(xp) => {
                        const el = document.evaluate(
                            xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
                        ).singleNodeValue;
                        if (!el) return '';
                        if (el.value !== undefined) return el.value;
                        return el.textContent || '';
                    }""",
                    xpath,
                )
            )
        except Exception:
            logger.exception("read_element_value(%s) failed", index)
            return ""


def create_browser_session(
    headless: bool = True,
    viewport: dict[str, int] | None = None,
) -> tuple[BrowserSession, Controller]:
    """Create a BrowserSession and Controller pair.

    Returns (session, controller). The caller is responsible for
    starting/stopping the session.
    """
    profile = BrowserProfile(
        headless=headless,
        viewport=viewport or {"width": 1280, "height": 720},
    )
    session = BrowserSession(browser_profile=profile)
    controller = Controller()
    return session, controller