"""Live AWS Price List adapter — verified offline with a mocked pricing client."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from budgie.pricing import AwsPricingProvider, _parse_ondemand_usd


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    # give each test its own disk price-cache so they don't contaminate each other
    monkeypatch.setenv("BUDGIE_HOME", str(tmp_path))

# A realistic (trimmed) Price List product JSON string.
SAMPLE = ('{"product":{"attributes":{"instanceType":"m5.large"}},'
          '"terms":{"OnDemand":{"ABC.JRTCKXETXF":{"priceDimensions":'
          '{"ABC.JRTCKXETXF.6YS6EN2CT7":{"pricePerUnit":{"USD":"0.0960000000"}}}}}}}')


class Fake:
    def __init__(self, price_list): self._pl = price_list
    def get_products(self, **kw): return {"PriceList": self._pl}


def test_parse_ondemand_usd():
    assert abs(_parse_ondemand_usd(SAMPLE) - 0.096) < 1e-6

def test_parse_bad_json_returns_none():
    assert _parse_ondemand_usd("{not json") is None

def test_provider_hourly():
    p = AwsPricingProvider(client=Fake([SAMPLE]))
    assert abs(p.hourly("ec2", "m5.large") - 0.096) < 1e-6

def test_provider_unknown_returns_none():
    p = AwsPricingProvider(client=Fake([]))
    assert p.hourly("ec2", "z9.nope") is None

def test_provider_api_error_returns_none():
    class Boom:
        def get_products(self, **kw): raise RuntimeError("no creds")
    assert AwsPricingProvider(client=Boom()).hourly("ec2", "m5.large") is None

def test_flat_falls_back_to_static_table():
    assert AwsPricingProvider(client=Fake([])).flat("nat-gateway") == 0.045

def test_result_is_cached():
    n = {"calls": 0}
    class Counting:
        def get_products(self, **kw):
            n["calls"] += 1; return {"PriceList": [SAMPLE]}
    p = AwsPricingProvider(client=Counting())
    p.hourly("ec2", "m5.large"); p.hourly("ec2", "m5.large")
    assert n["calls"] == 1

def test_filters_use_given_location():
    p = AwsPricingProvider(client=Fake([SAMPLE]))
    _svc, filters = p._filters("ec2", "m5.large", "Asia Pacific (Mumbai)")
    locs = [f["Value"] for f in filters if f["Field"] == "location"]
    assert locs == ["Asia Pacific (Mumbai)"]
