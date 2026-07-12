"""Real regression suite for the parser + gate — the moat. Every MUST-fix edge
case from the lead review lives here so it can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check


def v(cmd, cap=2.0):
    return check(cmd, cap).verdict


# --- core price + gate ---
def test_block_big_gpu():
    assert v("aws ec2 run-instances --instance-type p5.48xlarge --count 2") == "block"

def test_allow_cheap():
    assert v("aws ec2 run-instances --instance-type t3.micro") == "allow"

def test_count_multiplies():
    assert v("aws ec2 run-instances --instance-type m5.4xlarge --count 4") == "block"

def test_max_count():
    assert v("aws ec2 run-instances --instance-type m5.4xlarge --max-count 4") == "block"


# --- MUST-FIX #2: robust quantity, no crash ---
def test_count_range_no_crash():
    # --count 1:5 must not throw; qty=5 -> m5.large 0.096*5=0.48 -> allow
    assert v("aws ec2 run-instances --instance-type m5.large --count 1:5") == "allow"

def test_garbage_count_no_crash():
    assert v("aws ec2 run-instances --instance-type t3.micro --count notanumber") == "allow"


# --- MUST-FIX #1: embedded commands (loops / chains) no longer slip through ---
def test_loop_is_caught():
    cmd = "for i in 1 2 3; do aws ec2 run-instances --instance-type p5.48xlarge; done"
    assert v(cmd) == "block"          # was ALLOW before the fix — the $6,531 scenario

def test_loop_prices_every_iteration():
    # cheap per-iter, but 100 of them = $8.32/hr — must BLOCK, not just warn.
    cmd = "for i in $(seq 100); do aws ec2 run-instances --instance-type t3.large; done"
    assert v(cmd) == "block"          # was WARN before the loop-multiplier fix

def test_chain_and():
    assert v("aws s3 ls && aws ec2 run-instances --instance-type m5.24xlarge") == "block"

def test_semicolon_two_commands():
    cmd = ("aws ec2 run-instances --instance-type t3.micro; "
           "aws rds create-db-instance --db-instance-class db.r5.24xlarge")
    assert v(cmd) == "block"          # summed: tiny + huge db


# --- MUST-FIX #1: hidden params fail safe (not silent allow) ---
def test_cli_input_json_not_allowed():
    assert v("aws ec2 run-instances --cli-input-json file://big.json") == "warn"


# --- MUST-FIX #4: dry-run / spot / region ---
def test_dry_run_allowed():
    assert v("aws ec2 run-instances --instance-type p5.48xlarge --dry-run") == "allow"

def test_spot_lowers_verdict():
    on = v("aws ec2 run-instances --instance-type c5.9xlarge")                 # 1.53 -> warn
    sp = v("aws ec2 run-instances --instance-type c5.9xlarge "
           "--instance-market-options MarketType=spot")                        # ~0.46 -> allow
    assert on == "warn" and sp == "allow"

def test_region_noted():
    d = check("aws ec2 run-instances --instance-type m5.large --region ap-south-1")
    assert "us-east-1 price estimate" in d.reason


# --- safety / pass-through ---
def test_unknown_sku_warns():
    assert v("aws ec2 run-instances --instance-type z9.hypergiant") == "warn"

def test_non_spend_allow():
    assert v("aws s3 ls") == "allow"
    assert v("git commit -m x") == "allow"

def test_iac_warn():
    assert v("terraform apply -auto-approve") == "warn"
