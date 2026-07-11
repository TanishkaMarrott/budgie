"""Teardown: plan generation + executor (dry-run by default, double opt-in to run)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie.session import Resource, teardown_plan, execute_teardown, tag_flag

R = [
    Resource("ec2:instance", "i-1", "aws ec2 terminate-instances --instance-ids i-1"),
    Resource("rds:db", "d-1",
             "aws rds delete-db-instance --db-instance-identifier d-1 --skip-final-snapshot"),
]


def test_plan_lists_deletes():
    plan = teardown_plan("s", R)
    assert any("terminate-instances" in ln for ln in plan)

def test_empty_plan_message():
    assert "0 resources" in teardown_plan("s", [])[0]

def test_executor_dry_run_by_default(monkeypatch):
    monkeypatch.delenv("BUDGIE_ALLOW_TEARDOWN", raising=False)
    res = execute_teardown(R)                      # confirm defaults False
    assert [s for s, _ in res] == ["dry-run", "dry-run"]

def test_executor_needs_double_optin(monkeypatch):
    # confirm=True but env NOT set -> still dry-run, nothing deleted
    monkeypatch.delenv("BUDGIE_ALLOW_TEARDOWN", raising=False)
    res = execute_teardown(R, confirm=True)
    assert all(s == "dry-run" for s, _ in res)

def test_tag_flag():
    assert tag_flag("abc123") == "budgie:session=abc123"


def _seed_active(tmp_path, sid, rate):
    import datetime
    d = tmp_path / "sessions"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps(
        {"active_rate": rate, "accrued_cost": 0.0,
         "last_ts": datetime.datetime.now().isoformat(), "resources": {}}))


def test_teardown_credits_burn_on_real_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    monkeypatch.setenv("BUDGIE_ALLOW_TEARDOWN", "1")
    from budgie import state
    _seed_active(tmp_path, "sess", 5.0)
    res = [Resource("ec2:instance", "i-1", "true", rate=3.0),   # 'true' = harmless success
           Resource("rds:db", "d-1", "true", rate=2.0)]
    out = execute_teardown(res, confirm=True, session_id="sess")
    assert [s for s, _ in out] == ["deleted", "deleted"]
    assert state.session_total("sess") == 0.0                   # $5 - $3 - $2

def test_dry_run_credits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    monkeypatch.delenv("BUDGIE_ALLOW_TEARDOWN", raising=False)
    from budgie import state
    _seed_active(tmp_path, "sess", 5.0)
    execute_teardown([Resource("ec2:instance", "i-1", "true", rate=3.0)],
                     confirm=True, session_id="sess")           # env unset -> dry-run
    assert state.session_total("sess") == 5.0
