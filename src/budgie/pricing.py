"""Approximate on-demand hourly prices (USD, us-east-1, Linux).

SPIKE NOTE: these are static and rounded — good enough to prove the parse→price
→block loop. The real product pulls the AWS Pricing API (already a solved data
source via awslabs' AWS Pricing MCP) and caches it. The moat is the *parser* and
the *gate*, not this table.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# EC2 instance-type -> $/hr (the ones that actually cause bill shocks)
EC2 = {
    "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416, "t3.large": 0.0832,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384, "m5.4xlarge": 0.768,
    "m5.12xlarge": 2.304, "m5.24xlarge": 4.608,
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.4xlarge": 0.68, "c5.9xlarge": 1.53,
    "r5.large": 0.126, "r5.xlarge": 0.252, "r5.4xlarge": 1.008, "r5.24xlarge": 6.048,
    "g5.xlarge": 1.006, "g5.12xlarge": 5.672,
    "p3.2xlarge": 3.06, "p4d.24xlarge": 32.7726, "p5.48xlarge": 98.32,  # GPU = bill bombs
}

# RDS db-instance-class -> $/hr (single-AZ, MySQL-ish)
RDS = {
    "db.t3.micro": 0.017, "db.t3.medium": 0.068,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.m5.4xlarge": 1.368,
    "db.r5.large": 0.24, "db.r5.xlarge": 0.48, "db.r5.4xlarge": 1.92, "db.r5.24xlarge": 11.52,
}

REDSHIFT = {"dc2.large": 0.25, "dc2.8xlarge": 4.80, "ra3.xlplus": 1.086, "ra3.4xlarge": 3.26}
ELASTICACHE = {"cache.t3.micro": 0.017, "cache.m5.large": 0.156, "cache.r5.large": 0.252}
SAGEMAKER = {   # ml.* $/hr (hosting/notebook) — subset; live path covers the rest
    "ml.t3.medium": 0.05, "ml.m5.xlarge": 0.23, "ml.m5.4xlarge": 0.922,
    "ml.g5.xlarge": 1.408, "ml.p3.2xlarge": 3.825, "ml.p4d.24xlarge": 37.688,
}
OPENSEARCH = {  # *.search $/hr — subset
    "t3.small.search": 0.036, "m6g.large.search": 0.128, "c6g.large.search": 0.113,
    "r6g.large.search": 0.167, "r6g.4xlarge.search": 1.336,
}
EBS_GB_MONTH = {  # storage is $/GB-month -> hourly via / HOURS_PER_MONTH
    "gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125,
    "st1": 0.045, "sc1": 0.015, "standard": 0.05,
}

# Flat-rate resources: $/hr regardless of size
FLAT = {
    "nat-gateway": 0.045,          # + data processing (ignored in spike)
    "eks-cluster": 0.10,           # control plane
    "load-balancer": 0.0225,       # ALB base
    "elastic-ip-idle": 0.005,
}

HOURS_PER_MONTH = 730


def hourly(table: str, sku: str) -> float | None:
    """Look up $/hr for a SKU in a named table; None if unknown."""
    return {"ec2": EC2, "rds": RDS, "redshift": REDSHIFT, "elasticache": ELASTICACHE,
            "sagemaker": SAGEMAKER, "opensearch": OPENSEARCH}.get(table, {}).get(sku)


def ebs_hourly(volume_type: str, size_gb: int) -> float | None:
    """EBS volume cost as $/hr (storage priced per GB-month)."""
    rate = EBS_GB_MONTH.get(volume_type)
    return None if rate is None else round(rate * size_gb / HOURS_PER_MONTH, 6)


# --- pricing provider seam -------------------------------------------------
# Move #1: swap the static table for live AWS pricing without touching parse/gate.


class StaticProvider:
    """The bundled table. Zero deps, offline, deterministic — the default."""
    name = "static"

    def hourly(self, table: str, sku: str, region: str = "us-east-1") -> float | None:
        return hourly(table, sku)                 # static table is us-east-1 only

    def flat(self, key: str) -> float | None:
        return FLAT.get(key)


# Price List API `location` values (subset). Region matters — prices vary.
REGION_LOCATION = {
    "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
    "us-west-2": "US West (Oregon)", "eu-west-1": "EU (Ireland)",
    "eu-central-1": "EU (Frankfurt)", "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)", "ap-northeast-1": "Asia Pacific (Tokyo)",
}


def _parse_ondemand_usd(item: str) -> float | None:
    """Pull the OnDemand USD/hr out of one Price List product JSON string."""
    try:
        terms = json.loads(item).get("terms", {}).get("OnDemand", {})
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None
    for term in terms.values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            try:
                v = float(usd)
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
    return None


_PRICE_TTL = 7 * 24 * 3600     # prices change rarely; refetch weekly


def _cache_file() -> Path:
    return Path(os.environ.get("BUDGIE_HOME", ".budgie")) / "price-cache.json"


def _load_disk_cache() -> dict:
    try:
        return json.loads(_cache_file().read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_disk_cache(cache: dict) -> None:
    f = _cache_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cache))
    except OSError:
        pass


class AwsPricingProvider:
    """Live pricing via the AWS Price List API. boto3 is lazy-imported ONLY when
    selected. Prices are cached to disk ($BUDGIE_HOME/price-cache.json) so the
    per-command hook process doesn't re-hit AWS every time. Region is honoured —
    the same SKU is priced for the command's actual region.

    `flat` (NAT/EKS/ALB) and services without a wired filter fall back to the
    bundled static table.
    """
    name = "aws"

    def __init__(self, client=None, region: str = "us-east-1") -> None:
        if client is None:
            import boto3  # lazy — only when BUDGIE_PRICING=aws
            client = boto3.client("pricing", region_name="us-east-1")
        self._client = client
        self._default_region = region
        self._mem: dict[str, float | None] = {}
        self._disk = _load_disk_cache()

    def _filters(self, table: str, sku: str, location: str):
        common = [{"Type": "TERM_MATCH", "Field": "location", "Value": location}]
        if table == "ec2":
            return "AmazonEC2", [
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": sku},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ] + common
        if table == "rds":
            return "AmazonRDS", [
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": sku},
                {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": "MySQL"},
                {"Type": "TERM_MATCH", "Field": "deploymentOption", "Value": "Single-AZ"},
            ] + common
        if table == "elasticache":
            return "AmazonElastiCache", [
                {"Type": "TERM_MATCH", "Field": "cacheNodeType", "Value": sku}] + common
        if table == "redshift":
            return "AmazonRedshift", [
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": sku}] + common
        return None, None

    def hourly(self, table: str, sku: str, region: str | None = None) -> float | None:
        region = region or self._default_region
        key = f"{table}|{sku}|{region}"
        if key in self._mem:
            return self._mem[key]
        ent = self._disk.get(key)
        if ent and (time.time() - ent[1]) < _PRICE_TTL:     # warm disk cache
            self._mem[key] = ent[0]
            return ent[0]

        location = REGION_LOCATION.get(region, REGION_LOCATION["us-east-1"])
        service, filters = self._filters(table, sku, location)
        if not service:
            price = hourly(table, sku)                       # no live filter -> static
        else:
            price = None
            try:
                resp = self._client.get_products(
                    ServiceCode=service, Filters=filters, MaxResults=1)
                for item in resp.get("PriceList", []):
                    price = _parse_ondemand_usd(item)
                    if price is not None:
                        break
            except Exception:
                price = None                                 # net/creds error -> warn
        self._mem[key] = price
        self._disk[key] = [price, time.time()]
        _save_disk_cache(self._disk)
        return price

    def flat(self, key: str) -> float | None:
        return FLAT.get(key)


_ACTIVE = None


def active_provider():
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = (AwsPricingProvider() if os.environ.get("BUDGIE_PRICING") == "aws"
                   else StaticProvider())
    return _ACTIVE


def lookup(table: str, sku: str, region: str = "us-east-1") -> float | None:
    return active_provider().hourly(table, sku, region)


def lookup_flat(key: str) -> float | None:
    return active_provider().flat(key)
