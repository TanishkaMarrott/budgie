"""PostToolUse reconciliation: a succeeded create COMMITS its cost (PreToolUse
commits nothing), a delete credits it back, an untracked delete is a no-op.
PostToolUse only fires on success, so reconcile is never called for a failure."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _mods(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    from budgie import state, reconcile

    return state, reconcile


def _seed(tmp_path, sid, active, resources=None):
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(
        json.dumps(
            {
                "active_rate": active,
                "accrued_cost": 0.0,
                "last_ts": datetime.datetime.now().isoformat(),
                "resources": resources or {},
            }
        )
    )


def test_create_commits_and_records(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 0.0)  # PreToolUse committed nothing
    out = '{"Instances":[{"InstanceId":"i-0abc123456"}]}'
    r = rec.reconcile("aws ec2 run-instances --instance-type p5.48xlarge", out, "s")
    assert r["action"] == "create-committed" and "i-0abc123456" in r["ids"]
    assert state.session_total("s") == 98.32  # committed here, on success


def test_delete_credits_the_burn(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 98.32, {"i-0abc123456": 98.32})
    r = rec.reconcile("aws ec2 terminate-instances --instance-ids i-0abc123456", "", "s")
    assert r["action"] == "delete-credited"
    assert state.session_total("s") == 0.0  # burn released


def test_create_then_delete_roundtrip(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 0.0)
    rec.reconcile(
        "aws ec2 run-instances --instance-type m5.xlarge", '{"Instances":[{"InstanceId":"i-xyz1234567"}]}', "s"
    )
    assert state.session_total("s") == 0.192  # committed on create
    rec.reconcile("aws ec2 terminate-instances --instance-ids i-xyz1234567", "", "s")
    assert state.session_total("s") == 0.0  # credited on delete


def test_untracked_delete_is_noop(tmp_path, monkeypatch):
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 5.0, {})
    rec.reconcile("aws ec2 terminate-instances --instance-ids i-nottracked9", "", "s")
    assert state.session_total("s") == 5.0  # id unknown -> unchanged


def test_create_with_no_id_still_commits(tmp_path, monkeypatch):
    # output has no parseable id — the burn is still real and must be committed
    # (we just can't map it for later per-id crediting).
    state, rec = _mods(tmp_path, monkeypatch)
    _seed(tmp_path, "s", 0.0)
    r = rec.reconcile("aws ec2 run-instances --instance-type m5.xlarge", "", "s")
    assert r["action"] == "create-committed"
    assert state.session_total("s") == 0.192
