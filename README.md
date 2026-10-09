# Vivid Business onboarding voice agent

An outbound AI voice agent, **Noah**, built on [OpenClaw](https://docs.openclaw.ai) with the
[voice-call plugin](https://docs.openclaw.ai/plugins/voice-call). Noah calls people who started opening a
Vivid Business account but didn't finish. He finds out why they stopped, offers help and collects their answers.

**Status:** the agent works end to end in **mock mode** (a simulated phone line, no real calls).
Live calls through **Twilio** are configured, with keys read from `.env`, but haven't been tested on a real
line yet. See [Limitations](#limitations).

---

## What the agent does

1. When the call connects, Noah says a fixed greeting ([prompts/greeting.txt](prompts/greeting.txt)): introduces
   himself as an AI assistant from Vivid Business, says the person didn't finish opening an account, and asks
   for two minutes.
2. If they agree, he says the security line ("I will never ask for passwords, codes or card details"), then
   asks three questions, one at a time:
   - **Q1** What stopped you from finishing the account opening?
   - **Q2** Would you like some help to finish it? The options are a link to continue, or a guide for the step
     they're stuck on. Both are sent to the Vivid app chat via the `send_help` function, after the person
     confirms.
   - **Q3** Are you still planning to open the account? Roughly when?
3. He confirms the next step, says goodbye once and hangs up himself.

He also handles these cases:
- **Busy:** asks when to call back.
- **Chose another bank:** asks one short question why, without arguing.
- **Irritated:** apologises once, points to support and asks whether to stop calling.
- **"Are you a real person?":** says honestly that he is an AI assistant.
- **Questions about fees, legal or tax:** says he doesn't have that information and points to support.
- **Offers card details:** stops them politely.

The full instructions are in [prompts/noah.md](prompts/noah.md).

## How it works

```
 you / a campaign script
        │  openclaw voicecall call --to <number> --message <greeting>
        ▼
 OpenClaw Gateway (runs on this computer)
   ├─ voice-call plugin ── phone line: mock (simulated)  or  Twilio (+ ngrok tunnel)
   │      │ each thing the person says
   │      ▼
   ├─ agent "noah" ── instructions: prompts/noah.md
   │      │ tools
   │      ▼
   └─ vivid-tools plugin (this repo)
          ├─ send_help  → records the request in data/help-requests.jsonl (stub)
          └─ end_call   → hangs up once the goodbye has been spoken
```

The call runs in OpenClaw's standard turn-by-turn mode. The phone line turns speech into text, the agent writes
a reply, and the line speaks it.

## Where to change what

| I want to change… | File |
|---|---|
| What Noah says and how he behaves | [prompts/noah.md](prompts/noah.md) (keep the "Platform notes" section at the end) |
| The first sentence of the call | [prompts/greeting.txt](prompts/greeting.txt) |
| Call settings: mock line | [config/voice-call.mock.json5](config/voice-call.mock.json5) |
| Call settings: Twilio line | [config/voice-call.twilio.json5](config/voice-call.twilio.json5) |
| Agent permissions (tools, memory) | [config/agent-noah.json5](config/agent-noah.json5) |
| What `send_help` / `end_call` do | [plugins/vivid-tools/index.ts](plugins/vivid-tools/index.ts) |
| Keys (OpenAI, Twilio, ngrok) | `.env` (copy from [.env.example](.env.example); never committed) |

After changing anything except `.env`, run `./scripts/apply-config.sh` (add `twilio` in Twilio mode) and
**restart the Gateway**. After changing only `.env`, just restart the Gateway.

OpenClaw keeps its own settings in `~/.openclaw/openclaw.json`. The files in `config/` are the source of truth,
and `apply-config.sh` copies them there.

## Requirements

- macOS or Linux (tested on macOS, Apple Silicon)
- Node.js 24.16+ or 26.1+ (tested with 26.11.1)
- OpenClaw 2026.9.9 and `@openclaw/voice-call` 2026.9.9
- Python 3 (for the mock test script)
- An OpenAI API key (OpenClaw's default model provider here)

## Install

```bash
# 1. OpenClaw (installs Node 26 if it's missing) and the voice-call plugin
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard
openclaw plugins install @openclaw/voice-call

# 2. This repo
git clone <this repo> vivid-voice-agent && cd vivid-voice-agent
cp .env.example .env        # then put your OpenAI key in OPENAI_API_KEY

# 3. Apply the project settings to OpenClaw (creates the "noah" agent, links the vivid-tools plugin)
./scripts/apply-config.sh
```

If `openclaw` or `node` is "command not found" in a new terminal and Node was installed with nvm, add this to
`~/.zshrc`:

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

## Run in mock mode (no real calls)

Terminal 1 starts the Gateway and keeps it running. Keys are passed in from `.env`.

```bash
./scripts/start-gateway.sh
```

Terminal 2 places a test call. You type what the customer says, and Noah answers.

```bash
python3 scripts/mock-call.py
```

```
🤖 Agent: Hi, this is Noah, an AI assistant from Vivid Business. … Do you have two minutes?
👤 Customer: Sure, go ahead.
🤖 Agent: Just so you know, I will never ask for passwords, codes or card details. What stopped you from finishing the account opening?
…
```

Type `/end` to hang up. Each reply takes about 2–6 seconds. Requests for help land in
`data/help-requests.jsonl`. Full call records: `openclaw voicecall tail`.

## Enable live calls (Twilio)

You need:
1. **A Twilio account and a phone number with Voice.** A US number is issued instantly. Numbers in many other
   countries need a regulatory bundle that takes days to approve.
   - In **Voice → Settings → Geo Permissions**, allow the country you'll call.
   - On a **trial** account you can only call numbers listed under **Verified Caller IDs**, and every call starts
     with a short Twilio trial message.
2. **An ngrok account and the ngrok program.** Twilio has to reach this computer over the internet, and ngrok
   opens a temporary public HTTPS address for it. The free plan is enough.
   - On macOS without Homebrew: download the macOS zip from the ngrok dashboard (*Setup & Installation*), then:
     ```bash
     cd ~/Downloads && unzip ngrok-v3-stable-darwin-arm64.zip
     sudo mkdir -p /usr/local/bin && sudo mv ngrok /usr/local/bin/
     ngrok version
     ```
   - With Homebrew: `brew install ngrok`.

Then:

```bash
# 1. Fill in .env (no quotes, no spaces):
#    TWILIO_ACCOUNT_SID   Twilio Console → Account Info
#    TWILIO_AUTH_TOKEN    Twilio Console → Account Info
#    TWILIO_FROM_NUMBER   your Twilio number, e.g. +15550001234
#    NGROK_AUTHTOKEN      ngrok dashboard → Your Authtoken

# 2. Switch to Twilio. The script refuses if a key is missing or ngrok isn't installed.
./scripts/apply-config.sh twilio

# 3. Restart the Gateway (Ctrl+C in its terminal, then):
./scripts/start-gateway.sh

# 4. Check, then try a dry run, then a real call to your own (verified) phone
openclaw voicecall setup
openclaw voicecall smoke --to "+<your number>"
openclaw voicecall call --to "+<your number>" --message "$(cat prompts/greeting.txt)"
```

No keys go into the config files. [config/voice-call.twilio.json5](config/voice-call.twilio.json5) leaves the
Twilio fields empty, and the voice-call plugin reads `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER` and `NGROK_AUTHTOKEN` from the environment. `start-gateway.sh` loads them from `.env`.
Using fake values, I confirmed that `openclaw voicecall setup` reports the Twilio configuration as complete
when these variables are set.

To go back to mock: `./scripts/apply-config.sh`, then restart the Gateway.

## Limitations

- **Twilio is not tested on a real line.** The config is checked against OpenClaw's schema, and the
  environment-key pickup was verified with fake keys. No real call has been placed yet. Speech recognition
  accuracy, voice quality, latency and interruptions over the phone are unknown.
- **`send_help` is a stub.** It doesn't send anything to the Vivid app. It appends the request (link or
  guide, plus the stuck step) to `data/help-requests.jsonl`. It records the OpenClaw call/session ID, not a
  Vivid customer ID. A real integration needs the Vivid app chat API and a way to pass the customer ID into
  the call.
- **Voicemail is not detected by the phone line.** Twilio's answering-machine detection is not enabled. The
  "end the call on voicemail" rule depends on the model recognising a voicemail greeting in the transcript,
  and this hasn't been tested.
- **Turn-by-turn, not realtime.** Each reply takes a few seconds. OpenClaw's realtime voice mode
  (speech-to-speech, faster) works only with Twilio and is not set up.
- **Answers are not collected into a table yet.** They're only in the call transcripts
  (`openclaw voicecall tail`). Next step: a per-call row in `data/calls.csv` (reason, wants help, help type,
  plans, call-back time, do-not-call, outcome) for import into a database.
- **"Don't call me again" is not stored anywhere yet.** Noah asks and acknowledges it, but nothing keeps that
  number from being called again.
- **Calls cut off midway are not resumed.** Each call starts fresh. This is on purpose for now: first we
  measure how often calls drop.
- **One call at a time** (the plugin default), and **one fixed greeting** with no customer name.
- **Restart the Gateway after any config or plugin change.** OpenClaw tries to apply changes "live", but
  for the voice-call plugin this leaves the Gateway refusing new work ("Gateway is draining"), and the agent
  goes silent.
- **Workaround for hanging up.** The voice-call plugin's own `voice_call` tool can't be used from inside the
  call agent: it starts a second voice-call runtime and breaks the live call. So `end_call` starts a small
  background helper ([hangup-when-quiet.mjs](plugins/vivid-tools/hangup-when-quiet.mjs)). The helper waits
  until the goodbye is spoken and hangs up through the `openclaw voicecall` CLI. If no goodbye appears, it
  hangs up after 45 seconds.
- **No calling-hours or consent checks.** The agent doesn't check local time, consent or do-not-call lists
  before dialing. That is up to whatever starts the calls.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Noah doesn't answer in mock; the Gateway log says `Gateway is draining` | Restart the Gateway |
| `openclaw: command not found` | Add the nvm lines above to `~/.zshrc` and open a new terminal |
| `Gateway start blocked: … missing gateway.mode` | Run `./scripts/apply-config.sh` |
| `Another gateway … already owns this state directory` | A Gateway is already running. Stop it (`pkill -f openclaw-gateway`) and start again |
| Call ends by itself after 5 minutes | Plugin default maximum call length |

## Repository layout

```
config/            OpenClaw settings (no secrets): gateway, agent, mock and Twilio lines
prompts/           Noah's instructions and greeting
plugins/vivid-tools/  send_help (stub) and end_call tools
scripts/           apply-config.sh, start-gateway.sh, mock-call.py
data/              call output (help requests). Ignored by git: contains personal data
.env.example       which keys are needed. Copy to .env
```
