"""Session budget + provenance ledger + override — the stateful layer.

`check()` stays a pure per-command function (easy to test). `evaluate()` wraps
it with the things a real guard needs across a whole agent run:

  • cumulative SESSION budget — the cap is checked against everything committed
    so far this session, not just the one command;
  • a decision LEDGER (.budgie/ledger.jsonl) — the audit trail / savings metric;
  • an OVERRIDE — `BUDGIE_OK=1`, or a `.budgie/allow.txt` substring allowlist,
    so an intended expensive command isn't a dead end.

State lives under $BUDGIE_HOME (default `.budgie`).
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from . import parse as _parse
from .estimate import estimate as _estimate
from .gate import aggregate, Decision


def _home() -> Path:
    return Path(os.environ.get("BUDGIE_HOME", ".budgie"))


def _session_file(sid: str) -> Path:
    return _home() / "sessions" / f"{sid or 'default'}.json"


def session_total(sid: str) -> float:
    f = _session_file(sid)
    if f.exists():
        try:
            return float(json.loads(f.read_text()).get("committed_hourly", 0.0))
        except (json.JSONDecodeError, ValueError, OSError):
            return 0.0
    return 0.0


def _commit(sid: str, hourly: float) -> None:
    f = _session_file(sid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"committed_hourly": round(session_total(sid) + hourly, 6)}))


def reset_session(sid: str) -> None:
    _session_file(sid).unlink(missing_ok=True)


def _allowlisted(command: str) -> bool:
    f = _home() / "allow.txt"
    if not f.exists():
        return False
    try:
        for line in f.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and s in command:
                return True
    except OSError:
        pass
    return False


def _ledger(entry: dict) -> None:
    d = _home()
    d.mkdir(parents=True, exist_ok=True)
    with (d / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def evaluate(command: str, cap_hourly: float, session_id: str = "") -> Decision:
    """Stateful gate: cumulative budget + override + ledger. Commits the cost of
    anything not blocked, so the session running-total reflects what will run."""
    intents = _parse.extract(command)
    estimates = [_estimate(i) for i in intents]
    in_loop = any(i.in_loop for i in intents)
    cmd_hourly = sum(e.total_hourly for e in estimates if e.total_hourly is not None)
    prior = session_total(session_id)

    if intents and (os.environ.get("BUDGIE_OK") or _allowlisted(command)):
        decision = Decision("allow",
            f"override — committing ${cmd_hourly:.2f}/hr (session "
            f"${prior + cmd_hourly:.2f}/hr).")
    elif intents:
        decision = aggregate(estimates, cap_hourly, in_loop, committed=prior)
    else:
        from . import check
        decision = check(command, cap_hourly)

    _ledger({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "session": session_id or "default",
        "verdict": decision.verdict,
        "command_hourly": round(cmd_hourly, 4),
        "session_hourly_before": round(prior, 4),
        "command": command[:200],
        "reason": decision.reason,
    })

    if decision.verdict != "block" and cmd_hourly > 0:
        _commit(session_id, cmd_hourly)
    return decision
