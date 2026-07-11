"""Move #2: session provenance + teardown.

The story: every resource an agent creates in a session is tagged
`budgie:session=<id>`; when the session ends, budgie can tear down exactly
what that session created — the "$X spent, 0 orphans" guarantee.

SAFETY: this module never calls AWS. `teardown_plan()` returns the delete
commands as *strings* for review / explicit opt-in execution. Producing a plan
is inert; running it is a separate, deliberate step (the guarded executor is a
later, opt-in piece — mirrors budgie's read-only-by-default design).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass

TAG_KEY = "budgie:session"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def tag_flag(session_id: str) -> str:
    """The tag an agent's create command should carry for provenance.
    (Injection/collection per service is the integration point — EC2 uses
    --tag-specifications, most others --tags.)"""
    return f"{TAG_KEY}={session_id}"


@dataclass
class Resource:
    kind: str      # e.g. "ec2:instance", "rds:db"
    id: str        # arn / physical id
    delete_cmd: str


def teardown_plan(session_id: str, resources: list[Resource]) -> list[str]:
    """Given resources tagged with this session (sourced in the real product
    from the Resource Groups Tagging API / CloudTrail create-events — exactly
    the orphan-hunter reconciliation), return the ordered delete commands.

    Returns STRINGS only. Nothing is executed here.
    """
    if not resources:
        return [f"# budgie: session {session_id} created 0 resources — nothing to tear down"]
    lines = [f"# budgie teardown plan for session {session_id} "
             f"({len(resources)} resource(s)) — review before running:"]
    lines += [r.delete_cmd for r in resources]
    return lines


def execute_teardown(resources: list[Resource], confirm: bool = False) -> list[tuple[str, str]]:
    """Tear down the session's resources. DRY-RUN by default: returns
    [("dry-run", cmd), ...] and runs nothing. Actual deletion happens ONLY when
    confirm=True AND env BUDGIE_ALLOW_TEARDOWN=1 — double opt-in, because
    deleting real infrastructure is irreversible.
    """
    live = confirm and os.environ.get("BUDGIE_ALLOW_TEARDOWN") == "1"
    results: list[tuple[str, str]] = []
    for r in resources:
        if not live:
            results.append(("dry-run", r.delete_cmd))
            continue
        proc = subprocess.run(shlex.split(r.delete_cmd), capture_output=True, text=True)
        results.append(("deleted" if proc.returncode == 0 else "error", r.delete_cmd))
    return results
