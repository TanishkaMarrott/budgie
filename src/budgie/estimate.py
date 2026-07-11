"""Intent -> cost estimate. Deterministic: table lookup × quantity."""
from __future__ import annotations

from dataclasses import dataclass

from . import pricing
from .parse import Intent

SPOT_MULTIPLIER = 0.30   # rough: spot ~70% off on-demand (approx, varies by AZ/type)


@dataclass
class Estimate:
    hourly: float | None      # $/hr per unit; None => unknown/hidden (a risk, not a pass)
    qty: int
    resource: str
    known: bool
    region: str = "us-east-1"
    note: str = ""

    @property
    def total_hourly(self) -> float | None:
        return None if self.hourly is None else self.hourly * self.qty

    @property
    def monthly(self) -> float | None:
        h = self.total_hourly
        return None if h is None else h * pricing.HOURS_PER_MONTH


def estimate(intent: Intent) -> Estimate:
    label = (f"{intent.qty}× {intent.service} {intent.sku}" if intent.sku
             else f"{intent.service} {intent.action}")

    if intent.dry_run:
        return Estimate(0.0, 1, f"{intent.service} {intent.action} (dry-run)",
                        True, intent.region, "dry-run — no charge")

    if not intent.priceable:
        return Estimate(None, intent.qty, f"{intent.service} {intent.action}",
                        False, intent.region, intent.reason or "params hidden")

    if intent.table == "flat":
        rate = pricing.lookup_flat(intent.sku or "")
        return Estimate(rate, 1, intent.sku or "resource", rate is not None, intent.region)

    if intent.table == "ebs":                    # qty carries volume size in GB
        rate = pricing.ebs_hourly(intent.sku or "gp3", intent.qty)
        return Estimate(rate, 1, f"{intent.qty}GB {intent.sku} EBS volume",
                        rate is not None, intent.region, "storage $/GB-mo")

    if intent.table == "unknown":
        return Estimate(None, intent.qty, label, False, intent.region,
                        intent.reason or "unrecognized resource")

    rate = pricing.lookup(intent.table, intent.sku or "", intent.region)
    note = ""
    if rate is not None and intent.spot:
        rate = round(rate * SPOT_MULTIPLIER, 4)
        note = "spot (~70% off, approx)"
    # region caveat only applies to the region-agnostic static table; the live
    # provider prices the actual region, so no caveat there.
    if (rate is not None and intent.region != "us-east-1"
            and pricing.active_provider().name == "static"):
        note = (note + "; " if note else "") + "us-east-1 price estimate"
    return Estimate(rate, intent.qty, label, rate is not None, intent.region, note)
