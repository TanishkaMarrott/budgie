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

import contextlib
import datetime
import json
import os
from pathlib import Path

try:
    import fcntl  # POSIX advisory locks (macOS/Linux — the hook's runtime)
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from . import parse as _parse
from .estimate import estimate as _estimate
from .gate import aggregate, Decision


def _home() -> Path:
    return Path(os.environ.get("BUDGIE_HOME", ".budgie"))


def _session_file(sid: str) -> Path:
    return _home() / "sessions" / f"{sid or 'default'}.json"


_DELTA_CAP_H = 24.0  # cap one interval's accrual so a long gap can't explode it

_EMPTY = {"active_rate": 0.0, "accrued_cost": 0.0, "last_ts": None, "resources": {}}


class SessionStateError(Exception):
    """The session file exists but can't be read/parsed — the budget is untrusted."""


@contextlib.contextmanager
def _locked(sid: str):
    """Serialize the read-modify-write of one session across concurrent hook
    processes (two parallel Bash calls firing Pre/PostToolUse at once) so a
    lost update can't drop committed spend. No-ops where fcntl is unavailable."""
    f = _session_file(sid)
    f.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    lock = f.with_name(f.name + ".lock")
    fh = open(lock, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def _load(sid: str) -> dict:
    """Session state, or raise SessionStateError if the file exists but is corrupt.
    A missing file is a fresh (trustworthy-empty) session, not an error."""
    f = _session_file(sid)
    if not f.exists():
        return dict(_EMPTY, resources={})
    try:
        d = json.loads(f.read_text())
        return {
            "active_rate": float(d.get("active_rate", 0.0)),
            "accrued_cost": float(d.get("accrued_cost", 0.0)),
            "last_ts": d.get("last_ts"),
            "resources": dict(d.get("resources", {})),
        }
    except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
        raise SessionStateError(str(f)) from exc


def _read(sid: str) -> dict:
    """Tolerant read for display/accounting paths: a corrupt file reads as empty
    so `budgie session`/reconcile never crash. The *gate* checks _session_unreadable
    separately and fails CLOSED — corruption must never silently reset the budget."""
    try:
        return _load(sid)
    except SessionStateError:
        return dict(_EMPTY, resources={})


def _session_unreadable(sid: str) -> bool:
    try:
        _load(sid)
        return False
    except SessionStateError:
        return True


def _write(sid: str, s: dict) -> None:
    """Atomic write: serialise to a temp file, then os.replace (atomic on the same
    filesystem) so a crash mid-write can't leave a half-written, corrupt ledger."""
    f = _session_file(sid)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.tmp.{os.getpid()}")
    tmp.write_text(
        json.dumps(
            {
                "active_rate": round(s["active_rate"], 6),
                "accrued_cost": round(s["accrued_cost"], 6),
                "last_ts": s["last_ts"],
                "resources": s.get("resources", {}),
            }
        )
    )
    os.replace(tmp, f)


def _accrue(s: dict, now: "datetime.datetime") -> dict:
    """Fold elapsed time at the CURRENT active_rate into accrued_cost
    (accrued += active_rate × Δh, Δh capped), then advance the clock. This is
    the KML integral: cumulative dollars, not a sum of rates."""
    if s["last_ts"]:
        try:
            prev = datetime.datetime.fromisoformat(s["last_ts"])
            dh = (now - prev).total_seconds() / 3600.0
            s["accrued_cost"] += s["active_rate"] * max(0.0, min(dh, _DELTA_CAP_H))
        except ValueError:
            pass
    s["last_ts"] = now.isoformat(timespec="seconds")
    return s


def session_total(sid: str) -> float:
    """Current ACTIVE run-rate ($/hr) — what the session is burning now. This is
    what the cumulative budget cap is checked against."""
    return _read(sid)["active_rate"]


def session_accrued(sid: str) -> float:
    """Dollars accrued so far = time-integral of active_rate, brought to now."""
    return _accrue(_read(sid), datetime.datetime.now())["accrued_cost"]


def _commit(sid: str, hourly: float) -> None:
    """A new resource goes live: accrue elapsed at the old rate, then raise the
    active rate."""
    with _locked(sid):
        s = _accrue(_read(sid), datetime.datetime.now())
        s["active_rate"] += hourly
        _write(sid, s)


def release_active(sid: str, hourly: float) -> None:
    """A resource is torn down (is_deleted): accrue up to now at the old rate,
    then LOWER the active rate. Past dollars stay; future accrual drops."""
    with _locked(sid):
        s = _accrue(_read(sid), datetime.datetime.now())
        s["active_rate"] = max(0.0, s["active_rate"] - hourly)
        _write(sid, s)


def record_resource(sid: str, resource_id: str, rate: float) -> None:
    """Associate a just-created resource id with its rate (committed alongside, at
    PostToolUse). Doesn't touch active_rate — only enables later release-by-id."""
    with _locked(sid):
        s = _read(sid)
        s["resources"][resource_id] = round(float(rate), 6)
        _write(sid, s)


def release_by_id(sid: str, resource_id: str) -> float:
    """A resource with a known id was deleted: drop its rate from the active burn
    and the map. Returns the freed rate (0.0 if the id wasn't tracked)."""
    with _locked(sid):
        s = _accrue(_read(sid), datetime.datetime.now())
        rate = float(s["resources"].pop(resource_id, 0.0))
        if rate:
            s["active_rate"] = max(0.0, s["active_rate"] - rate)
        _write(sid, s)
    return rate


def reset_session(sid: str) -> None:
    _session_file(sid).unlink(missing_ok=True)


def _env_float(name: str, default: float | None = None) -> float | None:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _budget_decision(
    sid: str, estimates: list, prior: float, cmd_hourly: float, budget: float, base: Decision
) -> Decision:
    """Cumulative TOTAL-dollar gate (enforced only when BUDGIE_BUDGET is set).
    The net sum of what's been performed = dollars already spent + everything
    still running projected over a horizon. Blocks when that would cross the
    session's total budget — so create/tear-down churn and slow accrual are both
    counted, not just the instantaneous rate."""
    horizon = _env_float("BUDGIE_HORIZON", 1.0) or 1.0
    accrued = session_accrued(sid)  # dollars already spent, to now
    projected = accrued + (prior + cmd_hourly) * horizon
    known = [e for e in estimates if e.total_hourly is not None]
    top = max(known, key=lambda e: e.total_hourly).resource if known else "this command"
    hz = f"{horizon:g}h"
    if projected > budget:
        return Decision(
            "block",
            f"{top} would put the session at ~${projected:.2f} projected over {hz} "
            f"(${accrued:.2f} spent + ${prior + cmd_hourly:.2f}/hr running) — over the "
            f"${budget:.2f} session budget. Blocked.",
        )
    if projected > 0.8 * budget and base.verdict == "allow":
        return Decision(
            "warn",
            f"{top} brings the session to ~${projected:.2f} over {hz} — approaching "
            f"the ${budget:.2f} session budget (${accrued:.2f} spent).",
        )
    return base


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
    """Stateful gate (PreToolUse): cumulative budget + override + ledger.

    DECIDES only — it does NOT commit the cost. Anticipation (a prediction about a
    resource that doesn't exist yet) must never pollute the factual session ledger:
    a command can be blocked, fail, or be a dry-run. The cost is committed later, at
    PostToolUse (`reconcile`), which fires ONLY on success — so failed creates never
    count. (Claude Code fires no hook at all when a command fails, so there is no
    way to roll a phantom commit back; the fix is to never write it.)"""
    intents = _parse.extract(command)
    estimates = [_estimate(i) for i in intents]
    unbounded = any(i.unbounded for i in intents)
    cmd_hourly = sum(e.total_hourly for e in estimates if e.total_hourly is not None)
    prior = session_total(session_id)

    override = bool(intents and (os.environ.get("BUDGIE_OK") or _allowlisted(command)))
    fail_open = os.environ.get("BUDGIE_FAIL", "closed").lower() == "open"

    if intents and not override and not fail_open and _session_unreadable(session_id):
        # Corrupt/unreadable budget state: block spend rather than silently treating
        # the session as $0 spent (which would fail OPEN). Override / BUDGIE_FAIL=open
        # still let it through.
        decision = Decision(
            "block",
            "session budget state is unreadable/corrupt — blocked to be safe; an "
            "untrusted ledger must not reset the budget to $0. Remove the session "
            "file or set BUDGIE_FAIL=open to override.",
        )
    elif override:
        decision = Decision(
            "allow",
            f"override — allowing ${cmd_hourly:.2f}/hr (session would reach " f"${prior + cmd_hourly:.2f}/hr).",
        )
    elif intents:
        decision = aggregate(estimates, cap_hourly, committed=prior, unbounded=unbounded)
        budget = _env_float("BUDGIE_BUDGET")  # cumulative total-$ gate (opt-in)
        if budget is not None and cmd_hourly > 0 and decision.verdict != "block":
            decision = _budget_decision(session_id, estimates, prior, cmd_hourly, budget, decision)
    else:
        from . import check

        decision = check(command, cap_hourly)

    _ledger(
        {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "session": session_id or "default",
            "verdict": decision.verdict,
            "command_hourly": round(cmd_hourly, 4),
            "session_hourly_before": round(prior, 4),
            "command": command[:200],
            "reason": decision.reason,
        }
    )

    # NB: no commit here — see the docstring. Committing happens at PostToolUse.
    return decision
