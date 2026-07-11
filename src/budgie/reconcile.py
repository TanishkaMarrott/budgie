"""PostToolUse reconciliation — keep the session's active burn honest AFTER a
command runs, with no AWS polling.

PreToolUse commits a create's rate to active_rate (before the resource exists).
This runs after the command and reads its own output/args:
  • create succeeded → record {resource_id: rate} so a later delete can credit it
  • create FAILED    → release the rate PreToolUse committed (nothing was made)
  • delete succeeded → release_by_id for each id named in the delete command

Covers agent-driven creates/deletes. Out-of-band deletes (console, TTL,
autoscaling) are out of scope — that's a platform's job (snapshot polling).
"""
from __future__ import annotations

import json
import re

from . import parse as _parse
from . import state
from .estimate import estimate as _estimate

# delete action -> the flag holding the resource id(s)
_DELETE_IDS = {
    "terminate-instances": "instance-ids",
    "delete-db-instance": "db-instance-identifier",
    "delete-nat-gateway": "nat-gateway-id",
    "delete-volume": "volume-id",
    "delete-nodegroup": "nodegroup-name",
    "delete-cluster": "cluster-name",
    "delete-cache-cluster": "cache-cluster-id",
    "delete-load-balancer": "load-balancer-arn",
}
# id keys AWS returns in create output JSON
_ID_KEYS = ("InstanceId", "DBInstanceIdentifier", "NatGatewayId", "VolumeId",
            "NodegroupName", "ClusterName", "CacheClusterId", "LoadBalancerArn")
_ID_RE = re.compile(r"\b(i-[0-9a-f]{6,}|nat-[0-9a-f]{6,}|vol-[0-9a-f]{6,})\b")


def _created_ids(output: str) -> list[str]:
    ids: list[str] = []
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError, TypeError):
        data = None

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _ID_KEYS and isinstance(v, str):
                    ids.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    if not ids:
        ids = _ID_RE.findall(output or "")
    seen = set()
    return [x for x in ids if not (x in seen or seen.add(x))]


def _ids_from(value: str) -> list[str]:
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


def _delete_targets(command: str) -> list[str]:
    """Resource ids named by any aws delete/terminate invocation in the command."""
    tokens = _parse._tokenize(command)
    targets: list[str] = []
    n, i = len(tokens), 0
    while i < n:
        if _parse._is_aws(tokens[i]):
            j, span = i + 1, []
            while j < n and not _parse._is_aws(tokens[j]) and tokens[j] not in _parse._BOUNDARY:
                span.append(tokens[j]); j += 1
            if len(span) >= 2:
                flags = _parse._split_flags(span[2:])
                idflag = _DELETE_IDS.get(span[1])
                if idflag and flags.get(idflag):
                    targets += _ids_from(flags[idflag])
            i = j if j > i else i + 1
        else:
            i += 1
    return targets


def reconcile(command: str, output: str, success: bool, session_id: str) -> dict:
    """Post-execution: keep the session's active burn honest."""
    intents = _parse.extract(command)                    # a priced CREATE?
    if intents:
        ests = [_estimate(i) for i in intents]
        rate = sum(e.total_hourly for e in ests if e.total_hourly is not None)
        if not success:
            if rate:
                state.release_active(session_id, rate)   # undo the PreToolUse commit
            return {"action": "create-failed-release", "rate": rate}
        ids = _created_ids(output)
        if ids and rate:
            per = round(rate / len(ids), 6)
            for rid in ids:
                state.record_resource(session_id, rid, per)
        return {"action": "create-recorded", "ids": ids, "rate": rate}

    if success:                                          # a DELETE?
        freed, got = 0.0, []
        for rid in _delete_targets(command):
            f = state.release_by_id(session_id, rid)
            if f:
                freed += f
                got.append(rid)
        if got:
            return {"action": "delete-credited", "ids": got, "freed": round(freed, 6)}
    return {"action": "noop"}
