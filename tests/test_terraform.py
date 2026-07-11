"""Terraform plan pricing — feeds `terraform show -json` output."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie.terraform import price_plan


def _plan(*resources):
    return json.dumps({"resource_changes": [
        {"type": t, "change": {"actions": ["create"], "after": after}}
        for (t, after) in resources
    ]})


def test_block_big_instance():
    assert price_plan(_plan(("aws_instance", {"instance_type": "p5.48xlarge"}))).verdict == "block"

def test_allow_small_instance():
    assert price_plan(_plan(("aws_instance", {"instance_type": "t3.micro"}))).verdict == "allow"

def test_sums_across_resources():
    plan = _plan(("aws_instance", {"instance_type": "m5.large"}),
                 ("aws_db_instance", {"instance_class": "db.r5.24xlarge"}))
    assert price_plan(plan).verdict == "block"

def test_ebs_volume_priced():
    assert price_plan(_plan(("aws_ebs_volume", {"type": "gp3", "size": 20000}))).verdict == "block"

def test_aurora_cluster_warns():
    assert price_plan(_plan(("aws_rds_cluster", {"engine": "aurora-mysql"}))).verdict == "warn"

def test_ignores_deletes_and_updates():
    plan = json.dumps({"resource_changes": [
        {"type": "aws_instance", "change": {"actions": ["delete"], "after": None}}]})
    assert price_plan(plan).verdict == "allow"

def test_empty_plan_allows():
    assert price_plan(json.dumps({"resource_changes": []})).verdict == "allow"

def test_bad_json_warns():
    assert price_plan("{ not json").verdict == "warn"
