"""In-memory session store for the Gateway MCP server.

Keeps the per-session action history (action, expected_postcondition,
dispatch record, observation) so `gui.verify` can re-check postconditions
and the M4 recovery engine can walk the attempt history. Thread-safe enough
for the single-threaded asyncio server; a persistent store is out of scope
(PRD §15 session reconstruction arrives with structured logging).
"""

from __future__ import annotations

import uuid
from typing import Any


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def ensure(self, session_id: str) -> dict[str, Any]:
        return self._sessions.setdefault(session_id, {"actions": {}})

    def record_action(
        self,
        session_id: str,
        *,
        action: Any,
        expected_postcondition: str,
        record: Any,
        observation: Any,
        verdict: Any,
    ) -> str:
        action_id = uuid.uuid4().hex[:12]
        session = self.ensure(session_id)
        session["actions"][action_id] = {
            "action": action,
            "expected_postcondition": expected_postcondition,
            "record": record,
            "observation": observation,
            "verdict": verdict,
        }
        return action_id

    def get_action(self, session_id: str, action_id: str) -> dict[str, Any] | None:
        return self.ensure(session_id)["actions"].get(action_id)

    def latest_action(self, session_id: str) -> dict[str, Any] | None:
        actions = self.ensure(session_id)["actions"]
        if not actions:
            return None
        return actions[sorted(actions)[-1]]