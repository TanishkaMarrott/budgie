"""Loop pricing — the $6,531 pattern. A loop body runs N times, so its cost is
N × per-iteration; an unbounded loop can't be priced and is refused."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budgie import check
from budgie.parse import _loop_multiplier, extract


def v(cmd, cap=2.0):
    return check(cmd, cap).verdict


# --- multiplier detection ---
def test_seq_n():
    assert _loop_multiplier("for i in $(seq 100); do aws ec2 run-instances; done") == 100


def test_seq_a_b():
    assert _loop_multiplier("for i in $(seq 5 14); do aws x; done") == 10


def test_seq_a_step_b():
    assert _loop_multiplier("for i in $(seq 0 2 10); do aws x; done") == 6


def test_brace_range():
    assert _loop_multiplier("for i in {1..10}; do aws x; done") == 10


def test_literal_list():
    assert _loop_multiplier("for i in a b c d; do aws x; done") == 4


def test_while_is_unbounded():
    assert _loop_multiplier("while true; do aws x; done") is None


def test_xargs_is_unbounded():
    assert _loop_multiplier("cat hosts | xargs -I{} aws x") is None


def test_dynamic_range_is_unbounded():
    assert _loop_multiplier("for h in $(cat hosts); do aws x; done") is None


# --- quantity is multiplied through to the estimate ---
def test_qty_multiplied():
    its = extract("for i in $(seq 50); do aws ec2 run-instances --instance-type t3.micro; done")
    assert its and its[0].qty == 50


def test_count_and_loop_compound():
    # 3 iterations × --count 2 = 6 instances
    its = extract("for i in 1 2 3; do aws ec2 run-instances --instance-type m5.large --count 2; done")
    assert its and its[0].qty == 6


# --- gate outcomes ---
def test_cheap_loop_blocks_when_it_adds_up():
    # 100 × t3.large ($0.0832) = $8.32/hr — over a $2 cap
    assert v("for i in $(seq 100); do aws ec2 run-instances --instance-type t3.large; done") == "block"


def test_small_bounded_loop_still_allowed():
    # 3 × t3.micro ≈ $0.03/hr — genuinely under budget, must not over-block
    assert v("for i in 1 2 3; do aws ec2 run-instances --instance-type t3.micro; done") == "allow"


def test_unbounded_loop_with_spend_blocks():
    assert v("while true; do aws ec2 run-instances --instance-type t3.micro; done") == "block"


def test_unbounded_delete_loop_is_noop():
    # terminate-instances isn't a priceable create — an xargs delete loop must not block
    assert v("cat ids | xargs -I{} aws ec2 terminate-instances --instance-ids {}") == "allow"
