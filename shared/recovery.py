"""Recovery engine — bounded retry/re-observe/escalate loop (Architecture §13).

Pure logic, shared by the Gateway (M4) and the Browser Adapter (M5):

    attempt 1 fails  -> "retry"    (same action, re-observe + re-verify)
    attempt 2 fails  -> "reobserve" (alternate state source, e.g. vision)
    attempt 3+ fails -> "escalate"  (budget exhausted — planner/user decides)

`RecoveryBudget` caps attempts and wall-clock time. `RecoveryTracker` keeps
per-subtask state. Every attempt is written to the session's structured
JSON-lines log (Architecture §11/§17 event model), so a session is fully
reconstructable after the fact.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.action_schema import Verdict

STRATEGY_RETRY = "retry"
STRATEGY_REOBSERVE = "reobserve"
STRATEGY_ESCALATE = "escalate"
STRATEGY_DONE = "done"


@dataclass(frozen=True)
class RecoveryBudget:
    max_attempts: int = 3
    max_seconds: float = 300.0

    def to_dict(self) -> dict[str, Any]:
        return {"max_attempts": self.max_attempts, "max_seconds": self.max_seconds}


class RecoveryTracker:
    """State machine over one subtask's act-and-verify attempts."""

    def __init__(self, budget: RecoveryBudget | None = None) -> None:
        self.budget = budget or RecoveryBudget()
        self.attempts = 0
        self.strategies: list[str] = []
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.budget.max_attempts or self.elapsed >= self.budget.max_seconds

    def next_strategy(self, verdict: Verdict) -> str:
        """Strategy for the attempt after the one that produced `verdict`."""
        if verdict is Verdict.PASS:
            return STRATEGY_DONE
        if self.exhausted:
            return STRATEGY_ESCALATE
        if self.attempts < 2:
            return STRATEGY_RETRY
        return STRATEGY_REOBSERVE

    def advance(self) -> None:
        self.attempts += 1

    def summary(self, escalated: bool) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "strategies": self.strategies,
            "escalated": escalated,
            "budget": self.budget.to_dict(),
        }


def choose_recovery_strategy(attempts_used: int, verdict: Verdict, budget: RecoveryBudget | None = None) -> str:
    """Stateless strategy selection (Architecture §18 unit-test target).

    `attempts_used` = number of act-and-verify attempts already consumed.
    Mirrors `RecoveryTracker.next_strategy`.
    """
    if verdict is Verdict.PASS:
        return STRATEGY_DONE
    b = budget or RecoveryBudget()
    if attempts_used >= b.max_attempts:
        return STRATEGY_ESCALATE
    if attempts_used < 2:
        return STRATEGY_RETRY
    return STRATEGY_REOBSERVE


def append_event(
    log_dir: Path,
    *,
    session_id: str,
    worker: str,
    event_type: str,
    **fields: Any,
) -> Path:
    """Append one structured event (Architecture §11/§17) to
    `log_dir/session-<session_id>.jsonl`. Returns the log file path."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"session-{session_id}.jsonl"
    line = {
        "event": event_type,
        "session_id": session_id,
        "worker": worker,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **fields,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(line) + "\n")
    return log_path
