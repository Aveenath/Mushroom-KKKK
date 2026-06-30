"""
Tests for the alert decision logic in mist_alert.py.
Run with: pytest test_mist_alert.py -v
"""
import datetime
from mist_alert import _misting_decision, _should_send, _stale_cause_message, _heartbeat_update, REMIND_HOURS


# ── _misting_decision ────────────────────────────────────────────────────────

def test_low_humidity_triggers_mist_on():
    assert _misting_decision(temp=25, humidity=70) == "MIST ON"


def test_high_temp_triggers_mist_on():
    assert _misting_decision(temp=32, humidity=85) == "MIST ON"


def test_high_humidity_triggers_mist_off():
    assert _misting_decision(temp=27, humidity=95) == "MIST OFF"


def test_normal_conditions_trigger_nothing():
    assert _misting_decision(temp=27, humidity=85) is None


def test_humidity_exactly_at_boundary_is_normal():
    # 80.0 is not < 80.0, and 90.0 is not > 90.0 — both boundaries are inclusive-safe
    assert _misting_decision(temp=27, humidity=80.0) is None
    assert _misting_decision(temp=27, humidity=90.0) is None


def test_mist_on_takes_priority_when_both_breached():
    # temp > 30 AND humidity > 90 at once — MIST ON wins (matches original if/else order)
    assert _misting_decision(temp=32, humidity=95) == "MIST ON"


# ── _should_send ──────────────────────────────────────────────────────────────

def test_should_send_first_alert_when_no_prior_state():
    send_it, reason = _should_send("MIST ON", None)
    assert send_it is True
    assert "first alert" in reason


def test_should_send_when_status_changed():
    state = {"last_status": "MIST OFF", "last_sent": datetime.datetime.utcnow().isoformat()}
    send_it, reason = _should_send("MIST ON", state)
    assert send_it is True
    assert "changed" in reason


def test_should_not_send_when_throttled():
    recent = (datetime.datetime.utcnow() - datetime.timedelta(minutes=10)).isoformat()
    state = {"last_status": "MIST ON", "last_sent": recent}
    send_it, reason = _should_send("MIST ON", state)
    assert send_it is False
    assert "throttled" in reason


def test_should_send_reminder_after_remind_hours_elapsed():
    old = (datetime.datetime.utcnow() - datetime.timedelta(hours=REMIND_HOURS, minutes=5)).isoformat()
    state = {"last_status": "MIST ON", "last_sent": old}
    send_it, reason = _should_send("MIST ON", state)
    assert send_it is True
    assert "reminder" in reason


# ── _stale_cause_message ────────────────────────────────────────────────────────

def test_stale_cause_blames_device_when_sync_ok():
    msg = _stale_cause_message({"last_status": "OK", "last_sent": "2026-06-28T00:00:00"})
    assert "device" in msg.lower()
    assert "crashed" not in msg.lower()


def test_stale_cause_blames_device_when_no_sync_state_yet():
    msg = _stale_cause_message(None)
    assert "device" in msg.lower()


def test_stale_cause_blames_script_when_sync_errored():
    msg = _stale_cause_message({"last_status": "ERROR: 401 Unauthorized", "last_sent": "2026-06-28T00:00:00"})
    assert "crashed" in msg.lower()
    assert "401 Unauthorized" in msg


# ── _heartbeat_update ────────────────────────────────────────────────────────────

def test_heartbeat_first_run_ever_sends_no_message():
    new_status, msg = _heartbeat_update(None, "2026-06-29")
    assert new_status == "2026-06-29|1"
    assert msg is None


def test_heartbeat_same_day_just_increments_count():
    state = {"last_status": "2026-06-29|3", "last_sent": "irrelevant"}
    new_status, msg = _heartbeat_update(state, "2026-06-29")
    assert new_status == "2026-06-29|4"
    assert msg is None


def test_heartbeat_new_day_sends_summary_of_yesterday_and_resets():
    state = {"last_status": "2026-06-28|24", "last_sent": "irrelevant"}
    new_status, msg = _heartbeat_update(state, "2026-06-29")
    assert new_status == "2026-06-29|1"
    assert "24" in msg
    assert "2026-06-28" in msg
