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
const GRACE_MS = 1_500; // let the last words finish on the phone line

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function hangup(callId) {
  await sleep(GRACE_MS);
  await run("openclaw", ["voicecall", "end", "--call-id", callId], { timeout: 60_000 });
}

function goodbyeSpoken(record) {
  const saidSomethingSinceRequest = (record.transcript || []).some(
    (line) => line.speaker === "bot" && line.timestamp >= requestedAt,
  );
  return saidSomethingSinceRequest && record.state !== "speaking";
}

// Follow the saved call records until this session's call has spoken its goodbye.
const tail = spawn("openclaw", ["voicecall", "tail", "--since", "50", "--poll", "250"], {
  stdio: ["ignore", "pipe", "ignore"],
});
let callId;
let buffer = "";
let done = false;

async function finish(id) {
  if (done) return;
  done = true;
  tail.kill();
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
    if (goodbyeSpoken(record)) finish(callId).catch(fail);
  }
});

function fail(error) {
  console.error("[vivid-tools] hangup failed:", error?.message || error);
  process.exit(1);
}

setTimeout(() => finish(callId).catch(fail), GIVE_UP_MS);
