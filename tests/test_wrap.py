"""`budgie wrap` — injects budgie's hooks into an agent launch (via --settings)
without mutating project settings, and guards its inputs."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import wrap  # noqa: E402


def test_no_agent_is_usage_error():
    assert wrap.wrap_main([]) == 2


def test_unknown_agent_rejected():
    assert wrap.wrap_main(["kubectl"]) == 2


def test_injected_hooks_wire_both_pre_and_post():
    h = wrap._HOOKS["hooks"]
    assert h["PreToolUse"][0]["hooks"][0]["command"] == "budgie hook"
    assert h["PostToolUse"][0]["hooks"][0]["command"] == "budgie posthook"
    assert h["PreToolUse"][0]["matcher"] == "Bash"
    # must be valid JSON to pass to `claude --settings`
    json.loads(json.dumps(wrap._HOOKS))


def test_claude_is_supported():
    assert "claude" in wrap._SUPPORTED
