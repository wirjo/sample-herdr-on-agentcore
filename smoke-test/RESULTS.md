# Smoke test results — herdr on AgentCore Runtime

Run: 2026-08-12, us-east-1 (personal test account).
Runtime: `herdr_agentcore_smoke_test-bYcjfoAjLh` (PUBLIC network, no VPC/EFS/S3-Files).

## test_plan.md hypothesis results

1. **herdr server survives as PID1's child inside the container.**
   PASS — local Docker smoke test: image builds, `entrypoint.sh` starts herdr,
   healthcheck responds on :8080/ping, herdr socket exists.

2. **`open_shell()` gives a real PTY into the already-running herdr session
   (not a fresh container).**
   PASS — `h2_herdr_attaches_existing_session`: `herdr session list --json`
   shows the one session started by `entrypoint.sh` (not a second one).
   PASS — `h2b_real_tty_device`: `test -t 0/1` confirms a real PTY, backed by
   an actual `/dev/pts/N` device (not a pipe).

3. **PTY shells don't inherit Dockerfile ENV — profile.d workaround needed.**
   PASS (`h3_env_gotcha_handled`) — `HOME` and `herdr` PATH resolve correctly
   inside the shell via the `/etc/profile.d` fallback written by
   `entrypoint.sh`. Confirms the workaround is necessary and sufficient.

4. **Detach/reconnect via shellId preserves herdr session state.**
   PASS across all sub-checks:
   - `h4_reconnect`: reconnecting with the same `session_id`/`shell_id` after
     an abrupt (non-graceful) websocket close reports `reconnected=True`.
   - `h4_setup_pane_create` / `h4_state_preserved_across_reconnect`: a pane
     created (`herdr workspace create`) before the disconnect is still
     present (`w1:p1`) in `herdr pane list` after reconnect.
   - `h4_herdr_survives_reconnect`: `herdr session list` still reports the
     same running session post-reconnect.

5. **8h maxLifetime kills the microVM regardless of activity; herdr's own
   session state does NOT survive that without a filesystem mount.**
   Not executed (out of scope for this smoke test — no 8h wait performed).
   Documented as expected/assumed behavior only, per test_plan.md; no
   filesystem mount was configured, consistent with the deliberately minimal
   PUBLIC-network/no-VPC/no-EFS/no-S3-Files setup used here.

## Overall

8/8 executed checks PASS. Hypothesis 5 intentionally not executed (documented
assumption, out of scope). Full raw output/frames in `connect_results.json`.

## Cleanup

`cleanup.py` deleted the AgentCore runtime, IAM execution role, and ECR repo.
Independently re-verified via fresh AWS CLI calls (not just script exit
status) after a prior run had left resources live for ~8h unnoticed:
- `list-agent-runtimes`: no `herdr_agentcore_smoke_test-*` runtime remains.
- IAM role: `NoSuchEntity`.
- ECR repo: `RepositoryNotFoundException`.
