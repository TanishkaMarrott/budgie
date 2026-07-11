# 🐦 budgie

**A spend firewall for AI agents.** budgie sits on your agent's shoulder and
prices every cloud command *before it runs* — then blocks the ones that would
blow your budget. Billing alerts tell you after the money's gone; budgie stops
it at the door.

> *"The little bird that stops your AI agent's big bill."*

![budgie blocking a $143,547/mo command before it runs](docs/demo.gif)

## Demo

```console
$ budgie check 'aws ec2 run-instances --instance-type p5.48xlarge --count 2'
[BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr session cap. Blocked.

$ budgie check 'for i in $(seq 100); do aws ec2 run-instances --instance-type p5.48xlarge; done'
[BLOCK] 1× ec2 p5.48xlarge ≈ $98.32/hr ($71,774/mo) — over the $2.00/hr session cap. Blocked.

$ budgie check 'aws rds create-db-instance --db-instance-class db.r5.24xlarge'
[BLOCK] 1× rds db.r5.24xlarge ≈ $11.52/hr ($8,410/mo) — over the $2.00/hr session cap. Blocked.

$ budgie check 'aws ec2 run-instances --instance-type t3.micro'
[ALLOW] ≈ $0.01/hr — within budget.
```

Record the animated version with [`vhs`](https://github.com/charmbracelet/vhs): `vhs demo/demo.tape` → writes `docs/demo.gif`.

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
# from PyPI (once published)
uvx budgie check "aws ec2 run-instances --instance-type p5.48xlarge --count 2"
# [BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr cap.

# or straight from GitHub, no PyPI needed:
uvx --from git+https://github.com/TanishkaMarrott/budgie budgie check "aws ec2 run-instances --instance-type p5.48xlarge"
```

Wire it as a `PreToolUse` hook (blocks before the spend):

```jsonc
// .claude/settings.json
{ "hooks": { "PreToolUse": [
    { "matcher": "Bash", "hooks": [
        { "type": "command", "command": "budgie hook" } ] } ] } }
```

Set the cap with `BUDGIE_HOURLY=2.0`. Live AWS pricing (full SKU coverage):
`pip install budgie[aws]` and `BUDGIE_PRICING=aws`.

## Commands

```
budgie check "<command>"     # price + gate one command
budgie hook                  # PreToolUse hook entry (reads JSON on stdin)
budgie tf-plan plan.json     # price a `terraform show -json` plan
budgie session               # show each session's burn ($/hr) and accrued ($)
budgie ledger                # recent decisions + total spend stopped
```

## What it does today

- ✅ **Prices** the AWS services that cause most bill-shocks — EC2, RDS (+ resize),
  EKS, NAT, ElastiCache, Redshift, ELB, **EBS volumes** (storage $/GB-mo),
  **SageMaker**, **OpenSearch**. **Warns** (never silent-allow) when the cost is
  hidden — Fargate, Aurora, EMR, MSK, `--cli-input-json`, `terraform apply`.
- ✅ Extracts **every** `aws` invocation from **loops / `&&` / `;` / xargs / full
  paths**; handles `--dry-run`, **spot** (~70% off), **region**, robust `--count 1:5`.
- ✅ **Blocks via exit code 2** (version-proof hard deny); warns are non-blocking
  hints; allow is silent. Crash-proof, **fail-closed** on spend commands.
- ✅ **Cumulative session budget** — tracks the session's **active run-rate ($/hr)**
  and **accrued cost ($ = rate × time)** *separately*; teardown lowers the burn
  while past dollars stay. `budgie session` shows both.
- ✅ **Terraform** — `budgie tf-plan` prices a `terraform show -json` plan.
- ✅ **Ledger** + **override** (`BUDGIE_OK=1` / `.budgie/allow.txt`).
- ✅ **Live AWS pricing** — region-aware, disk-cached (AWS Price List API); zero-dep
  static table is the default.

## Roadmap

- **Composite pricing** — fold attached storage (RDS `--allocated-storage`, an
  instance's EBS volumes) and EKS worker nodes into the per-command estimate.
- **Auto-teardown reconciliation** — discover session-tagged resources (Resource
  Groups Tagging API) to credit the burn back automatically on delete.
- **Multi-cloud** — GCP / Azure pricing tables.
- **Hard enforcement** — mint a scoped credential / SCP from the budget (the
  un-bypassable tier).

## Limitations (honest)

budgie is a **fast advisory guard**, not an un-bypassable control. Know its edges:

- **Bash-tool only.** An agent using an AWS **MCP server** (boto3 directly)
  bypasses it. The un-bypassable tier is a scoped credential / SCP minted from the
  budget — on the roadmap.
- **Per-command, not composite (yet).** It prices the resource *in the command*.
  Attached storage (RDS allocated storage, an instance's EBS volumes) and dependent
  resources (EKS worker nodes) often arrive in *separate* commands, so a single
  storage-/node-heavy resource can be **under-estimated**. The cumulative session
  total partly compensates as those separate commands add up.
- **Provisioning cost, not usage cost.** Usage-based services — S3, Lambda,
  DynamoDB on-demand, data transfer, NAT data processing — can't be known before
  the fact and are not priced.
- **Estimates, not invoices.** It ignores Reserved Instances / Savings Plans; spot
  is a rough ~70% off. A covered account may see over-estimates.
- **Curated coverage.** It prices/warns the big bill-shock services; free creates
  (security groups, tags) are intentionally silent.

## License

MIT
