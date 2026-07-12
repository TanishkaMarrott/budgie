"""Intent parser — the moat, hardened.

Extracts EVERY aws spend invocation from an arbitrary bash string — loops,
`&&`/`;` chains, xargs — not just a line that starts with `aws`. Fails SAFE:
when a known spend action hides its params (`--cli-input-json`) it is returned
as unpriceable so the gate warns instead of silently allowing. Detects
--dry-run (no charge), spot, region, and robust quantities (`--count 1:5`).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# action -> (pricing table, sku flag).  Priceable straight from the CLI.
# (create-db-instance is handled separately so it can add attached storage.)
_SKU_ACTIONS = {
    "run-instances": ("ec2", "instance-type"),
    "create-cache-cluster": ("elasticache", "cache-node-type"),
    "create-notebook-instance": ("sagemaker", "instance-type"),
}
_REDSHIFT = ("redshift", "node-type")  # redshift create-cluster (disambiguated)
_FLAT_ACTIONS = {  # $/hr independent of size
    "create-nat-gateway": "nat-gateway",
    "create-cluster": "eks-cluster",  # eks create-cluster
    "create-load-balancer": "load-balancer",
    "allocate-address": "elastic-ip-idle",
}
# (service, action) that are billable but hide their sizing in a task def / nested
# config / separate resource — we can't price them, so WARN (never silent-allow).
_WARN_ACTIONS = {
    ("ecs", "run-task"),
    ("ecs", "create-service"),  # Fargate: cost in task def
    ("rds", "create-db-cluster"),  # Aurora: instances/serverless
    ("emr", "create-cluster"),
    ("emr", "run-job-flow"),
    ("sagemaker", "create-endpoint"),
    ("sagemaker", "create-training-job"),
    ("sagemaker", "create-transform-job"),
    ("kafka", "create-cluster"),
    ("mq", "create-broker"),
    ("elasticache", "create-replication-group"),
    ("redshift-serverless", "create-workgroup"),
}
_QTY_FLAGS = ("count", "max-count", "min-count", "num-cache-nodes", "number-of-nodes")
_UNPRICEABLE = ("cli-input-json", "cli-input-yaml")
_LOOP = {"for", "while", "until", "xargs"}
_BOUNDARY = {";", "&&", "||", "|", "&", "do", "done", "then", "fi", "(", ")", "{", "}"}


def _kv(s: str, key: str) -> str | None:
    """Pull a value out of AWS shorthand like 'InstanceType=r6g.large,InstanceCount=3'."""
    for part in s.replace(" ", "").split(","):
        if part.startswith(key + "="):
            return part.split("=", 1)[1]
    return None


def _rds_storage(it: "Intent", flags: dict) -> None:
    """Attach RDS storage details from the create/modify command onto the intent."""
    gb = flags.get("allocated-storage", "")
    it.storage_gb = int(gb) if gb.isdigit() else 0
    it.storage_type = flags.get("storage-type", "gp2")
    io = flags.get("iops", "")
    it.iops = int(io) if io.isdigit() else 0
    it.multi_az = "multi-az" in flags


@dataclass
class Intent:
    service: str
    action: str
    table: str  # ec2/rds/redshift/elasticache/flat/unknown
    sku: str | None
    qty: int = 1
    region: str = "us-east-1"
    spot: bool = False
    dry_run: bool = False
    priceable: bool = True  # False => params hidden => gate must warn, never allow
    reason: str = ""
    in_loop: bool = False
    raw: str = ""
    # composite (RDS): storage attached to the instance in the same command
    storage_gb: int = 0
    storage_type: str = "gp2"
    iops: int = 0
    multi_az: bool = False
    # a loop with no statically-bounded iteration count (while/until/xargs/dynamic
    # range) — total cost can't be computed, so the gate blocks rather than guesses.
    unbounded: bool = False


def _tokenize(command: str) -> list[str]:
    try:
        toks = shlex.split(command, comments=False, posix=True)
    except ValueError:
        toks = command.split()
    out: list[str] = []
    for t in toks:  # split trailing ';' into its own boundary token
        if t == ";":
            out.append(";")
        elif t.endswith(";") and len(t) > 1:
            out.append(t[:-1])
            out.append(";")
        else:
            out.append(t)
    return out


def _split_flags(tokens: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--"):
            key = t[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                flags[k] = v
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                flags[key] = tokens[i + 1]
                i += 1
            else:
                flags[key] = ""
        i += 1
    return flags


def _qty(flags: dict[str, str]) -> int:
    for k in _QTY_FLAGS:
        if flags.get(k):
            v = flags[k].split(":")[-1]  # "min:max" -> max
            try:
                return max(1, int(v))
            except ValueError:
                return 1
    return 1


def _mk(service, action, table, sku, flags, in_loop, raw, priceable=True, reason=""):
    return Intent(
        service=service,
        action=action,
        table=table,
        sku=sku,
        qty=_qty(flags),
        region=flags.get("region", "us-east-1"),
        spot=("instance-market-options" in flags and "spot" in raw.lower()),
        dry_run=("dry-run" in flags),
        priceable=priceable,
        reason=reason,
        in_loop=in_loop,
        raw=raw,
    )


def _parse_invocation(span: list[str], in_loop: bool, raw: str) -> Intent | None:
    if len(span) < 2:
        return None
    service, action = span[0], span[1]
    flags = _split_flags(span[2:])
    unpriceable = any(f in flags for f in _UNPRICEABLE)

    def warn(reason):
        return _mk(service, action, "unknown", None, flags, in_loop, raw, False, reason)

    # EBS volumes — priced as storage ($/GB-month); qty carries the size in GB.
    if service == "ec2" and action == "create-volume":
        size = flags.get("size", "")
        if size.isdigit() and int(size) > 0:
            it = _mk(service, action, "ebs", flags.get("volume-type", "gp3"), flags, in_loop, raw)
            it.qty = int(size)
            return it
        return warn("params in --cli-input-json") if unpriceable else warn("EBS volume size missing")

    # OpenSearch / legacy ES — instance type lives inside --cluster-config shorthand.
    if action in ("create-domain", "create-elasticsearch-domain") and service in ("opensearch", "es"):
        cc = flags.get("cluster-config", "")
        sku = _kv(cc, "InstanceType")
        if sku:
            it = _mk(service, action, "opensearch", sku, flags, in_loop, raw)
            cnt = _kv(cc, "InstanceCount")
            it.qty = int(cnt) if cnt and cnt.isdigit() else 1
            return it
        return (
            warn("params in --cli-input-json") if unpriceable else warn("OpenSearch instance not in --cluster-config")
        )

    # RDS instance — price the class + attached storage (+ Multi-AZ doubling).
    if action == "create-db-instance" and service == "rds":
        sku = flags.get("db-instance-class")
        if not sku:
            return warn("params in --cli-input-json") if unpriceable else warn("db-instance-class missing")
        it = _mk(service, action, "rds", sku, flags, in_loop, raw)
        _rds_storage(it, flags)
        return it

    # RDS resize (modify to a bigger class) — only our concern when a class is given.
    if action == "modify-db-instance":
        sku = flags.get("db-instance-class")
        if not sku:
            return None
        it = _mk(service, action, "rds", sku, flags, in_loop, raw)
        _rds_storage(it, flags)
        return it

    # EKS worker nodes — a nodegroup is where the real EKS cost lives (control
    # plane alone is $0.10/hr; the nodes are EC2 instances × desired size).
    if action == "create-nodegroup" and service == "eks":
        itypes = flags.get("instance-types") or flags.get("instance-type")
        sku = itypes.split(",")[0].strip() if itypes else None
        if not sku:
            return warn("params in --cli-input-json") if unpriceable else warn("nodegroup instance type missing")
        it = _mk(service, action, "ec2", sku, flags, in_loop, raw)
        desired = _kv(flags.get("scaling-config", ""), "desiredSize")
        it.qty = int(desired) if desired and desired.isdigit() else 1
        return it

    if action == "create-cluster" and service == "redshift":
        sku = flags.get(_REDSHIFT[1])
        if sku:
            return _mk(service, action, _REDSHIFT[0], sku, flags, in_loop, raw)
        return warn("params in --cli-input-json") if unpriceable else warn("redshift node-type missing")

    if action in _SKU_ACTIONS:
        table, sku_flag = _SKU_ACTIONS[action]
        sku = flags.get(sku_flag)
        if sku:
            return _mk(service, action, table, sku, flags, in_loop, raw)
        return warn("params in --cli-input-json") if unpriceable else warn(f"{action}: instance type missing")

    if action in _FLAT_ACTIONS:
        return _mk(service, action, "flat", _FLAT_ACTIONS[action], flags, in_loop, raw)

    # Billable-but-unparseable (Fargate, Aurora, EMR, SageMaker training, MSK, ...) -> warn.
    if (service, action) in _WARN_ACTIONS:
        return warn(f"{service} {action}: billable, cost hidden in config — review")

    if (action.startswith("create") or action.startswith("run")) and unpriceable:
        return warn("params in --cli-input-json")
    return None


def _is_aws(tok: str) -> bool:
    """Match `aws`, `aws2`, and full paths like /usr/local/bin/aws — not just a
    bare leading token. Missing a path-invoked command would be a silent allow."""
    return tok.rsplit("/", 1)[-1] in ("aws", "aws2")


def _loop_multiplier(command: str) -> int | None:
    """How many times a loop body runs, when statically determinable — so a
    `for i in $(seq 100)` create is priced as 100, not 1 (the $6,531 pattern).
    Returns the count (>=1), or None when it can't be bounded (while/until/xargs
    or a dynamic range like `$(cat hosts)`) — the caller treats None as unbounded."""
    if re.search(r"\b(while|until)\b", command) or re.search(r"\bxargs\b", command):
        return None
    m = re.search(r"\bfor\b\s+\w+\s+in\s+(.*?)\s*;?\s*\bdo\b", command, re.DOTALL)
    if not m:
        return None
    items = m.group(1).strip()
    sm = re.search(r"\bseq\s+(\d+)(?:\s+(\d+))?(?:\s+(\d+))?", items)  # seq N | A B | A STEP B
    if sm:
        nums = [int(x) for x in sm.groups() if x is not None]
        if len(nums) == 1:
            return max(1, nums[0])
        if len(nums) == 2:
            return max(1, nums[1] - nums[0] + 1)
        a, step, b = nums
        return max(1, (b - a) // step + 1) if step else 1
    bm = re.search(r"\{(\d+)\.\.(\d+)\}", items)  # brace expansion {1..N}
    if bm:
        return max(1, abs(int(bm.group(2)) - int(bm.group(1))) + 1)
    if "$(" not in items and "`" not in items and "*" not in items and items:  # literal list
        toks = items.split()
        if toks and all(not t.startswith("$") for t in toks):
            return max(1, len(toks))
    return None  # dynamic range -> unbounded


def extract(command: str) -> list[Intent]:
    """Every aws spend invocation in an arbitrary bash string."""
    tokens = _tokenize(command)
    in_loop = any(t in _LOOP for t in tokens)
    mult = _loop_multiplier(command) if in_loop else 1
    intents: list[Intent] = []
    n, i = len(tokens), 0
    while i < n:
        if _is_aws(tokens[i]):
            j, span = i + 1, []
            while j < n and not _is_aws(tokens[j]) and tokens[j] not in _BOUNDARY:
                span.append(tokens[j])
                j += 1
            got = _parse_invocation(span, in_loop, command)
            if got:
                if in_loop and mult is None:  # loop we can't bound
                    got.unbounded = True
                elif in_loop and mult and mult > 1:  # bounded loop -> price every iteration
                    got.qty *= mult
                intents.append(got)
            i = j if j > i else i + 1
        else:
            i += 1
    return intents
