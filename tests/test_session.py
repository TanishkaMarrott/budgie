"""Session budget + override + ledger — the stateful layer."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    monkeypatch.delenv("BUDGIE_OK", raising=False)
    from budgie import state
    return state


C59 = "aws ec2 run-instances --instance-type c5.9xlarge"      # $1.53/hr
P5 = "aws ec2 run-instances --instance-type p5.48xlarge"      # $98.32/hr


def test_cumulative_budget_blocks(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    d1 = st.evaluate(C59, 2.0, "s1")            # 1.53 alone: warn (>cap/2), committed
    assert d1.verdict in ("allow", "warn")
    d2 = st.evaluate(C59, 2.0, "s1")            # cumulative 3.06 > 2 -> block
    assert d2.verdict == "block"


def test_block_is_not_committed(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    st.evaluate(C59, 2.0, "s2")                 # commits 1.53
    st.evaluate(P5, 2.0, "s2")                  # blocked -> must NOT commit
    assert abs(st.session_total("s2") - 1.53) < 0.01


def test_sessions_are_isolated(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    st.evaluate(C59, 2.0, "a")
    assert st.session_total("b") == 0.0


def test_override_env_allows(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    monkeypatch.setenv("BUDGIE_OK", "1")
    assert st.evaluate(P5, 2.0, "s3").verdict == "allow"


def test_allowlist_file_allows(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    (tmp_path / "allow.txt").write_text("# intended\np5.48xlarge\n")
    assert st.evaluate(P5, 2.0, "s4").verdict == "allow"


def test_ledger_written(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    st.evaluate(P5, 2.0, "s5")
    rows = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert rows[-1]["verdict"] == "block" and rows[-1]["session"] == "s5"


# --- corrected cumulative model: rate vs accrued, active/deleted, time integral ---
import datetime  # noqa: E402


def _seed(tmp_path, sid, active_rate, hours_ago, accrued=0.0):
    d = tmp_path / "sessions"; d.mkdir(parents=True, exist_ok=True)
    ts = (datetime.datetime.now() - datetime.timedelta(hours=hours_ago)).isoformat()
    (d / f"{sid}.json").write_text(json.dumps(
        {"active_rate": active_rate, "accrued_cost": accrued, "last_ts": ts}))


def test_accrued_is_rate_times_time(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    _seed(tmp_path, "acc", active_rate=2.0, hours_ago=3.0)   # $2/hr for 3h ≈ $6
    assert 5.9 < st.session_accrued("acc") < 6.1

def test_release_lowers_active_rate_but_keeps_accrued(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    _seed(tmp_path, "r", active_rate=3.0, hours_ago=1.0)     # accrue ~$3, still burning $3/hr
    st.release_active("r", 3.0)                              # tear it down
    assert st.session_total("r") == 0.0                     # burn drops to 0
    assert st.session_accrued("r") >= 2.9                   # past dollars remain

def test_delta_is_capped(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch)
    _seed(tmp_path, "c", active_rate=1.0, hours_ago=100.0)   # 100h gap, capped at 24
    assert abs(st.session_accrued("c") - 24.0) < 0.1
