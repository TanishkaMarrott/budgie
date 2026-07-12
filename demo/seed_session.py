"""Seed a realistic session for the demo gif (run off-camera via VHS `Hide`).

Writes one session with a live burn rate and some already-accrued dollars so
`budgie session` shows the two-number model (active_rate vs accrued_cost)
without waiting for real time to pass. Uses BUDGIE_HOME so it never touches a
real session store.
"""
import datetime
import json
import os
import pathlib

home = pathlib.Path(os.environ["BUDGIE_HOME"]) / "sessions"
home.mkdir(parents=True, exist_ok=True)
(home / "agent-8f2.json").write_text(json.dumps({
    "active_rate": 4.61,        # two m5.24xlarge nodes still up
    "accrued_cost": 13.83,      # ~3h of prior burn already spent
    "last_ts": datetime.datetime.now().isoformat(),
    "resources": {},
}))
