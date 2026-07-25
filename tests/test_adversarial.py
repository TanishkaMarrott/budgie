"""Adversarial parser surface — the bypasses a firewall lives or dies on.

The happy-path suite was green while `aws --profile p ec2 run-instances` sailed
through as "not a spend command". These tests pin the *evasive* invocation forms:
leading global options, service-ambiguous actions, and command substitution.
A property test closes the loop — no billable action, under any global-flag
prefix, may parse to an empty intent list (a silent allow).
"""

import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check  # noqa: E402
from budgie.parse import extract  # noqa: E402

CAP = 2.0

# A big, unambiguously-over-cap create in each priced family.
BILLABLE_CORES = [
    "ec2 run-instances --instance-type p5.48xlarge",
    "ec2 run-instances --instance-type p5.48xlarge --count 4",
    "rds create-db-instance --db-instance-class db.r5.24xlarge --engine mysql",
    "eks create-nodegroup --cluster-name c --nodegroup-name n "
    "--instance-types m5.24xlarge --scaling-config minSize=1,maxSize=50,desiredSize=40",
    "elasticache create-cache-cluster --cache-cluster-id x --cache-node-type cache.r5.large --num-cache-nodes 20",
    "redshift create-cluster --cluster-identifier x --node-type dc2.8xlarge --number-of-nodes 10",
]

# Global-option prefixes an agent realistically puts between `aws` and the service.
GLOBAL_PREFIXES = [
    "",
    "--region us-west-2",
    "--profile prod",
    "--region eu-west-1 --profile prod",
    "--output json",
    "--output table --no-cli-pager",
    "--endpoint-url https://ec2.local",
    "--debug",  # boolean global — must not swallow the service token
    "--region=us-west-2",  # attached-value form
    "--color off --profile prod --output json",
]


# ---- BUG-1: global options before the service must not bypass the gate --------


@pytest.mark.parametrize("prefix", GLOBAL_PREFIXES)
@pytest.mark.parametrize("core", BILLABLE_CORES)
def test_global_flags_never_bypass(prefix, core):
    cmd = f"aws {prefix} {core}".strip()
    intents = extract(cmd)
    assert intents, f"silent allow — nothing parsed from: {cmd}"
    assert check(cmd, CAP).verdict == "block", f"not blocked: {cmd}"


def test_boolean_global_flag_keeps_service():
    # `--debug` takes no value; the next token is the service, not its argument.
    intents = extract("aws --debug ec2 run-instances --instance-type p5.48xlarge")
    assert len(intents) == 1
    assert intents[0].service == "ec2" and intents[0].action == "run-instances"


def test_value_global_flag_consumes_its_value():
    intents = extract("aws --region us-west-2 ec2 run-instances --instance-type t3.micro")
    assert len(intents) == 1
    assert intents[0].region == "us-west-2"
    assert intents[0].sku == "t3.micro"


# ---- BUG-2: create-cluster is service-ambiguous -------------------------------


def test_msk_create_cluster_warns_not_cheap_allow():
    # Was mispriced to a $0.10 flat EKS control plane and allowed.
    assert check("aws kafka create-cluster --cluster-name x --number-of-broker-nodes 6", CAP).verdict == "warn"


def test_emr_create_cluster_warns():
    assert check("aws emr create-cluster --instance-type m5.24xlarge --instance-count 20", CAP).verdict == "warn"


def test_ecs_create_cluster_is_free_allow():
    # ECS clusters are free — must not be charged the EKS flat rate.
    d = check("aws ecs create-cluster --cluster-name c", CAP)
    assert d.verdict == "allow"


def test_eks_create_cluster_still_flat_priced():
    intents = extract("aws eks create-cluster --name c")
    assert len(intents) == 1 and intents[0].table == "flat" and intents[0].sku == "eks-cluster"


# ---- BUG-3: command substitution / subshell -----------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "RESULT=$(aws ec2 run-instances --instance-type p5.48xlarge)",
        "ID=`aws ec2 run-instances --instance-type p5.48xlarge`",
        "( aws ec2 run-instances --instance-type p5.48xlarge )",
        "echo start; x=$(aws rds create-db-instance --db-instance-class db.r5.24xlarge)",
    ],
)
def test_command_substitution_detected(cmd):
    assert extract(cmd), f"aws hidden in substitution missed: {cmd}"
    assert check(cmd, CAP).verdict == "block"


# ---- Property / fuzz: no billable action, under any global prefix, is empty ----


def test_property_no_billable_action_is_silently_empty():
    """Every (prefix × billable core) combination must parse to >=1 intent.
    An empty list here is a silent allow — the class of bug that was BUG-1."""
    misses = []
    for prefix, core in itertools.product(GLOBAL_PREFIXES, BILLABLE_CORES):
        cmd = f"aws {prefix} {core}".strip()
        if not extract(cmd):
            misses.append(cmd)
    assert not misses, f"{len(misses)} silent-allow bypasses:\n" + "\n".join(misses)


def test_property_chained_creates_all_counted():
    # Two creates in a `&&` chain, each behind a different global prefix.
    cmd = (
        "aws --region us-west-2 ec2 run-instances --instance-type m5.large && "
        "aws --profile prod ec2 run-instances --instance-type m5.large"
    )
    intents = extract(cmd)
    assert len(intents) == 2
