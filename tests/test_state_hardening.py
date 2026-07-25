"""Session-state durability: corruption fails CLOSED, writes are atomic, and
concurrent commits don't lose updates. These guard the budget itself — a firewall
whose ledger silently resets to $0 (or loses a commit to a race) under-enforces."""

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import state  # noqa: E402


def _sid_file(tmp_path, sid="s"):
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}.json"


# ---- corruption fails CLOSED, not silent-zero ---------------------------------


def test_corrupt_session_blocks_spend(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    _sid_file(tmp_path).write_text("{ this is not json")
    d = state.evaluate("aws ec2 run-instances --instance-type t3.micro", 2.0, "s")
    assert d.verdict == "block"
    assert "unreadable" in d.reason or "corrupt" in d.reason


def test_corrupt_session_respects_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    monkeypatch.setenv("BUDGIE_FAIL", "open")
    _sid_file(tmp_path).write_text("{ not json")
    d = state.evaluate("aws ec2 run-instances --instance-type t3.micro", 2.0, "s")
    assert d.verdict != "block"  # explicit override lets it through


def test_corrupt_session_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    monkeypatch.setenv("BUDGIE_OK", "1")
    _sid_file(tmp_path).write_text("garbage")
    d = state.evaluate("aws ec2 run-instances --instance-type p5.48xlarge", 2.0, "s")
    assert d.verdict == "allow"


def test_corrupt_session_display_is_tolerant(monkeypatch, tmp_path):
    # `budgie session` and reconcile must not crash on a corrupt file.
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    _sid_file(tmp_path).write_text("{{{")
    assert state.session_total("s") == 0.0
    assert state.session_accrued("s") == 0.0


def test_missing_session_is_not_corrupt(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    assert state._session_unreadable("brand-new") is False  # fresh = trustworthy-empty


# ---- atomic writes ------------------------------------------------------------


def test_write_is_atomic_no_temp_left(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    state._commit("s", 1.23)
    files = list((tmp_path / "sessions").iterdir())
    assert not any(".tmp" in f.name for f in files), f"temp file leaked: {files}"
    assert abs(state.session_total("s") - 1.23) < 1e-6


# ---- concurrency: the lock prevents lost updates ------------------------------


def test_concurrent_commits_do_not_lose_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    n, rate = 25, 0.10
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()  # maximise contention on the read-modify-write
        state._commit("race", rate)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert abs(state.session_total("race") - n * rate) < 1e-6, "a commit was lost to a race"
