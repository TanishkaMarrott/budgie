"""PostToolUse reconciliation — the FACTUAL side of the ledger, with no AWS polling.

PreToolUse only *decides*; it commits nothing. This runs at PostToolUse, which
Claude Code fires ONLY when a command succeeds — so everything here is a fact:
  • create succeeded → COMMIT its rate to the session + record {id: rate} so a
    later delete can credit it back
  • delete succeeded → release_by_id for each id named in the delete command

Because a FAILED command fires no PostToolUse hook at all (verified live on
2.1.121 — and no PostToolUseFailure either), a failed create simply never reaches
here and never counts. There is no phantom commit to roll back — we never wrote one.

Covers agent-driven creates/deletes. Out-of-band deletes (console, TTL,
autoscaling) are out of scope — that's a platform's job (snapshot polling)."""

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
# create action -> the flag holding the USER-ASSIGNED identifier. Recording by this
# (in addition to the output-parsed id) means a later delete can credit the resource
# even when the create ran with --output text / --query and emitted no parseable id.
# Only where the create's identifier matches the delete's identifier flag above.
_CREATE_ID_FLAGS = {
    "create-db-instance": "db-instance-identifier",
    "create-nodegroup": "nodegroup-name",
    "create-cluster": "cluster-name",  # eks
    "create-cache-cluster": "cache-cluster-id",
}

# botocore's CLI prints this exact prefix on EVERY failed API call. If PostToolUse
# ever fires on a failure (the success-only guarantee is version-specific), this is
# the defensive signal that a "create" did not actually create anything.
def _looks_failed(text: str) -> bool:
    return "An error occurred (" in (text or "") and "when calling the" in text
# id keys AWS returns in create output JSON
_ID_KEYS = (
    "InstanceId",
    "DBInstanceIdentifier",
    "NatGatewayId",
    "VolumeId",
    "NodegroupName",
    "ClusterName",
    "CacheClusterId",
    "LoadBalancerArn",
)
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


def _walk(command: str):
    """Yield (service, action, flags) for every aws invocation — reusing the
    parser's global-flag-aware span logic so `aws --region x ec2 terminate-instances`
    is credited too (the leading-global-flag bypass hit deletes as well as creates)."""
    tokens = _parse._tokenize(command)
    n, i = len(tokens), 0
    while i < n:
        if _parse._is_aws(tokens[i]):
            j, span = i + 1, []
            while j < n and not _parse._is_aws(tokens[j]) and tokens[j] not in _parse._BOUNDARY:
                span.append(tokens[j])
                j += 1
            sa = _parse._service_action(span)
            if sa:
                yield sa
            i = j if j > i else i + 1
        else:
            i += 1


def _named_ids(command: str, flag_map: dict) -> list[str]:
    ids: list[str] = []
    for _service, action, flags in _walk(command):
        idflag = flag_map.get(action)
        if idflag and flags.get(idflag):
            ids += _ids_from(flags[idflag])
    return ids


def _delete_targets(command: str) -> list[str]:
    """Resource ids named by any aws delete/terminate invocation in the command."""
    return _named_ids(command, _DELETE_IDS)


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def reconcile(command: str, output: str, session_id: str, failed: bool = False) -> dict:
    """Post-execution accounting. PostToolUse *should* fire only on success, but we
    do not rely on that alone: if the call was interrupted or its output carries a
    botocore error, `failed` is set and we commit NOTHING (defensive no-phantom-spend).

    On success: commit a create's cost, or credit a delete."""
    if failed:
        return {"action": "skipped-failed"}

    intents = _parse.extract(command)
    # A resize (modify-*) is priced by the GATE, but committing it here would ADD a
    # second full instance rate on top of the original create — double-counting. The
    # gate still blocks an over-cap resize; the ledger simply doesn't re-commit it.
    committable = [i for i in intents if not i.action.startswith("modify")]
    if committable:
        ests = [_estimate(i) for i in committable]
        rate = sum(e.total_hourly for e in ests if e.total_hourly is not None)
        if rate:
            state._commit(session_id, rate)  # count the spend — it succeeded
        # Record by BOTH the output-parsed id and the user-assigned identifier from
        # the create command, so a later delete credits back even under --output text.
        ids = _dedupe(_created_ids(output) + _named_ids(command, _CREATE_ID_FLAGS))
        if ids and rate:
            per = round(rate / len(ids), 6)
            for rid in ids:
                state.record_resource(session_id, rid, per)
        return {"action": "create-committed", "ids": ids, "rate": rate}

    freed, got = 0.0, []  # a DELETE?
    for rid in _delete_targets(command):
        f = state.release_by_id(session_id, rid)
        if f:
            freed += f
            got.append(rid)
    if got:
        return {"action": "delete-credited", "ids": got, "freed": round(freed, 6)}
    return {"action": "noop"}
