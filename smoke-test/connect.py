#!/usr/bin/env python3
"""Connect to the herdr-on-AgentCore smoke test runtime and validate
test_plan.md hypotheses using the official bedrock_agentcore SDK
InvokeAgentRuntimeCommandShell interface.

Usage: AWS_PROFILE=herdr-agentcore-deploy python3 connect.py
"""
import asyncio
import json
import sys
import uuid

import boto3
from bedrock_agentcore.runtime import AgentCoreRuntimeClient
from bedrock_agentcore.runtime.shell import ShellChannel

PROFILE = "herdr-agentcore-deploy"
REGION = "us-east-1"

with open("deploy_state.json") as f:
    STATE = json.load(f)
RUNTIME_ARN = STATE["agentRuntimeArn"]


async def read_until(shell, marker, timeout=25):
    """Read frames until a line consisting solely of `marker` (the real command
    result, not merely an echo of typed input that happens to contain the
    marker text as a substring, e.g. "echo FOO_END" typed-input echo) appears,
    or until timeout. Returns accumulated text."""
    buf = ""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(shell.__anext__(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except StopAsyncIteration:
            break
        if frame.channel == ShellChannel.STDOUT:
            buf += frame.text
            for line in buf.split("\n"):
                if line.strip() == marker:
                    return buf
    return buf


async def run_validation():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    client = AgentCoreRuntimeClient(region=REGION, session=session)
    results = {}

    session_id = str(uuid.uuid4())
    shell_id = "herdr-smoke-shell-1"

    print(f"=== Connecting: session_id={session_id} shell_id={shell_id} ===")
    print(f"Runtime ARN: {RUNTIME_ARN}")

    # Use manual __aenter__ (not `async with`) for the first connection so we
    # can simulate an ABRUPT disconnect afterward (closing the raw websocket
    # directly) instead of letting a context-manager __aexit__ send a graceful
    # CLOSE frame -- the two are semantically different to the server (see note
    # below) and test_plan.md hypothesis 4 is specifically about surviving an
    # unexpected drop, not an intentional close.
    shell = await client.open_shell(RUNTIME_ARN, session_id=session_id, shell_id=shell_id).__aenter__()
    print(f"Connected. Shell ID: {shell.shell_id}, reconnected={shell.reconnected}")
    results["h1_connect"] = {"pass": True, "shell_id": shell.shell_id}

    # --- Hypothesis 3: ENV-not-inherited gotcha (bashrc fallback) ---
    await shell.send("echo ENVCHECK_START; echo HOME=$HOME; which herdr; echo ENVCHECK_END\n")
    out = await read_until(shell, "ENVCHECK_END")
    print("--- env check output ---")
    print(out)
    env_ok = "HOME=/home/agent" in out and "/usr/local/bin/herdr" in out
    results["h3_env_gotcha_handled"] = {"pass": env_ok, "output": out}

    # --- Hypothesis 2: herdr session list shows the already-running
    # server (not a fresh one) ---
    await shell.send("herdr session list --json 2>&1; echo HERDR_LIST_DONE\n")
    out2 = await read_until(shell, "HERDR_LIST_DONE")
    print("--- herdr session list output ---")
    print(out2)
    herdr_ok = "running" in out2 and "true" in out2
    results["h2_herdr_attaches_existing_session"] = {"pass": herdr_ok, "output": out2}

    # --- Hypothesis 2 (as specified by test_plan.md): confirm attaching
    # via the real PTY provides a genuine TTY device that herdr's TUI can
    # initialize against (unlike `docker exec` without -it locally, which
    # panics with "No such device or address" -- ratatui's terminal init
    # requires a real tty). Then use herdr's own documented CLI API
    # (per `herdr --skill`) to create a workspace/pane rather than TUI
    # keystrokes, which is the correct way to drive herdr non-interactively. ---
    await shell.send("test -t 0 && test -t 1 && echo REAL_TTY_CONFIRMED || echo REAL_TTY_FAILED; ls -la /proc/self/fd/0 2>&1; echo TTYCHECK_DONE\n")
    tty_out = await read_until(shell, "TTYCHECK_DONE")
    print("--- tty check (proves real PTY via test -t + /proc/self/fd, not the tty(1) command --")
    print("    which can fail self-referentially over some PTY bridges despite a genuine")
    print("    /dev/pts device being attached; test -t + fd inspection are the reliable check) ---")
    print(tty_out)
    real_tty = "REAL_TTY_CONFIRMED" in tty_out and "/dev/pts/" in tty_out
    results["h2b_real_tty_device"] = {"pass": real_tty, "output": tty_out}

    await shell.send("herdr workspace create 2>&1; echo WSCREATE_DONE\n")
    ws_out = await read_until(shell, "WSCREATE_DONE")
    print("--- herdr workspace create output ---")
    print(ws_out)
    results["h4_setup_pane_create"] = {"pass": "workspace_created" in ws_out, "output": ws_out}

    print("\n=== Simulating abrupt disconnect (Ctrl-] / network drop), not a graceful close ===")
    # NOTE: closing the raw websocket directly (bypassing __aexit__'s graceful
    # CLOSE frame) is what actually simulates an unexpected drop/detach. A
    # graceful close tells the server the shell is intentionally over, so a
    # subsequent connect correctly reports reconnected=False in that case --
    # that's correct SDK behavior, not a bug, and is NOT what hypothesis 4 is
    # asking about.
    await shell._ws.close()
    shell._ws = None
    shell._closed = True

    # --- Hypothesis 4: detach + reconnect preserves state ---
    async with client.open_shell(RUNTIME_ARN, session_id=session_id, shell_id=shell_id) as shell2:
        print(f"Reconnected. Shell ID: {shell2.shell_id}, reconnected={shell2.reconnected}")
        results["h4_reconnect"] = {"pass": shell2.reconnected, "shell_id": shell2.shell_id}

        await shell2.send("herdr pane list 2>&1; echo PANECHECK_END\n")
        out3 = await read_until(shell2, "PANECHECK_END")
        print("--- post-reconnect herdr pane list ---")
        print(out3)
        state_preserved = "w1:p1" in out3
        results["h4_state_preserved_across_reconnect"] = {"pass": state_preserved, "output": out3}

        await shell2.send("herdr session list --json 2>&1; echo HERDR_LIST2_DONE\n")
        out4 = await read_until(shell2, "HERDR_LIST2_DONE")
        print("--- herdr session list after reconnect ---")
        print(out4)
        results["h4_herdr_survives_reconnect"] = {
            "pass": "running" in out4 and "true" in out4,
            "output": out4,
        }

    return results


def main():
    results = asyncio.run(run_validation())
    print("\n=== VALIDATION SUMMARY ===")
    all_pass = True
    for name, r in results.items():
        status = "PASS" if r["pass"] else "FAIL"
        if not r["pass"]:
            all_pass = False
        print(f"{status}: {name}")
    with open("connect_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Overall:", "ALL PASS" if all_pass else "SOME FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
