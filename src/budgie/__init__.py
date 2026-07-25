"""budgie — a spend firewall for AI agents. Prices a cloud command before it
runs and blocks the over-budget ones."""

__version__ = "0.1.1"

from . import parse as _parse
from .estimate import estimate as _estimate
from .gate import aggregate, Decision

_IAC_MARKERS = (
    "terraform apply",
    "terraform destroy",
    "pulumi up",
    "cloudformation deploy",
    "cloudformation create-stack",
    "cdk deploy",
    "sam deploy",
    "serverless deploy",
    "sls deploy",
    "eksctl create",
)


def check(command: str, cap_hourly: float = 2.0) -> Decision:
    """Parse every spend invocation in a bash string, price each, and gate."""
    intents = _parse.extract(command)
    if intents:
        unbounded = any(i.unbounded for i in intents)
        return aggregate([_estimate(i) for i in intents], cap_hourly, unbounded=unbounded)
    # No parseable spend command. IaC applies have no CLI SKU -> warn, don't allow.
    lowered = command.strip().lower()
    if any(m in lowered for m in _IAC_MARKERS):
        from .gate import strict_mode

        msg = ("IaC apply detected — cost needs plan analysis (Infracost / AWS Pricing "
               "MCP). Don't auto-approve unreviewed.")
        if strict_mode():
            return Decision("block", msg + " Blocked (BUDGIE_STRICT).")
        return Decision("warn", msg)
    return Decision("allow", "not a spend command")
