# herdr on AgentCore Runtime — smoke test

Validates the PR idea before proposing it upstream to herdrdev/herdr:
run the `herdr` server inside an AgentCore Runtime container, attach to it
via `InvokeAgentRuntimeCommandShell` (WebSocket PTY), confirm panes/agents
work, confirm detach+reconnect preserves state, confirm the ENV-not-inherited
gotcha is handled.

Minimal scope for this smoke test — no VPC, no S3 Files, no gateway MCP.
Just: does herdr run and stay attachable through the AgentCore shell channel.

## Files
- `Dockerfile` — installs herdr binary + a healthcheck stub for AgentCore's
  service contract (GET /ping on :8080, since herdr itself doesn't serve HTTP).
- `entrypoint.sh` — PID1: writes env to /etc/profile.d (PTY shells don't
  inherit Dockerfile ENV — see docs/gotchas.md), starts `herdr server start`
  detached, then execs the healthcheck server in foreground.
- `deploy.py` — creates IAM execution role + AgentCore runtime (PUBLIC network,
  no VPC, no filesystem mount — deliberately minimal for the smoke test).
- `connect.py` — opens a WebSocket shell (`open_shell`), attaches to herdr.
- `cleanup.py` — deletes the runtime + role.
- `test_plan.md` — what "pass" looks like for each hypothesis in the PR.

## Cost / blast radius
Creates: 1 ECR repo, 1 IAM role, 1 AgentCore runtime (PUBLIC network mode,
no VPC/EFS/S3-Files). Should be a few cents for the duration of testing.
Nothing shared/production is touched. `cleanup.py` tears it down after.

## Validation status

**Executed end-to-end on 2026-08-12** (account 575108946562, us-east-1,
profile `herdr-agentcore-deploy`). Deployed, connected, validated, and torn
down. 8/8 executed hypotheses from `test_plan.md` PASS (hypothesis 5 — 8h
maxLifetime teardown — intentionally not executed; documented as an
assumption only). See `smoke-test/RESULTS.md` for the full pass/fail
breakdown and `smoke-test/connect_results.json` for raw output.

Cleanup (`cleanup.py`) ran and was independently re-verified via fresh AWS
CLI calls (runtime/IAM role/ECR repo all confirmed gone) — no live resources
left behind after this run.
