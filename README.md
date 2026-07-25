# 🐦 budgie

**A spend firewall for AI agents.** budgie is a Claude Code hook that prices an
agent's cloud command *before it runs* — folding in **storage, node groups, whole
loops, and the session's cumulative burn**, not just the headline instance — and
blocks the ones that breach your budget. Billing alerts fire after the money's
gone; budgie stops the command at the door.

[![tests](https://github.com/TanishkaMarrott/budgie/actions/workflows/ci.yml/badge.svg)](https://github.com/TanishkaMarrott/budgie/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![deps](https://img.shields.io/badge/core%20deps-zero-brightgreen.svg)

![a live Claude Code session — the agent runs an aws command and budgie blocks it before it executes](docs/wrap-demo.gif)

*Live: the agent tried to launch **3× c5.4xlarge** (~$1,489/mo) and budgie **blocked it before it ran** — over the session's cap.* One command puts the gate in front of a real agent:

```bash
budgie wrap claude     # launches Claude Code with budgie guarding every command
```

The depth is in what it *sees*: a **cheap** `db.t3.micro` is blocked not for the
instance but for the **20 TB of storage** attached to it — the difference between
"block the big box" and understanding what a command actually costs.

## Architecture — a gate and a ledger, kept apart

budgie is two hooks with one discipline between them: **anticipate to decide,
account to record — and never let the two cross.**

```
  agent (Claude Code) ── Bash: aws / terraform / gcloud …
        │
        ├──────────────── PreToolUse ──────────────────┐   the GATE — anticipation
        │  budgie hook                                  │   prices a resource that
        │    parse   every aws cmd (loops, &&, ;, xargs)│   doesn't exist yet; the
        │    price   static table | AWS Price List API  │   estimate is used only to
        │    gate    accrued + running + this cmd > cap?│   decide, then discarded —
        │      ├─ over  → exit 2  ✗  blocked (never runs)   it commits NOTHING
        │      └─ under → allow (exit 0), command runs  │
        │                                               ▼
        │                       session ledger  ($BUDGIE_HOME)
        │                       active_rate $/hr · accrued_cost $
        │                                               ▲
        └──────────────── PostToolUse ─────────────────┘   the LEDGER — accounting
           budgie posthook                                 fires ONLY on success, so
             create succeeded → COMMIT its cost + record id  only real spend is ever
             delete succeeded → credit the cost back         written down

  * pricing via AWS Price List API is opt-in: pip install "budgie-firewall[aws]"
```

**Why the split matters.** The gate *anticipates* — it prices a command that
hasn't run and may be blocked, fail, or be a dry-run, so it must not write
anything down. The ledger *accounts* — Claude Code fires PostToolUse only when a
command **succeeds**, so a create counts only once it's real; a failed create
fires no hook and never touches the total. (Verified live on Claude Code 2.1.121:
a failed command fires *no* PostToolUse — and no `PostToolUseFailure` either.)
Keeping a prediction out of the factual ledger is what stops **phantom spend** —
money that was never spent — from wrongly blocking later, legitimate commands.

The gate itself (`command → parse → price → verdict`) is a pure function over the
stdlib. Pricing and the ledger are seams around it.

---

## Why

Unsupervised agents now run real `aws` / `terraform` / `gcloud` commands — and
they loop, retry, and over-provision. One went viral for running up a **$6,531
AWS bill**. Every existing guardrail is too late or too coarse:

- **Billing alerts / FinOps dashboards** (Vantage, CloudZero) — report *after* the spend.
- **AWS Budgets** — fires *after* cumulative spend crosses a line, org-wide, blocks a whole service.
- **Infracost** — great, but *CI/PR-time* and Terraform-only.
- **Token-budget tools** (Waxell, loop-cost) — cap *LLM tokens*, not *infra*.

**None block the specific money-spending command, at agent runtime, before it runs.** That's budgie.

## The decision

```
agent about to run:  aws ec2 run-instances --instance-type p5.48xlarge --count 2
        │
        ▼   PreToolUse hook (budgie) — no execution, no network
   parse → find every aws command (loops, &&, ;, xargs, /path/aws)
   price → 2 × $98.32/hr = $196.64/hr  ($143,547/mo)
   gate  → dollars spent + running + this command  >  the cap?
        │
        ├─ yes → exit code 2  ✗   Claude Code blocks it; the command never runs
        └─ no  → allow (exit 0); the command runs, and PostToolUse commits its
                 cost to the session — only once it has actually succeeded
```

budgie **inspects** and decides allow/deny — it never runs the command itself
(that stays with the agent, only if budgie allows). A hard block uses **exit
code 2** (the version-proof deny); a *warn* is a non-blocking hint; *allow* is
silent. No AWS SDK, no network on the decision path.

## Session accrual (cumulative cost)

budgie keeps two **separate** numbers per session — the current burn and the
money already spent — because a rate is not a cost:

- **`active_rate` ($/hr)** — what the session is burning *right now*. A create
  raises it; a teardown lowers it.
- **`accrued_cost` ($)** — the *time-integral* of the burn: the area under the
  active-rate line.

```
on every event (create / teardown):
    accrued_cost += active_rate × (now − last_ts)   # Δt capped at 24h
    last_ts       = now
    create   → active_rate += resource_rate
    teardown → active_rate −= resource_rate          # past dollars stay accrued

 active_rate $/hr
   5 │                 ┌──────────────┐
     │                 │  burning $5  │
   3 │        ┌────────┘              └────────┐        ← teardown A: rate drops,
     │  $3    │            $5                $2 │          accrued does NOT
   0 ┼────────┘                                └──────────▶  time
     create A     create B              teardown A
                                                     accrued = Σ (rate × Δt)
```

Both are **enforced**, and you choose which:
- `BUDGIE_HOURLY` checks **`active_rate`** (max concurrent burn) — two
  individually-cheap boxes can still trip it together.
- `BUDGIE_BUDGET` checks the **cumulative total** — `accrued_cost` (real dollars
  spent) plus what's still running, projected over `BUDGIE_HORIZON`. Because
  torn-down resources keep their spent dollars in `accrued_cost`, create/tear-down
  churn is counted, not laundered.

`budgie session` prints both. This mirrors KML's `cost_estimator` accrual — rate
and cost kept distinct, with active vs torn-down resources tracked separately.

## Install

> **Name:** the PyPI package is **`budgie-firewall`** and the command is `budgie`.
> (The bare `budgie` name on PyPI is an unrelated 2015 SSH tool — not this project.)

```bash
# from PyPI (once published) — package is budgie-firewall, the command is budgie
uvx --from budgie-firewall budgie check "aws ec2 run-instances --instance-type p5.48xlarge --count 2"
# [BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr cap.

# or straight from GitHub, no PyPI needed:
uvx --from git+https://github.com/TanishkaMarrott/budgie budgie check "aws ec2 run-instances --instance-type p5.48xlarge"
```

**Fastest path — no settings edit:** `budgie wrap claude` launches Claude Code with
both hooks injected for that session (via its `--settings` channel), so you can try
budgie in one command.

To wire it in permanently, add **both** hooks — PreToolUse blocks before the spend,
PostToolUse commits it after success (required for cumulative budgets):

```jsonc
// .claude/settings.json
{ "hooks": {
    "PreToolUse":  [ { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "budgie hook" } ] } ],      // the gate — blocks (exit 2)
    "PostToolUse": [ { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "budgie posthook" } ] } ]   // the ledger — commits succeeded spend
} }
```

Two caps, use either or both:

- **`BUDGIE_HOURLY=2.0`** — a **rate** ceiling: never let the session burn faster
  than $2/hr at once.
- **`BUDGIE_BUDGET=1.0`** — a **cumulative total**: the net sum of dollars already
  spent plus everything still running (projected over `BUDGIE_HORIZON`, default
  1h) may never cross $1. This is what catches slow accrual and create/tear-down
  churn that a rate cap alone misses.

**No silent allow.** Anything that provisions but can't be priced — an un-enumerated
service, an unknown SKU, a hidden config — **warns**, never falls through to allow;
a dynamic quantity (`--count $N`) or an unbounded loop **blocks**. Flip `BUDGIE_STRICT=1`
to turn every can't-price warn into a hard block (the zero-escape-boat posture).

## Configuration

Every knob is an environment variable; all are optional.

| Variable | Default | What it does |
|---|---|---|
| `BUDGIE_HOURLY` | `2.0` | **Rate** ceiling ($/hr) — the session may never burn faster than this at once. |
| `BUDGIE_BUDGET` | *off* | **Cumulative total** ($) — dollars spent + still-running, projected over the horizon. Needs **both** hooks wired. |
| `BUDGIE_HORIZON` | `1.0` | Hours the running rate is projected over for `BUDGIE_BUDGET`. |
| `BUDGIE_STRICT` | *off* | `1` escalates every *can't-price* **warn → block** — nothing unpriceable runs. |
| `BUDGIE_FAIL` | `closed` | On internal error / corrupt state: `closed` blocks spend, `open` allows through. |
| `BUDGIE_OK` | *off* | `1` overrides the gate for the command (also `.budgie/allow.txt`, a substring allowlist). |
| `BUDGIE_PRICING` | `static` | `static` = bundled offline table; `aws` = live Price List API (`pip install "budgie-firewall[aws]"`). |
| `BUDGIE_HOME` | `.budgie` | Directory for the session ledger + price cache. |

## Commands

```
budgie check "<command>"     # price + gate one command
budgie hook                  # PreToolUse hook entry — prices + gates (exit 2 blocks)
budgie posthook              # PostToolUse hook — commits succeeded spend, credits deletes
budgie tf-plan plan.json     # price a `terraform show -json` plan
budgie session               # show each session's burn ($/hr) and accrued ($)
budgie ledger                # recent decisions + total spend stopped
budgie wrap claude           # launch Claude Code with budgie's hooks injected (no settings edit)
```

## What it does today

- ✅ **Composite pricing** — RDS = instance **+ storage + Multi-AZ**; EKS = control
  plane **+ node groups**; EBS volumes ($/GB-mo). Plus EC2, NAT, ElastiCache,
  Redshift, ELB, SageMaker, OpenSearch. **Warns** (never silent-allow) when cost is
  hidden — Fargate, Aurora, EMR, MSK, `--cli-input-json`, `terraform apply`.
- ✅ Extracts **every** `aws` invocation from **loops / `&&` / `;` / xargs / full
  paths**, including ones **behind leading global flags** (`aws --region … --profile …
  ec2 run-instances`) and **inside `$(…)` / backticks** — the common forms that must
  not slip past. **Disambiguates service-ambiguous actions**: only `eks create-cluster`
  is the flat $0.10 control plane; `kafka`/`emr create-cluster` **warn** (bill-shock),
  `ecs create-cluster` is free. Handles `--dry-run`, **spot** (~70% off), **region**,
  robust `--count 1:5`.
- ✅ **Prices the whole loop, not one iteration** — `for i in $(seq 100); do …`
  is costed as 100×, so the $6,531 runaway-loop pattern **blocks**. A loop with no
  bounded count (`while` / `xargs` / a dynamic range) that creates billable
  resources is refused outright — cost can't be bounded.
- ✅ **Blocks via exit code 2** (version-proof hard deny); warns are non-blocking
  hints; allow is silent. Crash-proof, **fail-closed** on spend commands.
- ✅ **Two enforced caps** — a **rate** ceiling (`BUDGIE_HOURLY`, $/hr) *and* a
  **cumulative total** (`BUDGIE_BUDGET`, $): the net sum of dollars spent + running,
  projected over a horizon. Rate and accrued cost are tracked *separately* (the KML
  model); `budgie session` shows both.
- ✅ **PostToolUse accounting** — commits a create's cost only once it **succeeds**,
  records the resource id, and **credits it back** when the agent deletes it. A
  failed create fires no hook, so it never counts — and as a belt-and-braces check it
  also **refuses to commit** an interrupted command or one whose output carries a
  botocore error, so no phantom spend even if a future version fires PostToolUse on
  failure. Resources are recorded by their **user-assigned identifier** too, so a
  teardown still credits back under `--output text`. A **resize** (`modify-*`) is
  gated but not re-committed (no double-count). No AWS polling.
- ✅ **Durable session state** — the budget ledger is written **atomically**
  (`os.replace`) and guarded by a **file lock**, so a crash mid-write or two parallel
  hooks can't corrupt it or lose a commit. A ledger that *can't* be read **fails
  closed** (blocks spend) rather than silently resetting the budget to $0.
- ✅ **Terraform** — `budgie tf-plan` prices a `terraform show -json` plan.
- ✅ **Ledger** + **override** (`BUDGIE_OK=1` / `.budgie/allow.txt`).
- ✅ **Live AWS pricing** — region-aware, disk-cached (AWS Price List API); zero-dep
  static table is the default.

## Roadmap

- **EC2 root/data volumes** — fold `--block-device-mappings` EBS into the instance
  estimate (RDS storage + EKS nodes are already composite).
- **Out-of-band deletes** — reconciliation is agent-driven; console / TTL /
  autoscaling deletes would need resource-snapshot polling (a platform's job).
- **Multi-cloud** — GCP / Azure pricing tables.
- **Hard enforcement** — mint a scoped credential / SCP from the budget (the
  un-bypassable tier).

## Limitations (honest)

budgie is a **fast advisory guard**, not an un-bypassable control. Know its edges:

- **Bash-tool only.** An agent using an AWS **MCP server** (boto3 directly)
  bypasses it. The un-bypassable tier is a scoped credential / SCP minted from the
  budget — on the roadmap.
- **Cumulative budgets need both hooks.** `BUDGIE_BUDGET` accrues through the
  PostToolUse hook, so wire **both** `budgie hook` and `budgie posthook` (the setup
  above does). PreToolUse alone still enforces the per-command rate cap, but won't
  accumulate spend across commands.
- **EKS nodegroup counts `desiredSize`** (or 1 when only `min`/`max` is given);
  autoscaling *beyond* desired isn't priced.
- **Composite only where the data's in the command.** RDS storage/Multi-AZ and EKS
  node groups are folded in; an EC2 instance's own EBS volumes
  (`--block-device-mappings`) are **not yet**, so a big root volume can be
  under-counted. The cumulative session total partly compensates.
- **Agent-driven reconciliation.** budgie credits deletes it *sees* (the agent's
  own commands). A delete done from the console / by TTL / by autoscaling won't be
  caught without snapshot polling — out of scope for a hook.
- **Provisioning cost, not usage cost.** Usage-based services — S3, Lambda,
  DynamoDB on-demand, data transfer, NAT data processing — can't be known before
  the fact and are not priced.
- **Estimates, not invoices.** It ignores Reserved Instances / Savings Plans; spot
  is a rough ~70% off. A covered account may see over-estimates.
- **Curated *pricing*, but no silent allow.** The static table prices the big
  bill-shock services exactly; anything else that **provisions** (an unknown SKU, an
  un-enumerated service, a hidden config) still **warns** rather than passing through
  — only genuinely free creates (security groups, tags, IAM, log groups…) are silent.
  So coverage gaps cost you a warning to review, never an unnoticed create.

## License

MIT
