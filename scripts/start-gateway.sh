#!/usr/bin/env bash
# Starts the OpenClaw Gateway (which runs the voice-call plugin) with keys from this project's .env.
# OpenClaw ignores provider keys in a project .env on purpose, so we pass them in as environment variables.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env file found. Copy .env.example to .env and fill in your keys." >&2
  exit 1
fi

# Make node/openclaw available even if nvm isn't loaded in this shell.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

set -a
. ./.env
set +a

exec openclaw gateway run "$@"
