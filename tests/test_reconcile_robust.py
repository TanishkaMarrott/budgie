"""Reconciliation robustness: failed commands commit nothing, resizes don't
double-count, and teardown crediting survives --output text (no parseable id)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import state  # noqa: E402
from budgie.reconcile import reconcile, _looks_failed  # noqa: E402

RDS_CREATE = "aws rds create-db-instance --db-instance-identifier mydb --db-instance-class db.r5.large --engine mysql"
RDS_RATE = 0.24  # db.r5.large, no attached storage


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))


# ---- failed commands never commit (defensive no-phantom-spend) ----------------


def test_failed_flag_commits_nothing(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    reconcile(RDS_CREATE, "", "s", failed=True)
    assert state.session_total("s") == 0.0


def test_botocore_error_detected():
    err = "An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation: ..."
    assert _looks_failed(err) is True
    assert _looks_failed('{"DBInstance": {"DBInstanceIdentifier": "mydb"}}') is False


# ---- resize (modify-*) must not double-count ----------------------------------


def test_modify_does_not_double_commit(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    reconcile(RDS_CREATE, '{"DBInstance": {"DBInstanceIdentifier": "mydb"}}', "s")
    assert abs(state.session_total("s") - RDS_RATE) < 1e-6
    # resize to a bigger class — the gate prices it, but the ledger must NOT add a
    # second full instance rate on top of the original create.
    reconcile("aws rds modify-db-instance --db-instance-identifier mydb --db-instance-class db.r5.4xlarge", "", "s")
    assert abs(state.session_total("s") - RDS_RATE) < 1e-6, "resize double-committed"


# ---- teardown credit survives --output text (no id in output) -----------------


def test_credit_by_command_identifier_under_output_text(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    # create emitted NO parseable id (e.g. --output text) — but we recorded the
    # user-assigned --db-instance-identifier, so the delete still credits it back.
    r = reconcile(RDS_CREATE, "", "s")
    assert "mydb" in r["ids"]
    assert abs(state.session_total("s") - RDS_RATE) < 1e-6
    reconcile("aws rds delete-db-instance --db-instance-identifier mydb --skip-final-snapshot", "", "s")
    assert state.session_total("s") == 0.0, "delete was not credited under --output text"


def test_global_flag_delete_is_credited(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    reconcile("aws ec2 run-instances --instance-type m5.large", '{"Instances":[{"InstanceId":"i-abc123"}]}', "s")
    assert state.session_total("s") > 0
    # delete behind a leading global flag — must still parse + credit.
    reconcile("aws --region us-east-1 ec2 terminate-instances --instance-ids i-abc123", "", "s")
    assert state.session_total("s") == 0.0
