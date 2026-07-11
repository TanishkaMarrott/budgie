# 🐦 budgie

**A spend firewall for AI agents.** budgie sits on your agent's shoulder and
prices every cloud command *before it runs* — then blocks the ones that would
blow your budget. Billing alerts tell you after the money's gone; budgie stops
it at the door.

> *"The little bird that stops your AI agent's big bill."*

<!-- ![demo](demo.gif)  ← the clip: agent runs `aws ec2 run-instances p5.48xlarge ×2`, budgie blocks a $143,547/mo command before it executes -->

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
   parse the command → SKU + region + qty      (no execution — pure string→price)
   price it:  2 × $98.32/hr = $196.64/hr  ($143,547/mo)
   over the $2/hr session cap?  → DENY  ✗   the command never runs
        │
        ▼   under budget → allow; created resources tagged budgie:session=<id>
        ▼   session end → tear down exactly what this session created  ("$X spent, 0 orphans")
```

budgie **inspects** the command and decides allow/deny — it never runs the
command itself (that stays with the agent, only if budgie allows). The core is a
pure function: `command → parse → price → verdict`. No AWS SDK, no network.

## Install

```bash
uvx budgie check "aws ec2 run-instances --instance-type p5.48xlarge --count 2"
# [BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr cap.
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

## What it does today

- ✅ Prices imperative AWS commands that cause most bill-shocks (EC2, RDS, EKS, NAT, ElastiCache, Redshift, ELB); extracts them from **loops / `&&` / `;` / xargs** (not just lines starting with `aws`); fails **safe** on unknown SKUs, `--cli-input-json`, and IaC applies.
- ✅ Handles `--dry-run` (no charge), **spot** (~70% off), **region**, robust quantities (`--count 1:5`).
- ✅ **Cumulative session budget** — the cap is checked against everything committed this session, not just one command.
- ✅ **Ledger** (`budgie ledger`) — every decision + "spend stopped" total.
- ✅ **Override** — `BUDGIE_OK=1` or a `.budgie/allow.txt` allowlist for intended spend.
- ✅ Crash-proof hook, **fail-closed** on spend commands (`BUDGIE_FAIL=open` to override).
- ✅ **Live AWS pricing** — `pip install budgie[aws]` + `BUDGIE_PRICING=aws` (AWS Price List API); static table is the zero-dep default.

## Roadmap

- **Terraform/Pulumi** — plan-based pricing (Infracost / AWS Pricing MCP).
- **Tag + teardown** — session provenance → auto-cleanup on session end (`session.py`).
- **Multi-cloud** — GCP / Azure pricing tables.
- **Hard enforcement** — mint a scoped credential / SCP from the budget (the un-bypassable tier).

## Limitations (honest)

budgie is a **fast advisory guard**, not an un-bypassable control. Know its edges:

- **Bash-tool only.** It sees shell commands. An agent using an AWS **MCP server**
  (boto3 directly) bypasses it. The un-bypassable tier is a scoped credential / SCP
  minted from the budget — on the roadmap.
- **Provisioning cost, not usage cost.** It prices what a command *creates* (run-rate).
  Usage-based services — S3, Lambda, DynamoDB on-demand, data transfer, NAT data
  processing — can't be known before the fact and are not priced.
- **Estimates, not invoices.** It ignores Reserved Instances / Savings Plans / spot
  fluctuation, so a covered account may see over-estimates. Spot is a rough ~70% off.
- **Curated coverage.** It prices/​warns the big bill-shock services; it does not
  claim to price every AWS resource. Free creates are intentionally silent.

## License

MIT
