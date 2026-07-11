# Launch kit

## Show HN

**Title**
> Show HN: Budgie – block an AI agent's costly cloud command before it runs

**Body**

An AI coding agent once looped `cloudformation deploy` and ran up a $6,531 AWS
bill. Every guardrail I found was too late or too coarse: billing alerts fire
*after* the spend, AWS Budgets triggers after a cumulative threshold, Infracost
runs at CI/PR time, and "agent budget" tools cap LLM *tokens*, not infrastructure.

Budgie is a `PreToolUse` hook for Claude Code (and Cursor/Codex via MCP) that
sits in front of the agent's shell. Before a money-spending command runs, it
parses it, prices it, and **denies it if it blows the budget** — so the command
never executes.

    $ budgie check 'aws ec2 run-instances --instance-type p5.48xlarge --count 2'
    [BLOCK] 2× ec2 p5.48xlarge ≈ $196.64/hr ($143,547/mo) — over the $2.00/hr cap.

What it does:
- Extracts every `aws` invocation from arbitrary bash — loops, `&&`/`;` chains,
  `xargs`, full paths (the loop case is exactly how the bills happen).
- Prices live via the AWS Price List API (region-aware, disk-cached); a zero-dep
  static table is the offline default.
- Enforces a **cumulative session budget**, not just per-command.
- Fails safe: unknown SKUs, `--cli-input-json`, and `terraform apply` warn rather
  than silently allow. Also prices `terraform show -json` plans.
- Ledger of every decision + an override (`BUDGIE_OK=1` / allowlist).

Honest limits: it's a Bash-tool guard (an agent using a boto3 MCP server bypasses
it — the un-bypassable tier is a scoped credential/SCP minted from the budget,
next on the roadmap); it prices provisioning, not usage (S3/Lambda/egress); and
estimates ignore RIs/Savings Plans.

Python, MIT, zero deps for the core. Feedback welcome — especially on the pricing
filters and which services to cover next.

GitHub: https://github.com/TanishkaMarrott/budgie

---

## X / tweet

> Agents now run real `aws` commands — and loop them. One racked up a $6,531 bill.
>
> Budgie 🐦 is a PreToolUse hook that prices a cloud command *before it runs* and
> blocks the over-budget ones. Billing alerts are too late; this is at the door.
>
> MIT, zero-dep core. github.com/TanishkaMarrott/budgie

---

## awesome-claude-code / awesome-mcp-servers PR line

> **[budgie](https://github.com/TanishkaMarrott/budgie)** — a spend firewall: a
> PreToolUse hook that prices an agent's `aws`/`terraform` command before it runs
> and blocks the over-budget ones (session budget, live pricing, ledger).

---

## Pre-launch checklist

- [ ] Record `docs/demo.gif` (`vhs demo/demo.tape`) and put it at the top of the README
- [ ] `gh repo edit TanishkaMarrott/budgie --visibility public --accept-visibility-change-consequences`
- [ ] Tag `v0.1.0` and (optionally) publish to PyPI so `uvx budgie` works
- [ ] Open the awesome-list PRs
- [ ] Post Show HN in the morning (US), then the X thread
