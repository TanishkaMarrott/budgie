"""Teardown: plan generation + executor (dry-run by default, double opt-in to run)."""
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
