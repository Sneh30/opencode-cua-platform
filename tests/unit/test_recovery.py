import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from shared.action_schema import Verdict
from shared.recovery import (
    STRATEGY_DONE,
    STRATEGY_ESCALATE,
    STRATEGY_REOBSERVE,
    STRATEGY_RETRY,
    RecoveryBudget,
    RecoveryTracker,
    append_event,
    choose_recovery_strategy,
)


class TestChooseRecoveryStrategy:
    def test_pass_is_done(self):
        assert choose_recovery_strategy(0, Verdict.PASS) is STRATEGY_DONE
        assert choose_recovery_strategy(3, Verdict.PASS) is STRATEGY_DONE

    def test_first_failure_retries(self):
        assert choose_recovery_strategy(0, Verdict.FAIL) is STRATEGY_RETRY

    def test_second_failure_reobserves(self):
        assert choose_recovery_strategy(1, Verdict.FAIL) is STRATEGY_RETRY
        assert choose_recovery_strategy(2, Verdict.FAIL) is STRATEGY_REOBSERVE

    def test_uncertain_follows_same_ladder(self):
        assert choose_recovery_strategy(0, Verdict.UNCERTAIN) is STRATEGY_RETRY
        assert choose_recovery_strategy(2, Verdict.UNCERTAIN) is STRATEGY_REOBSERVE

    def test_budget_exhausted_escalates(self):
        b = RecoveryBudget(max_attempts=3)
        assert choose_recovery_strategy(3, Verdict.FAIL, b) is STRATEGY_ESCALATE

    def test_custom_budget(self):
        b = RecoveryBudget(max_attempts=1)
        assert choose_recovery_strategy(1, Verdict.FAIL, b) is STRATEGY_ESCALATE
        assert choose_recovery_strategy(0, Verdict.FAIL, b) is STRATEGY_RETRY


class TestRecoveryTracker:
    def test_ladder_with_advance(self):
        t = RecoveryTracker()
        t.advance()
        assert t.next_strategy(Verdict.FAIL) is STRATEGY_RETRY
        t.advance()
        assert t.next_strategy(Verdict.FAIL) is STRATEGY_REOBSERVE
        t.advance()
        assert t.exhausted
        assert t.next_strategy(Verdict.FAIL) is STRATEGY_ESCALATE

    def test_pass_breaks_ladder(self):
        t = RecoveryTracker()
        t.advance()
        assert t.next_strategy(Verdict.PASS) is STRATEGY_DONE

    def test_max_seconds_budget(self):
        t = RecoveryTracker(RecoveryBudget(max_attempts=10, max_seconds=0))
        t.advance()
        assert t.exhausted

    def test_summary(self):
        t = RecoveryTracker(RecoveryBudget(max_attempts=2))
        t.advance()
        t.advance()
        s = t.summary(escalated=True)
        assert s["attempts"] == 2
        assert s["escalated"] is True
        assert s["budget"]["max_attempts"] == 2


class TestAppendEvent:
    def test_writes_jsonl_line(self, tmp_path):
        path = append_event(
            tmp_path,
            session_id="s1",
            worker="gateway",
            event_type="recovery.attempted",
            attempt=1,
            strategy="retry",
        )
        assert path.name == "session-s1.jsonl"
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "recovery.attempted"
        assert event["session_id"] == "s1"
        assert event["worker"] == "gateway"
        assert event["attempt"] == 1
        assert "timestamp" in event

    def test_appends_across_events(self, tmp_path):
        append_event(tmp_path, session_id="s2", worker="gateway", event_type="a")
        append_event(tmp_path, session_id="s2", worker="gateway", event_type="b")
        lines = (tmp_path / "session-s2.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["event"] == "b"

    def test_creates_log_dir(self, tmp_path):
        path = append_event(tmp_path / "nested" / "logs", session_id="s3", worker="gateway", event_type="a")
        assert path.exists()