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

**Not run yet** — building the Docker image and running `deploy.py` will
create real billable AWS resources under whatever profile/account is active.
Confirm before executing the deploy step.
