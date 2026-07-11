"""Broadened service coverage: EBS, SageMaker, OpenSearch, Fargate/Aurora warn,
RDS resize, and (importantly) NOT warning on free creates."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check


def v(cmd, cap=2.0):
    return check(cmd, cap).verdict


# --- EBS storage ($/GB-month) ---
def test_ebs_large_volume_blocks():
    # 20000 GB gp3 @ $0.08/GB-mo = $1600/mo = ~$2.19/hr -> block
    assert v("aws ec2 create-volume --size 20000 --volume-type gp3 --availability-zone us-east-1a") == "block"

def test_ebs_small_volume_allows():
    assert v("aws ec2 create-volume --size 100 --volume-type gp3 --availability-zone us-east-1a") == "allow"

def test_ebs_missing_size_warns():
    assert v("aws ec2 create-volume --volume-type gp3 --cli-input-json file://v.json") == "warn"


# --- SageMaker notebook (priceable) ---
def test_sagemaker_notebook_priced():
    # ml.p4d.24xlarge = $37.688/hr -> block
    assert v("aws sagemaker create-notebook-instance --notebook-instance-name n "
             "--instance-type ml.p4d.24xlarge --role-arn arn:x") == "block"

def test_sagemaker_small_notebook_allows():
    assert v("aws sagemaker create-notebook-instance --notebook-instance-name n "
             "--instance-type ml.t3.medium --role-arn arn:x") == "allow"


# --- OpenSearch: instance type inside --cluster-config ---
def test_opensearch_cluster_config_parsed():
    # r6g.4xlarge.search = 1.336; x3 = 4.0 -> block
    cmd = ("aws opensearch create-domain --domain-name d "
           "--cluster-config InstanceType=r6g.4xlarge.search,InstanceCount=3")
    assert v(cmd) == "block"


# --- billable but sizing hidden -> warn (not silent allow) ---
def test_fargate_run_task_warns():
    assert v("aws ecs run-task --cluster c --launch-type FARGATE --task-definition td") == "warn"

def test_aurora_cluster_warns():
    assert v("aws rds create-db-cluster --db-cluster-identifier c --engine aurora-mysql") == "warn"

def test_sagemaker_training_warns():
    assert v("aws sagemaker create-training-job --training-job-name t") == "warn"


# --- RDS resize (modify) ---
def test_rds_resize_blocks():
    assert v("aws rds modify-db-instance --db-instance-identifier d "
             "--db-instance-class db.r5.24xlarge") == "block"

def test_rds_modify_without_class_ignored():
    # a non-resize modify shouldn't be flagged
    assert v("aws rds modify-db-instance --db-instance-identifier d "
             "--backup-retention-period 7") == "allow"


# --- free creates must NOT be warned (low false-positive) ---
def test_security_group_not_flagged():
    assert v("aws ec2 create-security-group --group-name g --description d") == "allow"

def test_create_tags_not_flagged():
    assert v("aws ec2 create-tags --resources i-1 --tags Key=a,Value=b") == "allow"
