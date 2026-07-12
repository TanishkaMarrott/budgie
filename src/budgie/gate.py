"""The budget decision. Deterministic policy over the estimates.

BLOCK : total known cost over the hourly cap
WARN  : unknown/hidden cost, OR spend inside a loop, OR over half the cap
ALLOW : total known cost under half the cap
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pricing
from .estimate import Estimate


@dataclass
class Decision:
    verdict: str  # "block" | "warn" | "allow"
    reason: str


def aggregate(
    estimates: list[Estimate],
    cap_hourly: float,
    in_loop: bool = False,
    committed: float = 0.0,
    unbounded: bool = False,
) -> Decision:
    """Combine all spend invocations in one command into a single verdict.
    Multiple creates in a chain are summed; `committed` is the run-rate already
    committed this session, so the cap is checked CUMULATIVELY, not per-command."""
    if not estimates:
        return Decision("allow", "no spend command")

    known = [e for e in estimates if e.total_hourly is not None]
    unknown = [e for e in estimates if e.hourly is None]
    cmd_total = sum(e.total_hourly for e in known)
    total = committed + cmd_total  # cumulative session run-rate
    monthly = total * pricing.HOURS_PER_MONTH
    notes = sorted({e.note for e in estimates if e.note})
    tail = ("  [" + "; ".join(notes) + "]") if notes else ""
    sess = f" (session would reach ${total:.2f}/hr)" if committed > 0 else ""
    top = max(known, key=lambda e: e.total_hourly).resource if known else ""

    # An unbounded loop (while/until/xargs/dynamic range) that creates billable
    # resources has no fixed count — total cost can't be bounded, so refuse it.
    if unbounded and (cmd_total > 0 or unknown):
        what = top or (unknown[0].resource if unknown else "resources")
        each = f" (≈${cmd_total:.2f}/hr each)" if cmd_total > 0 else ""
        return Decision(
            "block",
            f"unbounded loop creates {what}{each} with no fixed iteration count — "
            f"cost can't be bounded. Blocked.{tail}",
        )

    if total > cap_hourly:
        what = top if len(known) == 1 else f"{len(known)} resources incl. {top}"
        cmd_monthly = cmd_total * pricing.HOURS_PER_MONTH
        return Decision(
            "block",
            f"{what} ≈ ${cmd_total:.2f}/hr (${cmd_monthly:,.0f}/mo) — over the "
            f"${cap_hourly:.2f}/hr session cap{sess}. Blocked.{tail}",
        )

    if unknown:
        u = unknown[0]
        return Decision(
            "warn",
            f"unknown price for {u.resource} ({u.note or 'unrecognized'}) — can't "
            f"verify it stays under ${cap_hourly:.2f}/hr. Review before running.{tail}",
        )

    # (a bounded loop's full cost is already priced into qty; an unbounded loop is
    # handled above — so `in_loop` no longer needs a blanket warn here.)
    if total > cap_hourly / 2:
        return Decision(
            "warn",
            f"{top} ≈ ${cmd_total:.2f}/hr (${monthly:,.0f}/mo{sess}) — approaching "
            f"the ${cap_hourly:.2f}/hr cap.{tail}",
        )

    return Decision("allow", f"≈ ${cmd_total:.2f}/hr — within budget.{tail}")
