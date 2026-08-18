"""Browser Adapter MCP server — Milestone M5.

Exposes browser.observe / browser.act / browser.verify tools backed by
Browser-Use's Controller/Tools registry and BrowserSession (Playwright,
DOM-first). Does NOT use Browser-Use's bundled Agent — OpenCode remains
the sole planner.

The Master Prompt is delivered into OpenCode session context via the MCP
`instructions` field.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# Add repo root so `import shared` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from shared.action_schema import (
    Action,
    BrowserActionType,
    Observation,
    Verdict,
    VerificationResult,
)
from shared.recovery import (
    RecoveryBudget,
    RecoveryTracker,
    STRATEGY_ESCALATE,
    append_event,
)
from shared.verifier import verify_text, best_available_source

from browser_adapter.action_dispatch import BrowserDispatcher, create_browser_session
from browser_adapter.session_wrapper import BrowserSessionWrapper

logger = logging.getLogger("browser_adapter")

# Master Prompt for browser tasks (subset of full Master Prompt)
BROWSER_MASTER_PROMPT = """\
# Role

You are operating as the supervisor of a computer-use platform. You have
access to four categories of tools:

  - Native tools: terminal / shell, filesystem read-write
  - gui.* tools (CUA Gateway): control of real desktop applications via
    screenshots and grounded actions
  - browser.* tools (Browser Adapter): control of a real web browser via
    DOM-level observation and single-step actions
  - session.* tools: session state, permission checks, halt

You are the only planner in this system. browser.* tools are hands,
not collaborators — they execute one grounded action or one observation
per call and report back. They do not decide what to do next. That is
always your job.

# Tool Selection for Browser Tasks

For browser-hosted tasks, prefer browser.* tools over gui.* tools.
The Browser Adapter reads DOM state directly; it is faster, cheaper,
and more reliable than treating a browser as a generic desktop app.

# Before every browser.act call

State what state you expect to exist after the action succeeds. The
Browser Adapter requires an `expected_postcondition` on every act call
and will reject calls that omit one. Write a checkable postcondition
("a button with text 'Submit' exists", "the page contains 'Welcome'"),
not vague ("the page loads").

# Observation and Verification Discipline

An action being dispatched is not the same as an action succeeding.
After any browser.act call:
  1. Call browser.verify before treating the step as done.
  2. If verdict is "pass", proceed. If "fail" or "uncertain", go to
     Recovery. Do not retry silently.
  3. If unsure whether a step succeeded, say so explicitly rather than
     asserting success.
"""

ACT_TYPES = [
    "navigate",
    "click",
    "type",
    "extract",
    "scroll",
    "select",
    "key",
]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "browser.observe": {
        "description": (
            "Return the current browser state via DOM/accessibility "
            "(primary) with optional screenshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "include_screenshot": {"type": "boolean", "default": True},
            },
            "required": ["session_id"],
        },
    },
    "browser.act": {
        "description": (
            "Execute one normalized browser action. `expected_postcondition` "
            "is required and fed to the verifier; the verdict is returned "
            "with the dispatch record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "action": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ACT_TYPES},
                        "target_hint": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["type", "target_hint"],
                },
                "expected_postcondition": {"type": "string"},
                "recovery_budget": {
                    "type": "object",
                    "properties": {
                        "max_attempts": {"type": "integer", "minimum": 1},
                        "max_seconds": {"type": "number", "minimum": 1},
                    },
                },
            },
            "required": ["session_id", "action", "expected_postcondition"],
        },
    },
    "browser.verify": {
        "description": (
            "Re-check whether an action's expected postcondition holds "
            "against the current browser DOM state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["session_id", "action_id"],
        },
    },
}


def _stub_result(**payload: Any) -> dict[str, Any]:
    payload.setdefault("stub", True)
    payload.setdefault("note", "Browser Adapter not started")
    return payload


def _missing(field: str) -> dict[str, Any]:
    return {"error": f"missing required field: {field}"}


def _error(message: str) -> dict[str, Any]:
    return {"error": message}


class LiveBrowserAdapter:
    """Real browser executor loop behind the browser.* tools."""

    def __init__(self, logs_dir: Path, headless: bool = True):
        self._logs_dir = logs_dir
        self._headless = headless
        self._session_wrapper: BrowserSessionWrapper | None = None
        self._dispatcher: BrowserDispatcher | None = None
        self._store = {}  # session_id -> {"actions": {action_id: entry}}
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_session(self) -> None:
        """Lazy-start the browser session on first use."""
        if self._session_wrapper is None:
            self._session_wrapper = BrowserSessionWrapper(headless=self._headless)
            await self._session_wrapper.start()
            session, controller = create_browser_session(headless=self._headless)
            # Use the existing session from wrapper
            self._dispatcher = BrowserDispatcher(self._session_wrapper.session, controller)

    # -- observation ---------------------------------------------------------

    async def observe(self, session_id: str, include_screenshot: bool = True) -> dict[str, Any]:
        await self._ensure_session()
        observation = await self._session_wrapper.observe()
        return {
            "screenshot_ref": observation.screenshot_ref,
            "accessibility_tree": observation.accessibility_tree,
            "active_app": observation.active_app,
            "source_used": observation.source_used.value,
        }

    # -- act + verify --------------------------------------------------------

    async def act(
        self,
        session_id: str,
        action_data: dict[str, Any],
        expected_postcondition: str,
        recovery_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Architecture §13: act-and-verify loop with bounded recovery."""
        await self._ensure_session()

        budget = RecoveryBudget(
            max_attempts=int(recovery_budget.get("max_attempts", 3)) if recovery_budget else 3,
            max_seconds=float(recovery_budget.get("max_seconds", 300)) if recovery_budget else 300.0,
        )
        tracker = RecoveryTracker(budget)

        action = self._resolve_action(action_data)

        append_event(
            self._logs_dir,
            session_id=session_id,
            worker="browser_adapter",
            event_type="action.dispatched",
            action=action.to_dict(),
            expected_postcondition=expected_postcondition,
        )

        result = None
        while not tracker.exhausted:
            tracker.advance()

            record = await self._dispatcher.dispatch(action)

            # Get DOM text for verification
            dom_text = await self._session_wrapper.get_dom_text()

            # Verify using DOM text (browser uses DOM as primary source)
            verdict = verify_text(dom_text, expected_postcondition)
            result = VerificationResult(
                verdict=verdict,
                evidence={
                    "expected": expected_postcondition,
                    "actual_text": dom_text[:400],
                    "actual_text_length": len(dom_text),
                    "match": verdict is Verdict.PASS,
                },
                source_used="dom",
            )

            append_event(
                self._logs_dir,
                session_id=session_id,
                worker="browser_adapter",
                event_type="verification.result",
                attempt=tracker.attempts,
                verdict=result.verdict.value,
                source_used=result.source_used,
                evidence=result.evidence,
            )

            strategy = tracker.next_strategy(result.verdict)
            if strategy == "done":
                tracker.strategies.append("done")
                break

            tracker.strategies.append(strategy)
            append_event(
                self._logs_dir,
                session_id=session_id,
                worker="browser_adapter",
                event_type="recovery.attempted",
                attempt=tracker.attempts,
                strategy=strategy,
                verdict=result.verdict.value,
            )

        escalated = tracker.attempts >= tracker.budget.max_attempts and result and result.verdict is not Verdict.PASS
        if escalated:
            append_event(
                self._logs_dir,
                session_id=session_id,
                worker="browser_adapter",
                event_type="recovery.exhausted",
                attempts=tracker.attempts,
            )

        action_id = self._store_action(
            session_id,
            action=action.to_dict(),
            expected_postcondition=expected_postcondition,
            record=record.to_dict(),
            verdict=result.to_dict(),
        )

        return {
            "action_id": action_id,
            "dispatched": record.dispatched,
            "raw_result": record.raw_result,
            "verdict": result.verdict.value,
            "evidence": result.evidence,
            "source_used": result.source_used,
            "recovery": tracker.summary(escalated),
        }

    async def verify(self, session_id: str, action_id: str) -> dict[str, Any] | None:
        entry = self._get_action(session_id, action_id)
        if entry is None:
            return None

        dom_text = await self._session_wrapper.get_dom_text()
        verdict = verify_text(dom_text, entry["expected_postcondition"])
        result = VerificationResult(
            verdict=verdict,
            evidence={
                "expected": entry["expected_postcondition"],
                "actual_text": dom_text[:400],
                "actual_text_length": len(dom_text),
                "match": verdict is Verdict.PASS,
            },
            source_used="dom",
        )
        return {
            "verdict": result.verdict.value,
            "evidence": result.evidence,
            "source_used": result.source_used,
        }

    def _resolve_action(self, action_data: dict[str, Any]) -> Action:
        return Action(
            type=BrowserActionType(action_data["type"]),
            target_hint=action_data.get("target_hint", ""),
            params=action_data.get("params", {}) or {},
        )

    def _store_action(
        self,
        session_id: str,
        action: dict[str, Any],
        expected_postcondition: str,
        record: dict[str, Any],
        verdict: dict[str, Any],
    ) -> str:
        action_id = uuid.uuid4().hex[:12]
        if session_id not in self._store:
            self._store[session_id] = {"actions": {}}
        self._store[session_id]["actions"][action_id] = {
            "action": action,
            "expected_postcondition": expected_postcondition,
            "record": record,
            "verdict": verdict,
        }
        return action_id

    def _get_action(self, session_id: str, action_id: str) -> dict[str, Any] | None:
        return self._store.get(session_id, {}).get("actions", {}).get(action_id)

    async def close(self) -> None:
        if self._session_wrapper:
            await self._session_wrapper.stop()


_LIVE_ADAPTER: LiveBrowserAdapter | None = None


def _make_live_adapter() -> LiveBrowserAdapter:
    global _LIVE_ADAPTER
    if _LIVE_ADAPTER is None:
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        headless = os.environ.get("BROWSER_HEADLESS", "true").lower() != "false"
        _LIVE_ADAPTER = LiveBrowserAdapter(logs_dir, headless=headless)
    return _LIVE_ADAPTER


def build_server() -> Server:
    server = Server(
        "browser-adapter",
        version="0.1.0",
        instructions=BROWSER_MASTER_PROMPT,
    )

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=schema["description"],
                inputSchema=schema["inputSchema"],
            )
            for name, schema in TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name not in TOOL_SCHEMAS:
            return [types.TextContent(type="text", text=json.dumps(_error(f"unknown tool: {name}"), indent=2))]

        adapter = _make_live_adapter()

        try:
            if name == "browser.observe":
                result = await adapter.observe(
                    arguments.get("session_id", ""),
                    arguments.get("include_screenshot", True),
                )
                return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

            if name == "browser.act":
                if not arguments.get("expected_postcondition"):
                    return [types.TextContent(type="text", text=json.dumps(_missing("expected_postcondition"), indent=2))]
                action = arguments.get("action") or {}
                if action.get("type") not in ACT_TYPES:
                    return [types.TextContent(type="text", text=json.dumps(_missing("action.type"), indent=2))]
                try:
                    result = await adapter.act(
                        arguments.get("session_id", ""),
                        action,
                        arguments["expected_postcondition"],
                        recovery_budget=arguments.get("recovery_budget"),
                    )
                    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
                except Exception as exc:
                    logger.exception("act failed")
                    return [types.TextContent(type="text", text=json.dumps(_error(f"browser adapter error: {exc}"), indent=2))]

            if name == "browser.verify":
                if not arguments.get("action_id"):
                    return [types.TextContent(type="text", text=json.dumps(_missing("action_id"), indent=2))]
                result = await adapter.verify(arguments.get("session_id", ""), arguments["action_id"])
                if result is None:
                    return [types.TextContent(type="text", text=json.dumps(_error(f"unknown action_id: {arguments['action_id']}"), indent=2))]
                return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as exc:
            logger.exception("handler failed")
            return [types.TextContent(type="text", text=json.dumps(_error(f"browser adapter error: {exc}"), indent=2))]

        raise AssertionError("unreachable")

    return server


async def amain() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(amain())


if __name__ == "__main__":
    main()