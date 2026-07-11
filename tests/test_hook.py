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


def test_block_exits_2_with_stderr(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws ec2 run-instances --instance-type p5.48xlarge"),
                   monkeypatch, capsys, tmp_path)
    assert rc == 2                          # exit 2 => Claude Code blocks it
    assert "budgie" in out.err.lower()
    assert out.out.strip() == ""            # nothing on stdout for a block

def test_allow_exits_0_silent(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws s3 ls"), monkeypatch, capsys, tmp_path)
    assert rc == 0 and out.out.strip() == ""

def test_warn_exits_0_with_context(monkeypatch, capsys, tmp_path):
    rc, out = _run(_cmd("aws ec2 run-instances --instance-type c5.9xlarge"),
                   monkeypatch, capsys, tmp_path)
    assert rc == 0
    assert "additionalContext" in out.out

def test_cumulative_block_exits_2(monkeypatch, capsys, tmp_path):
    # two under-cap boxes in the same session -> the 2nd trips the cap -> exit 2
    _run(_cmd("aws ec2 run-instances --instance-type c5.9xlarge", "same"),
         monkeypatch, capsys, tmp_path)
    rc, _ = _run(_cmd("aws ec2 run-instances --instance-type c5.9xlarge", "same"),
                 monkeypatch, capsys, tmp_path)
    assert rc == 2

def test_fail_closed_exits_2(monkeypatch, capsys, tmp_path):
    # bad cap forces an internal error; a spend-looking command must fail CLOSED
    rc, out = _run(_cmd("aws ec2 run-instances --instance-type p5.48xlarge"),
                   monkeypatch, capsys, tmp_path, env={"BUDGIE_HOURLY": "notafloat"})
    assert rc == 2 and "budgie" in out.err.lower()
