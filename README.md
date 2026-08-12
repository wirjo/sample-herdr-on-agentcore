# herdr on AWS Bedrock AgentCore Runtime

Run [herdr](https://herdr.dev) inside an AWS Bedrock AgentCore Runtime microVM,
and attach to it remotely over AgentCore's `InvokeAgentRuntimeCommandShell`
WebSocket-PTY channel — a durable, ephemeral-cloud-hosted terminal session
without standing up or managing your own SSH box.

```
python3 deploy.py       # build+push image, create runtime, write deploy_state.json
python3 attach.py       # open an interactive shell into the running container
> herdr                 # attach to the herdr workspace UI, same as on any server
```

Detach (Ctrl+D or `exit`), rerun `attach.py` with the same `--session`, and you
reconnect to the same shell with your herdr session, panes, and scrollback
still there.

## Why AgentCore Runtime as a remote target

Compared to "stand up a box and SSH into it":

- **No box to own** — no EC2 instance, no SSH keys to generate/rotate/hand
  out, no security groups, nothing to patch. AgentCore provisions and tears
  down the microVM for you.
- **IAM-native auth** — sigv4, not SSH keys. Fits whatever access
  control/rotation/audit an org already runs on AWS, instead of a parallel
  SSH-key system just for this.
- **Real isolation per session** — each runtime is its own microVM. For
  herdr specifically (agents running semi-autonomous code), that's a
  meaningfully stronger blast-radius boundary than another pane on a shared
  box.
- **Pay-per-use** — no idle EC2 cost sitting between sessions.
- **Audit trail for free** — CloudTrail/CloudWatch come with the service;
  no separate SSH-access-log pipeline to build.

"Persistence" here means session/pane state survives detach + reconnect
*within* the microVM's life — not indefinite storage. AgentCore's 8h
`maxLifetime` still applies, and this sample runs no S3 Files/EFS mount, so
state does not survive the microVM being torn down. See [Validation](#validation)
for what was and wasn't actually tested.

## How this maps to herdr's remote-access model

herdr already has two ways to work with a server-hosted session — see
[herdr.dev/docs/persistence-remote](https://herdr.dev/docs/persistence-remote/):

1. **SSH into the server, run `herdr` there.** herdr runs entirely on the
   remote machine; your terminal is just a dumb pipe to it.
2. **`herdr --remote <ssh-host>`** — a thin-client mode where your *local*
   herdr binary connects over SSH and streams the remote UI back, giving you
   things like desktop clipboard bridging that mode 1 can't.

AgentCore Runtime has neither of these natively. What it exposes is
`InvokeAgentRuntimeCommandShell` — a WebSocket-based PTY shell channel into
the running microVM, reachable through the `bedrock_agentcore` Python SDK's
`AgentCoreRuntimeClient.open_shell()`. This repo uses that channel as a
transport to reproduce **mode 1**: `attach.py` opens a real shell into the
container (already running the herdr server, started by `entrypoint.sh`),
and once you're in, you run `herdr` yourself exactly as you would over SSH.

Mapping the pieces:

| herdr concept | AgentCore equivalent here |
|---|---|
| SSH transport (mode 1) | AgentCore's shell WebSocket channel (`open_shell()`) |
| herdr server / session | the microVM + the `herdr server` process `entrypoint.sh` starts |
| detach / reattach | closing and reopening the WebSocket with the same `shell_id` + `session_id` |
| `herdr session attach <name>` (named sessions) | `attach.py --session <name>` (maps to a stable `shell_id`/`session_id` pair) |

**This is not herdr's native `--remote` thin-client transport.** Mode 2
requires herdr's own client binary to speak whatever transport it's given —
today that's SSH, not AgentCore's WebSocket shell protocol. Making `herdr
--remote` work directly against AgentCore would mean teaching herdr itself
to speak this transport as a client. This repo doesn't do that; it's a
pattern you can run today on top of AgentCore Runtime using mode 1's model,
and it's meant as a concrete reference if anyone wants to explore a native
upstream integration for mode 2.

## Quickstart

**Prerequisites:**
- An AWS account with credentials configured (`aws configure` or an
  `AWS_PROFILE` env var) with permissions for ECR, IAM, and
  `bedrock-agentcore-control`.
- Docker, for building the image.
- Python 3.9+ with `boto3` and `bedrock_agentcore` installed
  (`pip install boto3 bedrock-agentcore`).

**1. Build the image:**
```
docker build -t herdr-agentcore-sample:latest .
```

**2. Deploy:**
```
python3 deploy.py                                    # uses your default credential chain
python3 deploy.py --profile myprofile --region us-west-2   # or override explicitly
```
Creates an ECR repo, pushes the image, creates an IAM execution role and an
AgentCore runtime (PUBLIC network mode), and writes `deploy_state.json` with
the resulting ARNs for the next steps to read.

**3. Attach and run herdr:**
```
python3 attach.py
```
You land in a plain shell inside the container. Run `herdr` to attach to the
workspace UI — it connects to the already-running server, not a fresh one.

**4. Detach / reconnect demo:**
Press Ctrl+D (or `exit`) to leave the shell. Rerun the same command:
```
python3 attach.py
```
By default this reconnects to the same named session (`--session default`),
so `herdr pane list` (or the UI itself) shows the panes/tabs you had before —
the same persistence guarantee herdr gives you locally, now proven over
AgentCore's shell channel. Use `--session <name>` to run multiple independent
named sessions side by side.

**5. Clean up:**
```
python3 cleanup.py
```
Deletes the AgentCore runtime, IAM role, and ECR repo, then independently
re-verifies each is actually gone via fresh AWS API calls (see
[Validation](#validation) — trusting a script's exit code alone previously
left billable resources running for hours undetected).

## Cost / blast radius

Creates: 1 ECR repository, 1 IAM role, 1 AgentCore runtime (PUBLIC network
mode — no VPC, no EFS, no S3 Files). Cost is cents-scale for the duration you
run it. Nothing shared or production is touched; everything is scoped to
names this sample creates and `cleanup.py` removes.

## Validation

This was validated end-to-end on a real AWS account: deploy → attach → run
`herdr` → create a pane → detach → reattach with the same session → confirm
the pane persisted → cleanup → independently re-verified via fresh AWS CLI
calls that the runtime, IAM role, and ECR repo were actually gone.

The original smoke test that proved out the underlying approach (server
survives as PID1's child, PTY shells don't inherit Dockerfile `ENV`, detach/
reconnect preserves herdr session state) is preserved in `smoke-test/` — see
[`smoke-test/RESULTS.md`](smoke-test/RESULTS.md) for the full pass/fail
breakdown.
