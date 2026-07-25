"""`budgie wrap <agent>` — launch a coding agent already guarded by budgie.

The proxy-wrapper ergonomics (`headroom wrap claude`) but for a hook-based guard:
it injects budgie's PreToolUse + PostToolUse hooks through the agent's own
`--settings` channel — no mutation of your project's settings files — then execs
the agent. Supported today: Claude Code (`budgie wrap claude`).
"""
from __future__ import annotations

import json
import os
import shutil
import sys

_HOOKS = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "budgie hook"}]}
        ],
        "PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "budgie posthook"}]}
        ],
    }
}

_SUPPORTED = {"claude", "claude-code"}


def _banner(cap: str) -> None:
    print("  ┌──────────────────────────┐")
    print("  │   BUDGIE WRAP: CLAUDE \U0001f426   │")
    print("  └──────────────────────────┘")
    print(f"  gate: budgie PreToolUse hook · cap ${cap}/hr")
    print("  over-budget aws / terraform is blocked before it runs.\n")


def wrap_main(agent_cmd: list[str]) -> int:
    """Exec `agent_cmd` (e.g. ["claude", ...]) with budgie's hooks injected."""
    if not agent_cmd:
        print("usage: budgie wrap <agent> [args...]   e.g. budgie wrap claude", file=sys.stderr)
        return 2
    agent = agent_cmd[0]
    if agent not in _SUPPORTED:
        print(f"budgie wrap supports: {', '.join(sorted(_SUPPORTED))}. Got: {agent}", file=sys.stderr)
        return 2
    cap = os.environ.setdefault("BUDGIE_HOURLY", "2.0")
    exe = shutil.which(agent) or agent
    _banner(cap)
    argv = [exe, "--settings", json.dumps(_HOOKS), *agent_cmd[1:]]
    try:
        os.execvp(exe, argv)  # replace this process with the guarded agent
    except OSError as exc:
        print(f"budgie wrap: could not launch {agent}: {exc}", file=sys.stderr)
        return 1
