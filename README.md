# 🐦 budgie

**A spend firewall for AI agents.** budgie is a `PreToolUse` hook that prices an
agent's cloud command *before it runs* — folding in **storage, node groups, and
the session's cumulative burn**, not just the headline instance — and blocks the
ones that breach your budget. Billing alerts fire after the money's gone; budgie
stops the command at the door.

[![tests](https://github.com/TanishkaMarrott/budgie/actions/workflows/ci.yml/badge.svg)](https://github.com/TanishkaMarrott/budgie/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![deps](https://img.shields.io/badge/core%20deps-zero-brightgreen.svg)

![budgie pricing an agent's command before it runs — a cheap db class blocked by 20 TB of storage, node groups priced, and the session's live burn](docs/demo.gif)

The depth is in what it *sees*: below, a **cheap** `db.t3.micro` is blocked — not
for the instance, but for the **20 TB of storage** attached to it. That's the
difference between "block the big box" and understanding what a command costs.

## Demo

```console
$ budgie check 'aws rds create-db-instance --db-instance-class db.t3.micro --allocated-storage 20000 --storage-type gp2'
[BLOCK] 1× rds db.t3.micro ≈ $3.17/hr ($2,312/mo) — over the $2.00/hr session cap. Blocked.  [+20000GB gp2]

$ budgie check 'aws eks create-nodegroup --instance-types m5.24xlarge --scaling-config desiredSize=40'
[BLOCK] 40× eks m5.24xlarge ≈ $184.32/hr ($134,554/mo) — over the $2.00/hr session cap. Blocked.

$ budgie check 'aws ec2 run-instances --instance-type p5.48xlarge --count 2'
[BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr session cap. Blocked.

$ budgie check 'aws ec2 run-instances --instance-type t3.micro'
[ALLOW] ≈ $0.01/hr — within budget.

$ budgie session
  agent-8f2: burning $4.61/hr  ·  accrued $13.83 so far
```

Regenerate the animation with [`vhs`](https://github.com/charmbracelet/vhs): `vhs demo/demo.tape` → writes `docs/demo.gif`.

## Architecture

```
  agent (Claude Code) ── Bash: aws / terraform / gcloud …
        │
        ├──────────────── PreToolUse ─────────────────┐
        │  budgie hook                                 │
        │    parse   find every aws cmd (loops, &&, ;) │  ← pure function,
        │    price   static table | AWS Price List API │    no execution,
        │    gate    active_rate + this cmd > cap?      │    no network*
        │      ├─ over  → exit 2   ✗  command blocked  │
        │      └─ under → allow, commit rate to session │
        │                                              ▼
        │                          session ledger  ($BUDGIE_HOME)
        │                          active_rate $/hr · accrued_cost $
        │                                              ▲
        └──────────────── PostToolUse ────────────────┘
           budgie posthook
             reconcile   record created ids; on delete or
                         failed-create, credit the burn back

  * live pricing (AWS Price List API) is opt-in: pip install "budgie-firewall[aws]"
```

The core — `command → parse → price → verdict` — is a pure function over stdlib
only. Pricing and reconciliation are seams around it.

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

## How it works

```
agent about to run:  aws ec2 run-instances --instance-type p5.48xlarge --count 2
        │
        ▼   PreToolUse hook (budgie)
   parse → find every aws command (loops, &&, ;, /path/aws)   [no execution]
   price → 2 × $98.32/hr = $196.64/hr  ($143,547/mo)
   add to the session's active run-rate → over the $2/hr cap?
        │
        ├─ yes → exit code 2  ✗   Claude Code blocks it; the command never runs
        └─ no  → allow (exit 0) → commit the rate to the session
```

budgie **inspects** the command and decides allow/deny — it never runs the
command itself (that stays with the agent, only if budgie allows). A hard block
uses **exit code 2** (the version-proof deny); a *warn* is a non-blocking hint;
*allow* is silent. The core is a pure function: `command → parse → price →
verdict`. No AWS SDK, no network.

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

The **cap is checked against `active_rate`** (max concurrent burn), so two
individually-cheap boxes can still trip it together; `accrued_cost` is the real
dollars spent so far and projects to expiry. `budgie session` prints both.
This mirrors KML's `cost_estimator` accrual — rate and cost kept distinct, with
active vs torn-down resources tracked separately.

## Install

```bash
# from PyPI (once published) — package is budgie-firewall, the command is budgie
uvx --from budgie-firewall budgie check "aws ec2 run-instances --instance-type p5.48xlarge --count 2"
# [BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr cap.

# or straight from GitHub, no PyPI needed:
uvx --from git+https://github.com/TanishkaMarrott/budgie budgie check "aws ec2 run-instances --instance-type p5.48xlarge"
```

Wire it as a `PreToolUse` hook (blocks before the spend):

```jsonc
// .claude/settings.json
{ "hooks": {
    "PreToolUse":  [ { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "budgie hook" } ] } ],
    "PostToolUse": [ { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "budgie posthook" } ] } ]   // keeps the burn honest
} }
```

Set the cap with `BUDGIE_HOURLY=2.0`. Live AWS pricing (full SKU coverage):
`pip install "budgie-firewall[aws]"` and `BUDGIE_PRICING=aws`.

## Commands

```
budgie check "<command>"     # price + gate one command
budgie hook                  # PreToolUse hook entry — prices + gates (exit 2 blocks)
budgie posthook              # PostToolUse hook — reconciles the session burn
budgie tf-plan plan.json     # price a `terraform show -json` plan
budgie session               # show each session's burn ($/hr) and accrued ($)
budgie ledger                # recent decisions + total spend stopped
```

## What it does today

- ✅ **Composite pricing** — RDS = instance **+ storage + Multi-AZ**; EKS = control
  plane **+ node groups**; EBS volumes ($/GB-mo). Plus EC2, NAT, ElastiCache,
  Redshift, ELB, SageMaker, OpenSearch. **Warns** (never silent-allow) when cost is
  hidden — Fargate, Aurora, EMR, MSK, `--cli-input-json`, `terraform apply`.
- ✅ Extracts **every** `aws` invocation from **loops / `&&` / `;` / xargs / full
  paths**; handles `--dry-run`, **spot** (~70% off), **region**, robust `--count 1:5`.
- ✅ **Blocks via exit code 2** (version-proof hard deny); warns are non-blocking
  hints; allow is silent. Crash-proof, **fail-closed** on spend commands.
- ✅ **Cumulative session budget** — tracks the session's **active run-rate ($/hr)**
  and **accrued cost ($ = rate × time)** *separately*. `budgie session` shows both.
- ✅ **PostToolUse reconciliation** — records created resource ids and **credits the
  burn back** when the agent deletes them (or when a create fails); teardown does
  the same. No AWS polling.
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
- **Curated coverage.** It prices/warns the big bill-shock services; free creates
  (security groups, tags) are intentionally silent.

## License

MIT
