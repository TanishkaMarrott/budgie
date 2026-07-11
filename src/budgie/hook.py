"""Claude Code `PreToolUse` hook — block BEFORE the spend.

settings.json:
  { "hooks": { "PreToolUse": [
      { "matcher": "Bash", "hooks": [
          { "type": "command", "command": "budgie hook", "timeout": 5 } ] } ] } }

Block mechanism: for a hard block we **exit 2** (Claude Code blocks the tool call
and shows stderr to the agent) — the most robust, version-proof deny for a spend
firewall. A non-blocking warning uses permissionDecision "allow" + additionalContext
(exit 0). Plain allow: no output, exit 0.
A guard must never crash silently: on internal error we FAIL CLOSED (exit 2) for
anything that looks like a spend command (override with BUDGIE_FAIL=open).
"""
from __future__ import annotations

import json
import os
import sys

_SPEND_MARKERS = ("aws ", "terraform ", "pulumi ", "gcloud ", "cloudformation", " az ")


def _emit(**fields) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **fields}}))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    command = (payload.get("tool_input") or {}).get("command", "")
    session_id = payload.get("session_id", "")
    fail_open = os.environ.get("BUDGIE_FAIL", "closed").lower() == "open"

    try:
        cap = float(os.environ.get("BUDGIE_HOURLY", "2.0"))
        from .state import evaluate
        decision = evaluate(command, cap, session_id)
    except Exception as exc:                         # never crash-open silently
        looks_spendy = any(m in f" {command.lower()} " for m in _SPEND_MARKERS)
        if looks_spendy and not fail_open:
            print(f"budgie: couldn't price this command ({exc.__class__.__name__}); "
                  "blocked to be safe. Set BUDGIE_FAIL=open to allow through.",
                  file=sys.stderr)
            return 2                                  # fail CLOSED
        return 0

    if decision.verdict == "block":
        print(f"budgie 🐦 {decision.reason}", file=sys.stderr)
        return 2                                      # exit 2 => Claude Code blocks it
    if decision.verdict == "warn":
        _emit(permissionDecision="allow",
              additionalContext=f"budgie 🐦 ⚠ {decision.reason}")
    # allow -> emit nothing (implicit allow)
    return 0


def posthook_main() -> int:
    """PostToolUse hook — reconciles the session burn after a command runs
    (records created resource ids, credits deletes/failed creates). Always
    exits 0; reconciliation is best-effort and never disrupts the agent."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    session_id = payload.get("session_id", "")
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        output = resp.get("stdout") or resp.get("output") or ""
        success = not (resp.get("error") or resp.get("interrupted")
                       or (isinstance(resp.get("exit_code"), int) and resp["exit_code"] != 0)
                       or (isinstance(resp.get("returncode"), int) and resp["returncode"] != 0))
    else:
        output, success = str(resp or ""), True
    try:
        from .reconcile import reconcile
        reconcile(command, output, success, session_id)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
