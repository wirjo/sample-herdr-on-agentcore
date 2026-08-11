# Test plan — hypotheses from the herdr/AgentCore discussion

Each maps to a claim made in the thread. Pass/fail determines what goes into
the upstream PR description.

1. **herdr server survives as PID1's child inside a microVM container.**
   `docker build` + local `docker run` sanity check first (no AWS needed).
   Pass: `entrypoint.sh` starts herdr, healthcheck responds on :8080/ping,
   herdr socket exists at $HERDR_SOCKET_PATH.

2. **`agentcore exec --it` / `open_shell()` gives a real PTY into the running
   herdr session (not a fresh container).**
   Deploy to AgentCore, connect via `connect.py`, run `herdr` bare (no args) —
   should attach to the already-running server started by entrypoint.sh,
   not spawn a second server.
   Pass: `herdr session list` shows one session; attaching shows the same
   session across two separate `connect.py` invocations (same session_id).

3. **PTY shells don't inherit Dockerfile ENV — profile.d workaround needed.**
   Confirmed independently by AWS's own blog post (ServerWorks) and doc.
   Verify same behavior holds for herdr's runtime env vars
   (HERDR_SOCKET_PATH etc. are injected by herdr itself at pane-launch time,
   not by Docker ENV, so this may be moot — check what actually breaks).
   Pass/fail: document actual observed behavior, don't assume it transfers.

4. **Detach/reconnect via shellId preserves herdr session state.**
   Connect, create a pane, detach (Ctrl+]), reconnect with same
   session_id + shell_id within the 1h WS TTL.
   Pass: pane still exists, scrollback intact — same guarantee herdr already
   provides for local server restarts, now validated over the AgentCore
   WS layer too.

5. **8h maxLifetime kills the microVM regardless of activity; herdr's own
   session-state/restore does NOT survive that by default (ephemeral
   container fs unless a filesystem mount is configured).**
   Skip actual 8h wait for this smoke test — just confirm the *documented*
   behavior applies (no filesystem mount configured on purpose) so the
   PR description doesn't overclaim persistence without one.

## Out of scope for this smoke test
- S3 Files / EFS session-storage mount (needed for surviving maxLifetime
  teardown) — separate follow-up if hypothesis 5 needs deeper validation.
- Multi-agent / gateway MCP tooling from the sample repo — not relevant to
  the herdr PR.
