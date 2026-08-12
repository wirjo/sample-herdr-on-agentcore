#!/usr/bin/env python3
"""Interactive terminal attach to a herdr-on-AgentCore runtime.

Opens a real WebSocket PTY shell into the running container via
InvokeAgentRuntimeCommandShell (bedrock_agentcore's AgentCoreRuntimeClient
.open_shell()), puts the local terminal into raw mode, and pumps
stdin <-> the remote PTY so it behaves like a genuine interactive session
(no fixed commands, no scripted markers).

Once connected you land in a plain shell inside the container (entrypoint.sh
already started the herdr server in the background) — run `herdr` yourself
to attach to the workspace UI.

Usage:
    python3 attach.py                          # reads deploy_state.json
    python3 attach.py --session mysession      # named session (reattach later)
    python3 attach.py --runtime-arn arn:aws:... --profile myprofile --region us-east-1

Exit: Ctrl+D (EOF) or the shell's own `exit` closes the session gracefully.
Rerunning with the same --session reattaches to the same PTY (same demo as
herdr's own detach/reconnect model).
"""
import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import termios
import tty

import boto3
from bedrock_agentcore.runtime import AgentCoreRuntimeClient
from bedrock_agentcore.runtime.shell import ShellChannel

DEFAULT_STATE_FILE = "deploy_state.json"


def load_runtime_arn(state_file):
    with open(state_file) as f:
        state = json.load(f)
    return state["agentRuntimeArn"]


async def pump_stdin(shell, loop):
    """Read raw bytes from local stdin and forward them to the remote PTY."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    while True:
        chunk = await reader.read(1024)
        if not chunk:
            break
        await shell.send_bytes(chunk)


async def pump_stdout(shell, diagnostics):
    """Forward remote PTY output frames to local stdout.

    Only ShellChannel.STDOUT is written live to the terminal. Per the
    protocol docs (bedrock_agentcore.runtime.shell.protocol), STDERR on this
    channel is AgentCore *platform* diagnostics, not the remote process's
    stderr -- an out-of-band side channel, not part of the rendered stream.
    herdr's TUI draws via absolute-cursor-position ANSI sequences; splicing
    unrelated diagnostic bytes into that stream mid-escape-sequence corrupts
    the renderer (garbled box-drawing/text). Diagnostics are buffered instead
    and flushed to stderr after the session ends, once raw mode is restored.
    """
    async for frame in shell:
        if frame.channel == ShellChannel.STDOUT:
            os.write(sys.stdout.fileno(), frame.payload)
        elif frame.channel == ShellChannel.STDERR:
            diagnostics.append(frame.payload)


async def watch_resize(shell):
    """Forward local terminal resizes to the remote PTY."""
    loop = asyncio.get_running_loop()
    resize_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGWINCH, resize_event.set)
    while True:
        await resize_event.wait()
        resize_event.clear()
        size = shutil.get_terminal_size()
        await shell.resize(size.columns, size.lines)


async def run_session(client, runtime_arn, session_id, shell_id):
    print(f"Connecting (session_id={session_id}, shell_id={shell_id})...", file=sys.stderr)
    diagnostics = []
    async with client.open_shell(runtime_arn, session_id=session_id, shell_id=shell_id) as shell:
        print(
            f"Connected. shell_id={shell.shell_id} reconnected={shell.reconnected}",
            file=sys.stderr,
        )
        size = shutil.get_terminal_size()
        await shell.resize(size.columns, size.lines)

        loop = asyncio.get_running_loop()
        stdout_task = asyncio.ensure_future(pump_stdout(shell, diagnostics))
        stdin_task = asyncio.ensure_future(pump_stdin(shell, loop))
        resize_task = asyncio.ensure_future(watch_resize(shell))

        try:
            # Session ends when the remote shell closes (stdout_task finishes)
            # or the local side hits EOF (stdin_task finishes) — Ctrl+D.
            done, pending = await asyncio.wait(
                {stdout_task, stdin_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
        finally:
            resize_task.cancel()
            for task in (stdout_task, stdin_task, resize_task):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    return diagnostics


def main():
    parser = argparse.ArgumentParser(
        description="Interactively attach to a herdr-on-AgentCore runtime over a WebSocket PTY shell."
    )
    parser.add_argument(
        "--runtime-arn",
        help=f"AgentCore runtime ARN (default: read from {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to deploy_state.json written by deploy.py (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument("--profile", default=None, help="AWS profile (default: current credential chain)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--session",
        default="default",
        help="Named session — reuse the same name to reattach to the same PTY (default: 'default')",
    )
    args = parser.parse_args()

    runtime_arn = args.runtime_arn or load_runtime_arn(args.state_file)

    # Stable per-name session_id/shell_id so re-running with the same
    # --session reconnects to the same VM + PTY instead of provisioning a
    # fresh one — this is the detach/reattach demo. runtimeSessionId has a
    # 33-char minimum, so short --session names are zero-padded to satisfy it.
    session_id = f"herdr-attach-session-{args.session}".ljust(33, "0")
    shell_id = f"herdr-attach-shell-{args.session}"

    boto_session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = AgentCoreRuntimeClient(region=args.region, session=boto_session)

    if not sys.stdin.isatty():
        print("attach.py requires an interactive terminal (stdin is not a TTY).", file=sys.stderr)
        return 1

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    diagnostics = []
    try:
        tty.setraw(fd)
        diagnostics = asyncio.run(run_session(client, runtime_arn, session_id, shell_id))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print("\nDetached. Rerun with the same --session to reattach.", file=sys.stderr)
        for payload in diagnostics:
            sys.stderr.buffer.write(payload)
        if diagnostics:
            sys.stderr.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
