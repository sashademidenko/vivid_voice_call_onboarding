#!/usr/bin/env python3
"""Play a test call in mock mode: you type what the customer says, the agent answers.

Usage (Gateway must be running, see scripts/start-gateway.sh):
    python3 scripts/mock-call.py                       # random fake customer number
    python3 scripts/mock-call.py --to +15550001234     # specific fake number
    python3 scripts/mock-call.py --greeting "Custom first line"   (default: prompts/greeting.txt)

Type /end (or press Enter on an empty line) to hang up as the agent's side,
or /hangup to play a customer who hangs up mid-call.
"""
import argparse
import glob
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

WEBHOOK_URL = "http://127.0.0.1:3334/voice/webhook"
GREETING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts", "greeting.txt")
REPLY_TIMEOUT_S = 90
ENDED_STATES = {"completed", "ended", "hangup-bot", "hangup-user", "failed", "timeout"}


def openclaw_bin():
    found = shutil.which("openclaw")
    if found:
        return found
    candidates = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/openclaw")))
    if candidates:
        # openclaw needs `node` from the same folder on PATH.
        os.environ["PATH"] = os.path.dirname(candidates[-1]) + os.pathsep + os.environ.get("PATH", "")
        return candidates[-1]
    sys.exit("openclaw not found. Is OpenClaw installed?")


def openclaw(*args):
    result = subprocess.run([openclaw_bin(), *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"openclaw {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result.stdout


def send_event(call_id, event_type, **fields):
    body = json.dumps({"event": {"type": event_type, "callId": call_id, **fields}}).encode()
    req = urllib.request.Request(WEBHOOK_URL, data=body, headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


class TranscriptWatcher:
    """Follows `openclaw voicecall tail` in the background and keeps the latest transcript of one call."""

    def __init__(self, call_id):
        self.call_id = call_id
        self.lines = []
        self.ended = False
        self.proc = subprocess.Popen(
            [openclaw_bin(), "voicecall", "tail", "--since", "50", "--poll", "250"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            start_new_session=True,  # own process group, so stop() also ends openclaw's child process
        )
        threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self):
        for line in self.proc.stdout:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("callId") == self.call_id:
                self.lines = record.get("transcript") or []
                self.ended = self.ended or record.get("state") in ENDED_STATES

    def stop(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def wait_for_agent(self, shown):
        """Wait until the agent's reply appears after line number `shown`, print it, return new line count."""
        deadline = time.time() + REPLY_TIMEOUT_S
        while time.time() < deadline:
            lines = self.lines
            if len(lines) > shown and lines[-1].get("speaker") == "bot":
                for item in lines[shown:]:
                    if item.get("speaker") == "bot":
                        print(f"\n🤖 Agent: {item.get('text', '')}", flush=True)
                return len(lines)
            if self.ended:
                return len(lines)
            time.sleep(0.3)
        print("\n(no reply from the agent in time — check the Gateway window)", flush=True)
        return len(self.lines)


def main():
    parser = argparse.ArgumentParser(description="Test call in mock mode.")
    parser.add_argument("--to", help="fake customer number (default: random)")
    parser.add_argument("--greeting", help="agent's first line (default: prompts/greeting.txt)")
    args = parser.parse_args()

    to = args.to or f"+1555{random.randint(0, 9999999):07d}"
    greeting = args.greeting
    if not greeting:
        with open(GREETING_FILE, encoding="utf-8") as f:
            greeting = f.read().strip()

    out = openclaw("voicecall", "call", "--to", to, "--message", greeting)
    call_id = json.loads(out)["callId"]
    print(f"Test call to {to} started. /end = hang up, /hangup = customer hangs up.", flush=True)

    watcher = TranscriptWatcher(call_id)
    send_event(call_id, "call.answered")
    shown = watcher.wait_for_agent(0)

    said = ""
    try:
        while not watcher.ended:
            said = input("\n👤 Customer: ").strip()
            if not said or said == "/end":
                break
            if said == "/hangup":
                send_event(call_id, "call.ended", reason="hangup-user")
                watcher.ended = True
                print("\n(the customer hung up)", flush=True)
                break
            send_event(call_id, "call.speech", transcript=said, isFinal=True)
            shown = watcher.wait_for_agent(shown + 1)
        if watcher.ended and said != "/hangup":
            print("\n(the agent hung up)", flush=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        watcher.stop()
        if not watcher.ended:
            openclaw("voicecall", "end", "--call-id", call_id)
        print("\nCall ended.")


if __name__ == "__main__":
    main()
