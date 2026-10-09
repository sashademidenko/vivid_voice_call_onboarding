#!/usr/bin/env bash
# Applies all project settings to OpenClaw (~/.openclaw/openclaw.json). Safe to run again after any change.
#   ./scripts/apply-config.sh          # mock mode (default)
#   ./scripts/apply-config.sh twilio   # Twilio mode (config/voice-call.twilio.json5)
# Restart the Gateway afterwards (scripts/start-gateway.sh).
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
MODE="${1:-mock}"

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

PROVIDER_CONFIG="config/voice-call.$MODE.json5"
if [ ! -f "$PROVIDER_CONFIG" ]; then
  echo "Unknown mode '$MODE': $PROVIDER_CONFIG not found." >&2
  exit 1
fi

# Twilio mode: check the keys in .env and the ngrok program before switching (values are never printed).
if [ "$MODE" = "twilio" ]; then
  missing=""
  for key in TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM_NUMBER NGROK_AUTHTOKEN OPENAI_API_KEY; do
    grep -qE "^$key=.+" .env 2>/dev/null || missing="$missing $key"
  done
  if [ -n "$missing" ]; then
    echo "Fill these in .env first:$missing" >&2
    exit 1
  fi
  if ! command -v ngrok >/dev/null; then
    echo "ngrok is not installed (see README, 'Enable live calls')." >&2
    exit 1
  fi
fi

echo "1/5 Gateway settings"
openclaw config patch --file config/gateway.json5

echo "2/5 Agent noah"
if ! openclaw agents list 2>/dev/null | grep -q -- "- noah"; then
  openclaw agents add noah --workspace "$HOME/.openclaw/workspace-noah" --non-interactive
fi
openclaw config patch --file config/agent-noah.json5

echo "3/5 Plugin vivid-tools (send_help, end_call)"
if ! openclaw plugins inspect vivid-tools >/dev/null 2>&1; then
  openclaw plugins install --link ./plugins/vivid-tools --force
fi

echo "4/5 Voice Call ($MODE)"
openclaw config patch --file "$PROVIDER_CONFIG"

echo "5/5 Noah's prompt and the help-request log"
python3 - "$PROJECT_DIR" <<'EOF' | openclaw config patch --stdin
import json, sys
project = sys.argv[1]
prompt = open(f"{project}/prompts/noah.md", encoding="utf-8").read()
print(json.dumps({"plugins": {"entries": {
    "voice-call": {"config": {"responseSystemPrompt": prompt}},
    "vivid-tools": {"enabled": True, "config": {"outputFile": f"{project}/data/help-requests.jsonl"}},
}}}))
EOF

echo "Done. Restart the Gateway to apply: stop it (Ctrl+C) and run ./scripts/start-gateway.sh"
