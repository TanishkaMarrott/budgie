"""No escape boats. Every path where a genuinely-billable command could reach a
silent ALLOW is pinned here: un-enumerated provisioning verbs, dynamic quantities,
and non-aws IaC deploys must WARN or BLOCK — never allow. The free-create allowlist
is pinned too, so the safety net doesn't bury routine automation in warnings."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check  # noqa: E402

CAP = 2.0


def v(cmd):
    return check(cmd, CAP).verdict


# ---- the class of un-enumerated expensive creates must NOT silently allow -------

NEVER_SILENTLY_ALLOWED = [
    "aws autoscaling create-auto-scaling-group --min-size 40 --max-size 100 --launch-template x",
    "aws ec2 create-fleet --launch-template-configs file://c.json",
    "aws ec2 request-spot-fleet --spot-fleet-request-config file://c.json",
    "aws ec2 request-spot-instances --instance-count 100 --launch-specification file://s.json",
    "aws ec2 allocate-hosts --instance-type p5.48xlarge --quantity 5",
    "aws fsx create-file-system --file-system-type LUSTRE --storage-capacity 100000",
    "aws lightsail create-instances --instance-names x --bundle-id xlarge_3_0",
    "aws docdb create-db-instance --db-instance-class db.r5.24xlarge --db-instance-identifier d",
    "aws neptune create-db-instance --db-instance-class db.r5.12xlarge --db-instance-identifier n",
    "aws rds restore-db-instance-from-db-snapshot --db-instance-identifier r --db-snapshot-identifier s",
    "aws redshift restore-from-cluster-snapshot --cluster-identifier c --snapshot-identifier s",
    "aws elasticache create-replication-group --replication-group-id r --replication-group-description d",
    "aws memorydb create-cluster --cluster-name c --node-type db.r6g.4xlarge",
    "aws transfer create-server --protocols SFTP",
    "aws globalaccelerator create-accelerator --name a",
    "aws emr-serverless create-application --type SPARK --release-label emr-7.0",
]


@pytest.mark.parametrize("cmd", NEVER_SILENTLY_ALLOWED)
def test_unpriced_provisioning_never_allows(cmd):
    assert v(cmd) in ("warn", "block"), f"escape boat — silently allowed: {cmd}"


# ---- dynamic / unbounded quantity can't be priced as 1 -> block -----------------


@pytest.mark.parametrize(
    "cmd",
    [
        "aws ec2 run-instances --instance-type t3.micro --count $N",
        "aws ec2 run-instances --instance-type t3.micro --count $(cat n.txt)",
        "aws ec2 run-instances --instance-type t3.micro --count notanumber",
    ],
)
def test_dynamic_count_blocks(cmd):
    assert v(cmd) == "block", f"dynamic count priced as 1 (escape boat): {cmd}"


# ---- non-aws IaC deploys warn, never allow --------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "eksctl create cluster --name x --nodes 10",
        "sam deploy --guided",
        "serverless deploy",
        "terraform apply -auto-approve",
        "cdk deploy --all",
    ],
)
def test_iac_deploys_warn(cmd):
    assert v(cmd) == "warn", f"IaC deploy silently allowed: {cmd}"


# ---- the free-create allowlist stays quiet (no warn fatigue) --------------------

FREE_STAYS_ALLOW = [
    "aws ec2 create-security-group --group-name g --description d",
    "aws ec2 create-tags --resources i-x --tags Key=k,Value=v",
    "aws ec2 create-launch-template --launch-template-name lt",
    "aws iam create-role --role-name r --assume-role-policy-document file://p.json",
    "aws iam create-policy --policy-name p --policy-document file://p.json",
    "aws s3 mb s3://mybucket",
    "aws logs create-log-group --log-group-name lg",
    "aws sns create-topic --name t",
    "aws sqs create-queue --queue-name q",
    "aws ecs create-cluster --cluster-name free",
    "aws rds create-db-subnet-group --db-subnet-group-name g --subnet-ids s-1 s-2",
    "aws lambda create-function --function-name f --runtime python3.12 --role r --handler h",
    "aws ec2 create-snapshot --volume-id vol-123",
]


@pytest.mark.parametrize("cmd", FREE_STAYS_ALLOW)
def test_free_creates_stay_quiet(cmd):
    assert v(cmd) == "allow", f"free op noisily warned: {cmd}"


# ---- reads / deletes are never spend --------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "aws ec2 describe-instances",
        "aws ec2 terminate-instances --instance-ids i-abc",
        "aws s3 ls",
        "aws rds delete-db-instance --db-instance-identifier d",
    ],
)
def test_reads_and_deletes_allow(cmd):
    assert v(cmd) == "allow"


# ---- BUDGIE_STRICT: unpriceable spend blocks, but cheap known spend still runs --


def test_strict_blocks_unpriceable(monkeypatch):
    monkeypatch.setenv("BUDGIE_STRICT", "1")
    assert v("aws fsx create-file-system --storage-capacity 100000") == "block"
    assert v("aws docdb create-db-instance --db-instance-class db.r5.24xlarge") == "block"
    assert v("terraform apply -auto-approve") == "block"


def test_strict_still_allows_cheap_known(monkeypatch):
    monkeypatch.setenv("BUDGIE_STRICT", "1")
    assert v("aws ec2 run-instances --instance-type t3.micro") == "allow"


def test_strict_off_by_default():
    # without the env var, unpriceable spend warns (usable default), never silent-allow
    assert v("aws fsx create-file-system --storage-capacity 100000") == "warn"
