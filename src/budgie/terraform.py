"""Price a Terraform plan.

`terraform apply` has no CLI SKU to parse — but `terraform show -json <plan>`
does. Feed that JSON here and budgie prices every resource being *created* and
gates the total, the same way it gates an `aws` command.

    terraform plan -out tf.plan && terraform show -json tf.plan > plan.json
    budgie tf-plan plan.json
"""
from __future__ import annotations

import json

from . import pricing
from .estimate import Estimate
from .gate import aggregate, Decision

# terraform resource type -> (pricing table, attribute holding the sku)
_TF = {
    "aws_instance": ("ec2", "instance_type"),
    "aws_db_instance": ("rds", "instance_class"),
    "aws_elasticache_cluster": ("elasticache", "node_type"),
    "aws_redshift_cluster": ("redshift", "node_type"),
    "aws_sagemaker_notebook_instance": ("sagemaker", "instance_type"),
    "aws_ebs_volume": ("ebs", "type"),
}
# billable but sizing lives elsewhere in the plan -> warn
_TF_WARN = {"aws_ecs_service", "aws_rds_cluster", "aws_emr_cluster",
            "aws_opensearch_domain", "aws_elasticsearch_domain", "aws_msk_cluster"}


def _estimate_resource(rtype: str, after: dict) -> Estimate | None:
    if rtype in _TF:
        table, attr = _TF[rtype]
        if table == "ebs":
            size = int(after.get("size", 0) or 0)
            rate = pricing.ebs_hourly(after.get("type") or "gp3", size)
            return Estimate(rate, 1, f"{rtype} {size}GB", rate is not None, note="storage $/GB-mo")
        sku = after.get(attr)
        if sku:
            r = pricing.lookup(table, sku)
            return Estimate(r, 1, f"{rtype} {sku}", r is not None)
        return Estimate(None, 1, rtype, False, note="sku not in plan")
    if rtype in _TF_WARN:
        return Estimate(None, 1, rtype, False, note="billable, sizing elsewhere in plan")
    return None


def price_plan(plan_json: str, cap_hourly: float = 2.0) -> Decision:
    try:
        data = json.loads(plan_json)
    except (json.JSONDecodeError, ValueError):
        return Decision("warn", "could not parse terraform plan JSON — review manually.")
    estimates = []
    for rc in data.get("resource_changes", []):
        if "create" not in rc.get("change", {}).get("actions", []):
            continue
        est = _estimate_resource(rc.get("type", ""), rc.get("change", {}).get("after") or {})
        if est is not None:
            estimates.append(est)
    if not estimates:
        return Decision("allow", "no billable resources being created in this plan.")
    return aggregate(estimates, cap_hourly)
