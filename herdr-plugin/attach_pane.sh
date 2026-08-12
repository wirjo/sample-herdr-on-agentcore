#!/usr/bin/env bash
# herdr plugin entrypoint: opens attach.py as a herdr pane.
#
# Config: HERDR_PLUGIN_CONFIG_DIR/.env can set AWS_PROFILE, AWS_REGION,
# HERDR_AGENTCORE_STATE_FILE, and HERDR_AGENTCORE_SESSION (see README in
# this directory). Falls back to the parent repo's deploy_state.json and
# the "default" session name when unset.
set -euo pipefail

PLUGIN_ROOT="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CONFIG_DIR="${HERDR_PLUGIN_CONFIG_DIR:-${PLUGIN_ROOT}}"

if [ -f "${CONFIG_DIR}/.env" ]; then
  # shellcheck disable=SC1091
  source "${CONFIG_DIR}/.env"
fi

STATE_FILE="${HERDR_AGENTCORE_STATE_FILE:-${PLUGIN_ROOT}/../deploy_state.json}"
SESSION_NAME="${HERDR_AGENTCORE_SESSION:-default}"
VENV_DIR="${PLUGIN_ROOT}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Plugin venv missing at ${VENV_DIR} -- rerun 'herdr plugin install' or the build step." >&2
  exit 1
fi

ATTACH_ARGS=(--state-file "${STATE_FILE}" --session "${SESSION_NAME}")
if [ -n "${AWS_PROFILE:-}" ]; then
  ATTACH_ARGS+=(--profile "${AWS_PROFILE}")
fi
if [ -n "${AWS_REGION:-}" ]; then
  ATTACH_ARGS+=(--region "${AWS_REGION}")
fi

exec "${VENV_DIR}/bin/python3" "${PLUGIN_ROOT}/../attach.py" "${ATTACH_ARGS[@]}"
