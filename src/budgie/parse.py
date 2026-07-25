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
    ("memorydb", "create-cluster"),  # billable; create-cluster no longer flat-priced
    ("dax", "create-cluster"),
}
_QTY_FLAGS = ("count", "max-count", "min-count", "num-cache-nodes", "number-of-nodes")
_UNPRICEABLE = ("cli-input-json", "cli-input-yaml")
_LOOP = {"for", "while", "until", "xargs"}
_BOUNDARY = {";", "&&", "||", "|", "&", "do", "done", "then", "fi", "(", ")", "{", "}"}

# AWS CLI *global* options that can precede the service name and take a value.
# They must be skipped when locating the service, else `aws --region x ec2
# run-instances` parses `--region` as the service and the command is a silent
# allow — the single most common invocation form was a total bypass.
_GLOBAL_VALUE_FLAGS = {
    "region", "profile", "output", "endpoint-url", "cli-read-timeout",
    "cli-connect-timeout", "ca-bundle", "cli-binary-format", "color",
    "query", "page-size", "max-items", "starting-token",
}

# Safety net (no silent allow of unknown spend). Any action starting with one of
# these verbs *provisions* something billable — if we couldn't price or warn it
# above, it must still WARN rather than fall through to allow. This closes the
# whole class of un-enumerated expensive creates (autoscaling groups, EC2/spot
# fleets, dedicated hosts, FSx, Lightsail, DocumentDB/Neptune, RDS restores, …).
_BILLABLE_VERBS = (
    "create-", "run-", "request-", "restore-", "provision-",
    "allocate-", "purchase-", "reserve-", "launch-",
)
# The exception list: genuinely free-to-create operations, kept silent so routine
# automation isn't buried in warnings. Everything NOT here that matches a verb warns.
_FREE_CREATE_ACTIONS = {
    # networking / compute primitives (free)
    "create-security-group", "create-vpc", "create-subnet", "create-route-table",
    "create-route", "create-internet-gateway", "create-egress-only-internet-gateway",
    "create-network-interface", "create-network-acl", "create-dhcp-options",
    "create-key-pair", "import-key-pair", "create-placement-group",
    "create-launch-template", "create-launch-template-version",
    "create-tags", "create-default-vpc", "create-default-subnet",
    "create-customer-gateway", "create-local-gateway-route",
    # snapshots / images (storage-usage, not provisioning)
    "create-snapshot", "create-image", "register-image",
    # RDS/ElastiCache/Redshift config objects (free)
    "create-db-subnet-group", "create-db-parameter-group", "create-db-cluster-parameter-group",
    "create-cache-subnet-group", "create-cache-parameter-group", "create-option-group",
    "create-cluster-subnet-group", "create-cluster-parameter-group",
    # IAM / identity (free)
    "create-role", "create-policy", "create-policy-version", "create-user",
    "create-group", "create-instance-profile", "create-service-linked-role",
    "create-login-profile", "create-access-key", "create-saml-provider",
    # serverless / eventing / logs / storage-container (free to create; usage-billed)
    "create-function", "create-bucket", "create-topic", "create-queue",
    "create-log-group", "create-log-stream", "create-repository", "create-secret",
    "create-api", "create-rest-api", "create-deployment", "create-stage",
    "create-hosted-zone", "create-change-set", "create-alarm",
}
# Ambiguous action names that are free only for specific services (kept silent).
_FREE_CREATE_SA = {
    ("ecs", "create-cluster"),  # ECS cluster is free; tasks/services carry the cost
    ("batch", "create-compute-environment"),  # priced when it launches, not here
}


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
    # Neutralise shell substitution / subshell punctuation so an `aws` hidden
    # inside $(...), `...`, or a ( subshell ) is exposed as its own token instead
    # of being glued to `$(` and silently missed. We parse intent, never execute,
    # so flattening these fails toward *detecting more*, which is the safe side.
    command = re.sub(r"\$\(|[()`]", " ", command)
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


def _qty_is_dynamic(flags: dict[str, str]) -> bool:
    """A quantity flag whose value isn't a literal integer (`--count $N`,
    `--count $(…)`) can't be bounded — pricing it as 1 would let an arbitrarily
    large fan-out through. Treated like an unbounded loop: the gate blocks it."""
    for k in _QTY_FLAGS:
        v = flags.get(k)
        if v:
            n = v.split(":")[-1]
            try:
                int(n)
                return False
            except ValueError:
                return True
    return False


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
        unbounded=_qty_is_dynamic(flags),  # dynamic --count can't be bounded -> block
    )


def _service_action(span: list[str]) -> tuple[str, str, dict[str, str]] | None:
    """Find (service, action, flags), skipping any leading AWS *global* options
    (--region/--profile/--output/…) that precede the service name. Without this,
    `aws --profile p ec2 run-instances` parsed `--profile` as the service and was
    silently allowed — the worst failure mode a firewall can have."""
    i, n = 0, len(span)
    while i < n and span[i].startswith("-"):
        name = span[i].lstrip("-")
        if "=" in name:  # --region=x — value attached, one token
            i += 1
        elif name in _GLOBAL_VALUE_FLAGS and i + 1 < n and not span[i + 1].startswith("-"):
            i += 2  # --region x — consume the value too
        else:
            i += 1  # boolean global flag (--debug, --no-verify-ssl, …)
    if i + 1 >= n:  # need both a service and an action
        return None
    # Parse flags across the WHOLE span so a global `--region`/`--profile` that
    # preceded the service is still captured (skipping it must not drop its value —
    # the live pricer needs the region). Positional service/action are ignored.
    return span[i], span[i + 1], _split_flags(span)


def _parse_invocation(span: list[str], in_loop: bool, raw: str) -> Intent | None:
    sa = _service_action(span)
    if sa is None:
        return None
    service, action, flags = sa
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

    # create-cluster is service-ambiguous: only EKS is the flat $0.10 control
    # plane. kafka/emr create-cluster are bill-shock services meant to WARN below;
    # ecs create-cluster is free. Gate the flat match on the service so MSK/EMR are
    # no longer mispriced to $0.10 and allowed (their _WARN_ACTIONS entries were
    # dead code — flat matched first).
    if action in _FLAT_ACTIONS and not (action == "create-cluster" and service != "eks"):
        return _mk(service, action, "flat", _FLAT_ACTIONS[action], flags, in_loop, raw)

    # Billable-but-unparseable (Fargate, Aurora, EMR, SageMaker training, MSK, ...) -> warn.
    if (service, action) in _WARN_ACTIONS:
        return warn(f"{service} {action}: billable, cost hidden in config — review")

    if (action.startswith("create") or action.startswith("run")) and unpriceable:
        return warn("params in --cli-input-json")

    # SAFETY NET — no silent allow of unknown spend. If the action provisions
    # something (a billable verb) and we didn't recognise/price it above, and it's
    # not on the curated free-create allowlist, WARN rather than fall through to
    # allow. This closes the whole class of un-enumerated expensive creates.
    if (
        action.startswith(_BILLABLE_VERBS)
        and action not in _FREE_CREATE_ACTIONS
        and (service, action) not in _FREE_CREATE_SA
    ):
        return warn(f"{service} {action}: provisions a billable resource we can't price — review before running")
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
