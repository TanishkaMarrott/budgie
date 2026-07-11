"""Composite pricing: RDS instance + storage (+ Multi-AZ), EKS node groups."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check
from budgie.parse import extract
from budgie.estimate import estimate


def v(cmd, cap=2.0):
    return check(cmd, cap).verdict


def _rate(cmd):
    return sum(estimate(i).total_hourly or 0.0 for i in extract(cmd))


# --- RDS: storage is often the real cost, not the instance class ---
def test_rds_adds_storage():
    # db.t3.micro ≈ $0.017/hr instance; +10000GB gp2 ≈ $1.575/hr storage
    r = _rate("aws rds create-db-instance --db-instance-class db.t3.micro "
              "--allocated-storage 10000 --storage-type gp2")
    assert 1.5 < r < 1.7

def test_rds_storage_flips_to_block():
    # cheap instance, huge storage -> block (was $0.017/hr allow before the fix)
    assert v("aws rds create-db-instance --db-instance-class db.t3.micro "
             "--allocated-storage 20000 --storage-type gp2") == "block"

def test_rds_multi_az_doubles():
    single = _rate("aws rds create-db-instance --db-instance-class db.m5.large")
    multi = _rate("aws rds create-db-instance --db-instance-class db.m5.large --multi-az")
    assert abs(multi - 2 * single) < 0.01

def test_rds_instance_only_unchanged():
    assert v("aws rds create-db-instance --db-instance-class db.r5.24xlarge") == "block"


# --- EKS: the nodes (nodegroup) are the real cost, not the $0.10 control plane ---
def test_eks_nodegroup_priced_from_nodes():
    # 3 × m5.xlarge ($0.192) = $0.576/hr
    r = _rate("aws eks create-nodegroup --cluster-name c --nodegroup-name n "
              "--instance-types m5.xlarge --scaling-config minSize=1,maxSize=5,desiredSize=3")
    assert 0.55 < r < 0.60

def test_eks_nodegroup_blocks_when_big():
    # 4 × m5.4xlarge ($0.768) = $3.07/hr -> block
    assert v("aws eks create-nodegroup --cluster-name c --nodegroup-name n "
             "--instance-types m5.4xlarge --scaling-config desiredSize=4") == "block"

def test_eks_control_plane_still_flat():
    assert v("aws eks create-cluster --name prod") == "allow"
