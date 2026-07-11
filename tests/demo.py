"""Run real commands through the parse->price->gate pipeline. This IS the spike:
if these verdicts are right, the thesis holds.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check

CAP = 2.0  # $/hr session cap

COMMANDS = [
    'aws ec2 run-instances --instance-type p5.48xlarge --count 2',   # GPU x2 -> bill bomb
    'aws ec2 run-instances --instance-type t3.micro',                # cheap -> allow
    'aws ec2 run-instances --instance-type m5.4xlarge --count 4',    # 4x mid -> block
    'aws rds create-db-instance --db-instance-class db.r5.24xlarge',  # huge db -> block
    'aws ec2 create-nat-gateway --subnet-id subnet-123',             # flat cheap -> allow
    'aws eks create-cluster --name prod',                            # flat -> allow
    'aws ec2 run-instances --instance-type c5.9xlarge',              # ~1.53 -> warn (>cap/2)
    'aws ec2 run-instances --instance-type z9.hypergiant',           # unknown SKU -> warn
    'aws s3 ls',                                                     # not a spend cmd -> allow
    'terraform apply -auto-approve',                                # needs plan path (spike: pass)
]

TAG = {"block": "\033[91mBLOCK\033[0m", "warn": "\033[93mWARN \033[0m", "allow": "\033[92mALLOW\033[0m"}

print(f"session cap = ${CAP:.2f}/hr\n" + "=" * 78)
for cmd in COMMANDS:
    d = check(cmd, CAP)
    print(f"[{TAG[d.verdict]}] {cmd}")
    print(f"         → {d.reason}\n")
