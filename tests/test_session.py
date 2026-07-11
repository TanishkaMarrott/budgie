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
