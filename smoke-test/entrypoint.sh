#!/usr/bin/env bash
# PID1 for the AgentCore container.
#
# Gotcha (per AWS docs / ServerWorks blog): PTY shells opened via
# InvokeAgentRuntimeCommandShell are clean login shells, NOT children of
# this process, so they do not inherit env set here via `export` alone.
# Write anything herdr/tools need into /etc/profile.d so login shells pick
# it up.
set -euo pipefail

sudo_or_direct() {
  if [ "$(id -u)" = "0" ]; then "$@"; else "$@"; fi
}

# Persist env for PTY login shells (see gotcha above).
cat > /tmp/herdr-env.sh <<EOF
export HOME=${HOME}
export PATH="${PATH}"
EOF
# /etc/profile.d requires root; if not root, fall back to writing into
# a location we source from ~/.bashrc instead.
if [ -w /etc/profile.d ]; then
  cp /tmp/herdr-env.sh /etc/profile.d/herdr-env.sh
else
  echo '[ -f /tmp/herdr-env.sh ] && . /tmp/herdr-env.sh' >> "${HOME}/.bashrc"
fi

# Start herdr server detached so it's already running before anyone connects.
# `herdr server` (no subcommand) runs the headless server itself — there is no
# `herdr server start`; that prints help text and starts nothing (caught by
# this smoke test: session list showed running:false until this was fixed).
herdr server >/tmp/herdr-server.log 2>&1 &
HERDR_PID=$!
echo "herdr server starting (pid ${HERDR_PID}), logs at /tmp/herdr-server.log"

# Give it a moment, then report status for the container logs (CloudWatch).
sleep 2
herdr session list --json || echo "herdr session list failed (see /tmp/herdr-server.log)"

# Foreground: AgentCore's service contract requires GET /ping on :8080.
exec python3 /app/healthcheck.py
