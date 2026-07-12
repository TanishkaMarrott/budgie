"""The PreToolUse hook contract: block => exit 2 + stderr; warn => allow+context;
allow => silent exit 0; fail-closed on internal error for spend commands."""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _run(payload, monkeypatch, capsys, tmp_path, env=None):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    from budgie.hook import main

    rc = main()
    return rc, capsys.readouterr()


def _cmd(c, sid="s"):
    return {"session_id": sid, "tool_input": {"command": c}}


def _runpost(command, sid, monkeypatch, tmp_path, stdout=""):
    """Simulate the PostToolUse hook firing (success) — commits the cost."""
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    payload = {
        "session_id": sid,
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    from budgie.hook import posthook_main

    return posthook_main()


def test_block_exits_2_with_stderr(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws ec2 run-instances --instance-type p5.48xlarge"), monkeypatch, capsys, tmp_path)
    assert rc == 2  # exit 2 => Claude Code blocks it
    assert "budgie" in out.err.lower()
    assert out.out.strip() == ""  # nothing on stdout for a block


def test_allow_exits_0_silent(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws s3 ls"), monkeypatch, capsys, tmp_path)
    assert rc == 0 and out.out.strip() == ""


def test_warn_exits_0_with_context(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws ec2 run-instances --instance-type c5.9xlarge"), monkeypatch, capsys, tmp_path)
    assert rc == 0
    assert "additionalContext" in out.out


def test_cumulative_block_exits_2(monkeypatch, capsys, tmp_path):
    # two under-cap boxes, same session. The FIRST succeeds (PostToolUse commits
    # it), so the SECOND trips the cumulative cap -> exit 2. This is the real flow:
    # PreToolUse decides, PostToolUse commits.
    c = "aws ec2 run-instances --instance-type c5.9xlarge"
    _run(_cmd(c, "same"), monkeypatch, capsys, tmp_path)  # PreToolUse #1 -> warn
    _runpost(c, "same", monkeypatch, tmp_path)  # PostToolUse -> commit 1.53
    rc, _ = _run(_cmd(c, "same"), monkeypatch, capsys, tmp_path)  # PreToolUse #2 -> block
    assert rc == 2


def test_failed_create_leaves_no_phantom(monkeypatch, capsys, tmp_path):
    # PreToolUse allows a create; the command then FAILS so no PostToolUse fires
    # (we simply never call _runpost). A later cheap command must NOT be blocked by
    # phantom committed spend — the whole point of committing only at PostToolUse.
    c = "aws ec2 run-instances --instance-type c5.9xlarge"
    _run(_cmd(c, "sess"), monkeypatch, capsys, tmp_path)  # allowed, but "fails" (no posthook)
    from budgie import state

    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    assert state.session_total("sess") == 0.0  # no phantom commit


def test_fail_closed_exits_2(monkeypatch, capsys, tmp_path):
    # bad cap forces an internal error; a spend-looking command must fail CLOSED
    rc, out = _run(
        _cmd("aws ec2 run-instances --instance-type p5.48xlarge"),
        monkeypatch,
        capsys,
        tmp_path,
        env={"BUDGIE_HOURLY": "notafloat"},
    )
    assert rc == 2 and "budgie" in out.err.lower()
