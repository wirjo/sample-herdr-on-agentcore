#!/usr/bin/env bash
# PID1 for the AgentCore container.
#
# Gotcha (per AWS docs / ServerWorks blog): PTY shells opened via
# InvokeAgentRuntimeCommandShell are clean login shells, NOT children of
# this process, so they do not inherit env set here via `export` alone.
# Write anything herdr/tools need into /etc/profile.d so login shells pick
# it up.
set -euo pipefail

SESSION_STORAGE_MOUNT="${HERDR_SESSION_STORAGE_MOUNT:-/mnt/workspace}"
HERDR_CONFIG_DIR="${HOME}/.config/herdr"

# If deploy.py was run with --session-storage, AgentCore mounts managed
# session storage at SESSION_STORAGE_MOUNT before this script runs (the
# Dockerfile does not create that path itself, so its presence here means
# AgentCore attached it -- not that it happened to already exist in the
# image). herdr keeps its session/pane state under ~/.config/herdr (see
# `herdr session list --json` -> session_dir); symlinking that directory
# onto the mounted path is what makes herdr's own persistence survive a
# stop/resume cycle instead of just resetting with the microVM's ephemeral
# root filesystem.
#
# See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-filesystem-configurations.html
if [ -d "${SESSION_STORAGE_MOUNT}" ]; then
  echo "Session storage mount detected at ${SESSION_STORAGE_MOUNT}; persisting herdr state there."
  mkdir -p "${SESSION_STORAGE_MOUNT}/herdr-config"
  mkdir -p "$(dirname "${HERDR_CONFIG_DIR}")"
  if [ -L "${HERDR_CONFIG_DIR}" ]; then
    : # Already a symlink from a prior boot on the same persisted filesystem.
  elif [ -d "${HERDR_CONFIG_DIR}" ]; then
    # First boot with a non-empty pre-existing dir (shouldn't normally
    # happen since the image doesn't create it, but handle it safely
    # rather than silently discarding anything already there).
    cp -a "${HERDR_CONFIG_DIR}/." "${SESSION_STORAGE_MOUNT}/herdr-config/" 2>/dev/null || true
    rm -rf "${HERDR_CONFIG_DIR}"
    ln -s "${SESSION_STORAGE_MOUNT}/herdr-config" "${HERDR_CONFIG_DIR}"
  else
    ln -s "${SESSION_STORAGE_MOUNT}/herdr-config" "${HERDR_CONFIG_DIR}"
  fi
else
  echo "No session storage mount at ${SESSION_STORAGE_MOUNT}; herdr state is ephemeral for this run."
fi

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
