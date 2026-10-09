// Hangs up the agent's current call once its goodbye has been spoken.
// Runs as a separate background process started by the end_call tool, and talks to the Gateway
// through the `openclaw voicecall` CLI. Calling the voice_call tool from inside the agent would start
// a second voice-call runtime and break the live call, so we go through the CLI instead.
//   node hangup-when-quiet.mjs <sessionKey> <requestedAtMs>
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
const sessionKey = process.argv[2] || "";
const requestedAt = Number(process.argv[3]) || Date.now();
const GIVE_UP_MS = 45_000; // hang up anyway if the goodbye never shows up
const MAX_SPEAKING_MS = 20_000; // longest we wait for the goodbye to finish playing
const GRACE_MS = 1_500; // let the last words finish on the phone line

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function liveStatus(callId) {
  const { stdout } = await run("openclaw", ["voicecall", "status", "--call-id", callId, "--json"], { timeout: 60_000 });
  return JSON.parse(stdout);
}

// The saved records can miss the short "speaking" phase, so ask the Gateway for the live state.
async function hangup(callId) {
  const deadline = Date.now() + MAX_SPEAKING_MS;
  while (Date.now() < deadline) {
    const call = await liveStatus(callId);
    if (call.endedAt) return;
    if (call.state !== "speaking") break;
    await sleep(500);
  }
  await sleep(GRACE_MS);
  await run("openclaw", ["voicecall", "end", "--call-id", callId], { timeout: 60_000 });
}

function goodbyeAppeared(record) {
  return (record.transcript || []).some((line) => line.speaker === "bot" && line.timestamp >= requestedAt);
}

// Follow the saved call records until this session's call has spoken its goodbye.
const tail = spawn("openclaw", ["voicecall", "tail", "--since", "50", "--poll", "250"], {
  stdio: ["ignore", "pipe", "ignore"],
  detached: true, // own process group, so stopTail() also ends openclaw's child process
});

function stopTail() {
  try {
    process.kill(-tail.pid, "SIGTERM");
  } catch {}
}
let callId;
let buffer = "";
let done = false;

async function finish(id) {
  if (done) return;
  done = true;
  stopTail();
  if (id) await hangup(id);
  process.exit(0);
}

tail.stdout.on("data", (chunk) => {
  buffer += chunk;
  const lines = buffer.split("\n");
  buffer = lines.pop();
  for (const line of lines) {
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    if (record.sessionKey !== sessionKey) continue;
    if (record.endedAt) {
      // Older calls to the same number show up in the history too; only stop if *this* call ended.
      if (record.callId === callId) {
        callId = undefined;
        if (record.endedAt >= requestedAt) finish(undefined);
      }
      continue;
    }
    callId = record.callId;
    if (goodbyeAppeared(record)) finish(callId).catch(fail);
  }
});

function fail(error) {
  stopTail();
  console.error("[vivid-tools] hangup failed:", error?.message || error);
  process.exit(1);
}

setTimeout(() => finish(callId).catch(fail), GIVE_UP_MS);
