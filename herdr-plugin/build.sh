#!/usr/bin/env bash
# Plugin build hook: creates a local venv and installs the Python deps
# attach.py needs (boto3, bedrock_agentcore). Runs once at
# `herdr plugin install`/`herdr plugin link` time, not on every pane open.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PLUGIN_ROOT}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${PLUGIN_ROOT}/requirements.txt"

echo "herdr-agentcore.attach: venv ready at ${VENV_DIR}"
