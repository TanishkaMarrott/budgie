"""Robustness fixes: path/wrapper aws detection, region-aware + disk-cached pricing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from budgie import check
from budgie.pricing import AwsPricingProvider


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))   # no stale disk price-cache


P5 = "ec2 run-instances --instance-type p5.48xlarge"


def v(c, cap=2.0):
    return check(c, cap).verdict


# --- fix 1: aws detected however it's invoked (else silent-allow) ---
def test_full_path_aws():
    assert v(f"/usr/local/bin/aws {P5}") == "block"

def test_aws2_binary():
    assert v(f"aws2 {P5}") == "block"

def test_env_prefix():
    assert v(f"AWS_PROFILE=prod aws {P5}") == "block"

def test_path_aws_inside_loop():
    assert v(f"for i in 1 2; do /usr/local/bin/aws {P5}; done") == "block"


# --- fix 3: region flows into the live filter ---
class Fake:
    def __init__(self, usd="0.1"):
        self.usd, self.last = usd, None
    def get_products(self, **kw):
        self.last = kw
        item = ('{"terms":{"OnDemand":{"a":{"priceDimensions":'
                '{"b":{"pricePerUnit":{"USD":"%s"}}}}}}}' % self.usd)
        return {"PriceList": [item]}


def test_region_selects_location():
    f = Fake()
    AwsPricingProvider(client=f).hourly("ec2", "m5.large", region="ap-northeast-1")
    locs = [x["Value"] for x in f.last["Filters"] if x["Field"] == "location"]
    assert locs == ["Asia Pacific (Tokyo)"]


def test_region_is_part_of_cache_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    n = {"c": 0}
    class Counting(Fake):
        def get_products(self, **kw):
            n["c"] += 1; return super().get_products(**kw)
    p = AwsPricingProvider(client=Counting())
    p.hourly("ec2", "m5.large", "us-east-1")
    p.hourly("ec2", "m5.large", "ap-south-1")   # different region -> new key -> 2nd call
    assert n["c"] == 2


# --- fix 2: disk cache survives across provider instances (fresh hook processes) ---
def test_disk_cache_persists_across_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))
    n = {"c": 0}
    class Counting(Fake):
        def get_products(self, **kw):
            n["c"] += 1; return super().get_products(**kw)
    AwsPricingProvider(client=Counting()).hourly("ec2", "m5.large", "us-east-1")
    # a brand-new provider (simulating the next hook process) must read the disk cache
    AwsPricingProvider(client=Counting()).hourly("ec2", "m5.large", "us-east-1")
    assert n["c"] == 1
    assert (tmp_path / "price-cache.json").exists()
