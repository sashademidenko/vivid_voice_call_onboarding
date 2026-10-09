// Tools for the Noah call agent:
// - send_help: a stub. It does NOT send anything yet: it appends the request to a JSONL file so we
//   can check that the agent called it correctly. Replace recordRequest() with a call to the
//   Vivid Business app chat API once that is available.
// - end_call: hangs up the current call after the goodbye (see hangup-when-quiet.mjs).
import { spawn } from "node:child_process";
import { appendFile, mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const DEFAULT_OUTPUT_FILE = join(homedir(), ".openclaw", "vivid", "help-requests.jsonl");
const HELP_TYPES = ["link", "guide"];
const HANGUP_HELPER = join(dirname(fileURLToPath(import.meta.url)), "hangup-when-quiet.mjs");

async function recordRequest(outputFile: string, entry: Record<string, unknown>) {
  await mkdir(dirname(outputFile), { recursive: true });
  await appendFile(outputFile, JSON.stringify(entry) + "\n", "utf8");
}

function textResult(text: string, details: Record<string, unknown>) {
  return { content: [{ type: "text", text }], details };
}

export default definePluginEntry({
  id: "vivid-tools",
  name: "Vivid Tools",
  description: "Tools for the Vivid Business onboarding call agent.",
  register(api) {
    const outputFile: string = api.pluginConfig?.outputFile || DEFAULT_OUTPUT_FILE;

    api.registerTool((toolContext) => ({
      name: "send_help",
      label: "Send help",
      description:
        "Send the customer help in their Vivid Business app chat: a link to continue the account opening " +
        "where they left off, or a short guide for the step they're stuck on. Call only after the customer agreed.",
      parameters: {
        type: "object",
        properties: {
          help_type: {
            type: "string",
            enum: HELP_TYPES,
            description: '"link" = continue where they left off; "guide" = short guide for the step they are stuck on.',
          },
          stuck_step: {
            type: "string",
            description: "For a guide: a few words on the step they are stuck on, e.g. 'document upload'.",
          },
        },
        required: ["help_type"],
      },
      async execute(_toolCallId, params) {
        const helpType = typeof params?.help_type === "string" ? params.help_type : "";
        if (!HELP_TYPES.includes(helpType)) {
          return textResult('help_type must be "link" or "guide".', { ok: false });
        }
        const entry = {
          at: new Date().toISOString(),
          sessionKey: toolContext?.sessionKey ?? null,
          help_type: helpType,
          stuck_step: typeof params?.stuck_step === "string" ? params.stuck_step : null,
        };
        await recordRequest(outputFile, entry);
        return textResult("Help sent to the customer's Vivid Business app chat.", { ok: true, ...entry });
      },
    }));

    // The built-in voice_call tool can't be used from inside the call agent (it starts a second
    // voice-call runtime and breaks the live call), so hanging up goes through a background helper.
    api.registerTool((toolContext) => ({
      name: "end_call",
      label: "End call",
      description:
        "Hang up the current phone call once your goodbye has been spoken. Call this first, then give the " +
        "goodbye as your final reply; the line is cut right after it finishes.",
      parameters: { type: "object", properties: {} },
      async execute() {
        const sessionKey = toolContext?.sessionKey;
        if (!sessionKey) return textResult("No active call session.", { ok: false });
        const helper = spawn(process.execPath, [HANGUP_HELPER, sessionKey, String(Date.now())], {
          detached: true,
          stdio: "ignore",
        });
        helper.unref();
        return textResult("The call will end after your goodbye.", { ok: true });
      },
    }));
  },
});
