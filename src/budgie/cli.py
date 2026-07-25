"""budgie CLI:  check "<cmd>" | hook | ledger | version"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import check, __version__


def _default_cap() -> float:
    """The cap the enforcing hook uses — so `check`/`tf-plan` agree with `hook`."""
    try:
        return float(os.environ.get("BUDGIE_HOURLY", "2.0"))
    except ValueError:
        return 2.0


def _session(_args) -> int:
    from .state import _home, session_total, session_accrued

    d = _home() / "sessions"
    files = sorted(d.glob("*.json")) if d.exists() else []
    if not files:
        print("no sessions tracked yet")
        return 0
    for f in files:
        rate, accrued = session_total(f.stem), session_accrued(f.stem)
        print(f"  {f.stem}: burning ${rate:.2f}/hr  ·  accrued ${accrued:.2f} so far")
    return 0


def _ledger(_args) -> int:
    from .state import _home

    f = _home() / "ledger.jsonl"
    if not f.exists():
        print("no budgie ledger yet")
        return 0
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    blocked = sum(1 for r in rows if r["verdict"] == "block")
    saved = sum(r.get("command_hourly", 0) for r in rows if r["verdict"] == "block")
    for r in rows[-20:]:
        print(f"  {r['ts']}  [{r['verdict']:5}]  ${r.get('command_hourly',0):.2f}/hr  " f"{r['command'][:70]}")
    print(
        f"\n{len(rows)} decisions · {blocked} blocked · " f"~${saved:.2f}/hr (${saved*730:,.0f}/mo) of spend stopped"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="budgie")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="price + gate one command")
    c.add_argument("command")
    c.add_argument("--cap", type=float, default=None, help="hourly $ cap (default: $BUDGIE_HOURLY or 2.0)")
    sub.add_parser("hook", help="PreToolUse hook entry (reads JSON on stdin)")
    sub.add_parser("posthook", help="PostToolUse hook entry (reconciles the session burn)")
    sub.add_parser("ledger", help="show recent decisions + spend stopped")
    sub.add_parser("session", help="show cumulative $/hr committed per session")
    t = sub.add_parser("tf-plan", help="price a `terraform show -json` plan file")
    t.add_argument("file")
    t.add_argument("--cap", type=float, default=None)
    w = sub.add_parser("wrap", help="launch a coding agent guarded by budgie (e.g. budgie wrap claude)")
    w.add_argument("agent_cmd", nargs=argparse.REMAINDER)
    sub.add_parser("version")
    args = p.parse_args(argv)

    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "wrap":
        from .wrap import wrap_main

        return wrap_main(args.agent_cmd)
    if args.cmd == "hook":
        from .hook import main as hook_main

        return hook_main()
    if args.cmd == "posthook":
        from .hook import posthook_main

        return posthook_main()
    if args.cmd == "ledger":
        return _ledger(args)
    if args.cmd == "session":
        return _session(args)
    cap = args.cap if args.cap is not None else _default_cap()
    if args.cmd == "tf-plan":
        from pathlib import Path
        from .terraform import price_plan

        d = price_plan(Path(args.file).read_text(), cap)
        print(f"[{d.verdict.upper()}] {d.reason}")
        return 1 if d.verdict == "block" else 0

    d = check(args.command, cap)
    tag = {"block": "BLOCK", "warn": "WARN", "allow": "ALLOW"}[d.verdict]
    print(f"[{tag}] {d.reason}")
    return 1 if d.verdict == "block" else 0


if __name__ == "__main__":
    raise SystemExit(main())
