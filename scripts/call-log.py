#!/usr/bin/env python3
"""Writes one row per finished call to data/calls.csv (for import into a database).

Started automatically by scripts/start-gateway.sh. It follows the voice-call records and, when a
call ends, asks an OpenAI model to fill in the answers from the transcript. The transcript itself
is not stored in the table.

    python3 scripts/call-log.py            # follow calls (what start-gateway.sh runs)
    python3 scripts/call-log.py --rebuild  # rebuild the whole table from past calls, then exit
"""
import argparse
import csv
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_FILE = os.path.join(PROJECT_DIR, "data", "calls.csv")
HELP_FILE = os.path.join(PROJECT_DIR, "data", "help-requests.jsonl")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
MODEL = os.environ.get("CALL_LOG_MODEL", "gpt-5.6-luna")

COLUMNS = [
    "call_id", "phone", "started_at", "duration_sec", "end_reason", "outcome",
    "stop_reason", "stop_category", "wants_help", "help_type", "help_sent",
    "still_planning", "planned_when", "callback_at", "other_bank_reason", "do_not_call",
]
OUTCOMES = ["completed", "busy_callback", "other_bank", "upset", "voicemail", "no_answer", "cut_off"]
STOP_CATEGORIES = ["documents", "verification", "fees", "other_bank", "no_time", "other", ""]

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "stop_reason", "stop_category", "wants_help", "help_type",
                 "still_planning", "planned_when", "callback_at", "other_bank_reason", "do_not_call"],
    "properties": {
        "outcome": {"type": "string", "enum": OUTCOMES},
        "stop_reason": {"type": "string"},
        "stop_category": {"type": "string", "enum": STOP_CATEGORIES},
        "wants_help": {"type": "string", "enum": ["yes", "no", ""]},
        "help_type": {"type": "string", "enum": ["link", "guide", "human_support", ""]},
        "still_planning": {"type": "string", "enum": ["yes", "no", "unsure", ""]},
        "planned_when": {"type": "string"},
        "callback_at": {"type": "string"},
        "other_bank_reason": {"type": "string"},
        "do_not_call": {"type": "string", "enum": ["yes", "no"]},
    },
}

EXTRACTION_PROMPT = """You read the transcript of an outbound phone call. "bot" is Noah, an AI assistant
of Vivid Business; "user" is a person who started opening a business account but didn't finish.
Fill in the fields using only what the person actually said. Use "" when it wasn't discussed.

outcome: pick the FIRST that applies, in this order:
- voicemail: a voicemail or answering machine picked up
- no_answer: the person never really spoke
- upset: the person was irritated or upset at any point (even if the call ended politely)
- other_bank: they chose another bank or changed their mind
- busy_callback: the person was busy and a call-back was discussed
- cut_off: the call ended abruptly, before the agent's goodbye
- completed: none of the above; the call went through to a normal goodbye
stop_reason: why they didn't finish opening the account (Q1), in a few of their own words.
  Not why they can't talk right now: if they were only busy, leave it "".
stop_category: documents (which/upload documents), verification (identity checks, photos), fees (price),
  other_bank, no_time (busy, forgot, postponed), other.
wants_help: answer to "Would you like some help to finish it?".
help_type: what they picked: link, guide, or human_support (wants to talk to a person).
still_planning / planned_when: answer to "Are you still planning to open the account? Roughly when?"
  (planned_when in their words, e.g. "next week").
callback_at: when to call back, if they were busy (their words).
other_bank_reason: why they chose another bank (their words).
do_not_call: "yes" only if they asked not to be called again."""


def load_env():
    """Read keys from .env if they aren't in the environment already (for manual runs)."""
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def openclaw_bin():
    found = shutil.which("openclaw")
    if found:
        return found
    candidates = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/openclaw")))
    if candidates:
        os.environ["PATH"] = os.path.dirname(candidates[-1]) + os.pathsep + os.environ.get("PATH", "")
        return candidates[-1]
    sys.exit("openclaw not found. Is OpenClaw installed?")


def iso(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def help_sent_call_ids():
    """Call IDs for which the agent actually called send_help (from the stub's log)."""
    ids = set()
    try:
        with open(HELP_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    session_key = json.loads(line).get("sessionKey") or ""
                except ValueError:
                    continue
                ids.add(session_key.rsplit(":", 1)[-1])
    except FileNotFoundError:
        pass
    return ids


def extract_answers(record):
    transcript = "\n".join(f'{line.get("speaker")}: {line.get("text", "")}' for line in record.get("transcript") or [])
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": f"Call end reason: {record.get('endReason', '')}\n\nTranscript:\n{transcript}"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "call_answers", "strict": True, "schema": EXTRACTION_SCHEMA},
        },
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(json.loads(resp.read())["choices"][0]["message"]["content"])


def build_row(record):
    started = record.get("startedAt")
    answered = record.get("answeredAt")
    ended = record.get("endedAt")
    row = {
        "call_id": record.get("callId", ""),
        "phone": record.get("to", ""),
        "started_at": iso(started),
        "duration_sec": round((ended - (answered or started)) / 1000) if ended and (answered or started) else "",
        "end_reason": record.get("endReason", ""),
    }
    person_spoke = any(line.get("speaker") == "user" for line in record.get("transcript") or [])
    if person_spoke:
        row.update(extract_answers(record))
        if not row.get("stop_reason"):
            row["stop_category"] = ""
    else:
        # Nobody answered: no need to ask the model.
        row["outcome"] = "voicemail" if "voicemail" in (record.get("endReason") or "") else "no_answer"
        row["do_not_call"] = "no"
    row["help_sent"] = "yes" if row["call_id"] in help_sent_call_ids() else "no"
    return {column: row.get(column, "") for column in COLUMNS}


def logged_call_ids():
    try:
        with open(CSV_FILE, newline="", encoding="utf-8") as f:
            return {row["call_id"] for row in csv.DictReader(f)}
    except FileNotFoundError:
        return set()


def append_row(row):
    os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
    new_file = not os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def finished_records(lines):
    """Latest saved state of every call that has ended, in the order they ended."""
    finished = {}
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("callId") and record.get("endedAt"):
            finished[record["callId"]] = record
    return sorted(finished.values(), key=lambda r: r["endedAt"])


def log_call(record, done):
    if record["callId"] in done:
        return
    try:
        append_row(build_row(record))
        done.add(record["callId"])
        print(f"[call-log] logged call {record['callId']}", flush=True)
    except Exception as error:  # keep following other calls
        print(f"[call-log] could not log call {record['callId']}: {error}", file=sys.stderr, flush=True)


def rebuild():
    proc = subprocess.Popen(
        [openclaw_bin(), "voicecall", "tail", "--since", "1000000", "--poll", "250"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=20)  # tail never exits on its own; history is printed first
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate()
    records = finished_records(out.splitlines())
    if os.path.exists(CSV_FILE):
        os.replace(CSV_FILE, CSV_FILE + ".bak")
    done = set()
    for record in records:
        log_call(record, done)
    print(f"[call-log] rebuilt {CSV_FILE} with {len(done)} call(s)")


def follow():
    done = logged_call_ids()
    # --since 500 also picks up calls that ended while this script wasn't running.
    proc = subprocess.Popen(
        [openclaw_bin(), "voicecall", "tail", "--since", "500", "--poll", "500"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for line in proc.stdout:
            for record in finished_records([line]):
                log_call(record, done)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Per-call results table (data/calls.csv).")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the table from all past calls")
    args = parser.parse_args()
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (.env).")
    if args.rebuild:
        rebuild()
    else:
        follow()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
