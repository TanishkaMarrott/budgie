"""PostToolUse reconciliation: record create ids, credit deletes, roll back failed creates."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _mods(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    from budgie import state, reconcile
    return state, reconcile


def _seed(tmp_path, sid, active, resources=None):
    import datetime
    d = tmp_path / "sessions"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps(
        {"active_rate": active, "accrued_cost": 0.0,
         "last_ts": datetime.datetime.now().isoformat(), "resources": resources or {}}))


def test_create_success_records_id(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 98.32)                                  # PreToolUse already committed
    out = '{"Instances":[{"InstanceId":"i-0abc123456"}]}'
    r = rec.reconcile("aws ec2 run-instances --instance-type p5.48xlarge", out, True, "s")
    assert r["action"] == "create-recorded" and "i-0abc123456" in r["ids"]
    assert state.session_total("s") == 98.32                     # burn unchanged, id now mapped

def test_delete_credits_the_burn(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 98.32, {"i-0abc123456": 98.32})
    r = rec.reconcile("aws ec2 terminate-instances --instance-ids i-0abc123456", "", True, "s")
    assert r["action"] == "delete-credited"
    assert state.session_total("s") == 0.0                       # burn released

def test_create_then_delete_roundtrip(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 0.192)
    rec.reconcile("aws ec2 run-instances --instance-type m5.xlarge",
                  '{"Instances":[{"InstanceId":"i-xyz1234567"}]}', True, "s")
    assert state.session_total("s") == 0.192
    rec.reconcile("aws ec2 terminate-instances --instance-ids i-xyz1234567", "", True, "s")
    assert state.session_total("s") == 0.0

def test_failed_create_rolls_back(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 98.32)
    r = rec.reconcile("aws ec2 run-instances --instance-type p5.48xlarge",
                      "An error occurred (Unauthorized)", False, "s")
    assert r["action"] == "create-failed-release"
    assert state.session_total("s") == 0.0                       # committed rate rolled back

def test_untracked_delete_is_noop(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 5.0, {})
    rec.reconcile("aws ec2 terminate-instances --instance-ids i-nottracked9", "", True, "s")
    assert state.session_total("s") == 5.0                       # id unknown -> unchanged
