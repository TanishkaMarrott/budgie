"""Cumulative total-dollar budget (BUDGIE_BUDGET) — the net sum of what's been
performed (dollars already spent + everything still running, projected over a
horizon) must not cross the session budget. This is separate from the $/hr rate
cap and catches slow accrual + create/tear-down churn the rate cap misses."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _seed(tmp_path, sid, active=0.0, accrued=0.0, hours_ago=0.0):
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    ts = (datetime.datetime.now() - datetime.timedelta(hours=hours_ago)).isoformat()
    (d / f"{sid}.json").write_text(
        json.dumps({"active_rate": active, "accrued_cost": accrued, "last_ts": ts, "resources": {}})
    )


def _ev(tmp_path, monkeypatch, cmd, sid="s", budget=None, horizon=None, cap=100.0):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    if budget is not None:
        monkeypatch.setenv("BUDGIE_BUDGET", str(budget))
    if horizon is not None:
        monkeypatch.setenv("BUDGIE_HORIZON", str(horizon))
    from budgie.state import evaluate

    return evaluate(cmd, cap, sid)


T3 = "aws ec2 run-instances --instance-type t3.medium"  # $0.0416/hr
BIG = "aws ec2 run-instances --instance-type c5.9xlarge"  # $1.53/hr


def test_no_budget_env_means_no_total_gate(tmp_path, monkeypatch):
    # Without BUDGIE_BUDGET, only the rate cap applies — a cheap box passes.
    d = _ev(tmp_path, monkeypatch, T3, cap=2.0)
    assert d.verdict == "allow"


def test_over_budget_single_command_blocks(tmp_path, monkeypatch):
    # $1.53/hr projected over 1h = $1.53 > $1 budget -> block (rate cap is high).
    d = _ev(tmp_path, monkeypatch, BIG, budget=1.0, horizon=1.0)
    assert d.verdict == "block" and "budget" in d.reason


def test_cheap_under_budget_allowed(tmp_path, monkeypatch):
    # t3.medium projected over 1h = $0.04 < $1 -> allow (must not over-block).
    d = _ev(tmp_path, monkeypatch, T3, budget=1.0, horizon=1.0)
    assert d.verdict == "allow"


def test_accrued_spend_shrinks_remaining_budget(tmp_path, monkeypatch):
    # $0.98 already spent; a $0.04/hr box over 1h -> $1.02 projected > $1 -> block.
    _seed(tmp_path, "s", active=0.0, accrued=0.98)
    d = _ev(tmp_path, monkeypatch, T3, budget=1.0, horizon=1.0)
    assert d.verdict == "block"


def test_horizon_scales_projection(tmp_path, monkeypatch):
    # $0.0416/hr over 1h = $0.04 (allow); over 30h = $1.25 (block).
    assert _ev(tmp_path, monkeypatch, T3, budget=1.0, horizon=1.0).verdict == "allow"
    assert _ev(tmp_path, monkeypatch, T3, sid="s2", budget=1.0, horizon=30.0).verdict == "block"


def test_running_resources_count_toward_budget(tmp_path, monkeypatch):
    # already burning $0.90/hr; a new $0.192/hr box -> (0.90+0.192)*1h = $1.09 > $1 -> block.
    _seed(tmp_path, "s", active=0.90, accrued=0.0)
    d = _ev(
        tmp_path, monkeypatch, "aws ec2 run-instances --instance-type m5.xlarge", budget=1.0, horizon=1.0
    )  # m5.xlarge $0.192
    assert d.verdict == "block"


def test_warn_when_approaching_budget(tmp_path, monkeypatch):
    # $0.85 spent + $0.04 over 1h = $0.89 -> between 0.8 and 1.0 of $1 -> warn.
    _seed(tmp_path, "s", active=0.0, accrued=0.85)
    d = _ev(tmp_path, monkeypatch, T3, budget=1.0, horizon=1.0)
    assert d.verdict == "warn" and "approaching" in d.reason


def test_override_bypasses_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_OK", "1")
    d = _ev(tmp_path, monkeypatch, BIG, budget=1.0, horizon=1.0)
    assert d.verdict == "allow"


def test_churn_is_bounded_by_accrued(tmp_path, monkeypatch):
    # A resource that actually ran (accrued $0.95) then was torn down (active=0):
    # a new create still sees the spent dollars and blocks. Rate cap alone wouldn't.
    _seed(tmp_path, "s", active=0.0, accrued=0.95)
    d = _ev(tmp_path, monkeypatch, "aws ec2 run-instances --instance-type m5.large", budget=1.0, horizon=1.0)
    assert d.verdict == "block"
