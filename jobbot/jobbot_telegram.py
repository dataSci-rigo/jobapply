#!/usr/bin/env python3
"""
JobBot's Telegram link for the voice interview (@apply2jobs_31416_bot).

The bot only carries messages. Claude, in a Claude Code session, reads each
answer and writes the next question, so nothing new is asked until he has
answered:

  python jobbot_telegram.py listen             # the service: long-poll, transcribe voice notes, fill the inbox
  python jobbot_telegram.py wait [--timeout S] # block until he has sent something new; print it, mark it read
  python jobbot_telegram.py turn FILE.json     # file his answer (interview.file_turn), send the next question
  python jobbot_telegram.py ask                # (re)send the open question
  python jobbot_telegram.py send TEXT          # a plain message to him
  python jobbot_telegram.py inbox [--all]      # print unread (or all) messages without marking them read

turn FILE.json has interview.TURN_SCHEMA's shape plus the answer being filed:
  {"answer": "...", "entries": [...], "confirm_proposals": [...], "drop_proposals": [...],
   "answered_questions": [...], "interests": [...],
   "next_question": {"text": "...", "why": "...", "options": [...]}, "done": false}
(empty lists may be left out). "keep_open": true files an addition to an earlier
answer and leaves the open question open (and re-sends it).

Only messages from TELEGRAM_ID are accepted. Voice notes are transcribed with
Gemini through OpenRouter and echoed back so he can see what was heard; the
audio is kept in data/voice/ for re-transcribing.
"""

import argparse
import base64
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import capabilities
import config
import data_loader
import interview

_API = "https://api.telegram.org/bot{token}/{method}"
_FILE = "https://api.telegram.org/file/bot{token}/{path}"
_AUDIO_FORMATS = {".oga": "ogg", ".ogg": "ogg", ".opus": "ogg", ".mp3": "mp3", ".m4a": "m4a",
                  ".wav": "wav", ".aac": "aac", ".flac": "flac"}
_CHUNK = 3900                    # Telegram's limit is 4096 characters per message
_INTRO = ("Voice interview for your job profile. Answer each question with a voice note (rambling is "
          "fine), a typed message, or a button. Claude reads every answer, files what it shows into your "
          "profile, and only then sends the next question. /skip skips a question; /ask repeats the "
          "current one. If Claude isn't running, your answers wait in the inbox and get read next time.")


def _log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {_scrub(msg)}", flush=True)


def _scrub(text: str) -> str:
    """Request errors quote the URL, and the URL holds the bot token."""
    token = config.TELEGRAM_BOT_TOKEN
    return text.replace(token, "<token>") if token else text


# ── Telegram API ──────────────────────────────────────────────────────────────

def _call(method: str, params: dict | None = None, http_timeout: float = 20):
    try:
        resp = requests.post(_API.format(token=config.TELEGRAM_BOT_TOKEN, method=method),
                             json=params or {}, timeout=http_timeout).json()
    except requests.RequestException as exc:
        raise RuntimeError(_scrub(f"{method}: {exc}")) from None
    if not resp.get("ok"):
        raise RuntimeError(f"{method}: {resp.get('description')}")
    return resp["result"]


def _download(file_id: str) -> tuple[bytes, str]:
    """(audio bytes, file suffix) for a Telegram file."""
    path = _call("getFile", {"file_id": file_id})["file_path"]
    try:
        resp = requests.get(_FILE.format(token=config.TELEGRAM_BOT_TOKEN, path=path), timeout=180)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(_scrub(f"download: {exc}")) from None
    return resp.content, Path(path).suffix.lower()


def send(text: str, options: list[str] | None = None, turn_id: str = "") -> None:
    """Send text in chunks; quick-reply buttons go on the last chunk."""
    chunks = [text[i:i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]
    for n, chunk in enumerate(chunks):
        params = {"chat_id": config.TELEGRAM_CHAT_ID, "text": chunk}
        if options and n == len(chunks) - 1:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": o[:60], "callback_data": f"o:{turn_id}:{i}"}] for i, o in enumerate(options)]}
        _call("sendMessage", params)


def _send_question(q: dict) -> None:
    text = q["question"] + (f"\n\n({q['why']})" if q.get("why") else "")
    send(text, q.get("options") or [], q["id"])


# ── Transcription ─────────────────────────────────────────────────────────────

def _glossary() -> str:
    """Names from his resume and profile, so the transcript spells his terms right."""
    data = data_loader.load_all()
    terms = [e.get("company", "") for e in data["resume"].get("experience", [])]
    terms += ["POC (Physical Optics Corporation)", "MIDS", "Capstone", "MagTek"]
    terms += [t for v in (data["resume"].get("skills") or {}).values() for t in (v if isinstance(v, list) else [])]
    terms += [e["name"] for e in data.get("capabilities", [])]
    terms += [p["name"] for p in capabilities.load_proposed()]
    seen, out = set(), []
    for t in terms:
        if isinstance(t, str) and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return ", ".join(out[:250])


def transcribe(audio: bytes, fmt: str) -> str | None:
    """Verbatim transcript, or None on failure / no speech."""
    if not config.OPENROUTER_KEY:
        _log("OPEN_ROUTER not set; cannot transcribe")
        return None
    prompt = ("Transcribe this voice note verbatim. Output only the transcript, no commentary. "
              "The speaker is describing his engineering work. Technical terms he may use — spell them "
              f"this way when you hear them: {_glossary()}. If it contains no speech, reply exactly: NO_SPEECH")
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_KEY}"},
            json={"model": config.TRANSCRIBE_MODEL, "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode(),
                                                        "format": fmt}},
            ]}]},
            timeout=300,
        ).json()
    except requests.RequestException as exc:
        _log(f"transcription failed: {exc}")
        return None
    text = (resp.get("choices") or [{}])[0].get("message", {}).get("content")
    if not text or text.strip() == "NO_SPEECH":
        _log(f"transcription empty: {str(resp)[:200]}")
        return None
    return text.strip()


# ── Inbox (listener appends; Claude reads) ────────────────────────────────────

def _read_cursor_path() -> Path:
    return config.TELEGRAM_STATE_PATH.with_name("telegram_read.json")


def load_inbox() -> list[dict]:
    path = config.TELEGRAM_INBOX_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(record: dict) -> dict:
    record = {"seq": len(load_inbox()) + 1, "at": datetime.now().isoformat(timespec="seconds"), **record}
    with config.TELEGRAM_INBOX_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_cursor() -> int:
    path = _read_cursor_path()
    return json.loads(path.read_text())["read"] if path.exists() else 0


def mark_read(seq: int) -> None:
    _read_cursor_path().write_text(json.dumps({"read": seq}))


def unread() -> list[dict]:
    cursor = read_cursor()
    return [r for r in load_inbox() if r["seq"] > cursor]


# ── Listener ──────────────────────────────────────────────────────────────────

def _allowed(chat_id) -> bool:
    return bool(config.TELEGRAM_CHAT_ID) and str(chat_id) == str(config.TELEGRAM_CHAT_ID)


def _option_text(data: str) -> str | None:
    """Button data o:<turn_id>:<i> -> the option text, if that question is still open."""
    try:
        _, turn_id, i = data.split(":")
        q = interview.current_question()
        return q["options"][int(i)] if q and q["id"] == turn_id else None
    except (ValueError, IndexError, KeyError):
        return None


def _handle_voice(m: dict) -> None:
    media = m.get("voice") or m.get("audio")
    _call("sendChatAction", {"chat_id": m["chat"]["id"], "action": "typing"})
    audio, suffix = _download(media["file_id"])
    config.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.VOICE_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{m['message_id']}{suffix or '.ogg'}"
    path.write_bytes(audio)
    duration = int(media.get("duration") or 0)
    text = transcribe(audio, _AUDIO_FORMATS.get(suffix, "ogg"))
    if not text:
        _append({"kind": "voice_failed", "text": "", "duration": duration, "file": str(path)})
        send("Couldn't transcribe that voice note (it's saved). Try again, or type the answer.")
        return
    _append({"kind": "voice", "text": text, "duration": duration, "file": str(path)})
    send(f"🎙 Heard ({duration // 60}:{duration % 60:02d}):\n{text}\n\n✓ Saved — Claude reads it next.")


def handle_update(u: dict) -> None:
    if "callback_query" in u:
        cq = u["callback_query"]
        if not _allowed(cq["message"]["chat"]["id"]):
            return
        _call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        text = _option_text(cq.get("data", ""))
        if text is None:
            send("That button belongs to an earlier question. /ask shows the current one.")
            return
        _append({"kind": "button", "text": text})
        send(f"✓ {text} — saved; add detail by voice or text if you like.")
        return

    m = u.get("message")
    if not m or not _allowed(m["chat"]["id"]):
        return
    if m.get("voice") or m.get("audio"):
        _handle_voice(m)
        return
    text = (m.get("text") or "").strip()
    if not text:
        send("I can read voice notes, text and buttons.")
        return
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command in ("/start", "/help"):
        send(_INTRO)
        q = interview.current_question()
        if q:
            _send_question(q)
    elif command == "/ask":
        q = interview.current_question()
        _send_question(q) if q else send("No question is open — Claude will send one.")
    elif command == "/skip":
        _append({"kind": "skip", "text": "(skipped)"})
        send("Skipped — Claude will pick a different question.")
    else:
        _append({"kind": "text", "text": text})
        send("✓ Saved — Claude reads it next.")


def _load_offset() -> int:
    path = config.TELEGRAM_STATE_PATH
    return json.loads(path.read_text()).get("offset", 0) if path.exists() else 0


def _save_offset(offset: int) -> None:
    config.TELEGRAM_STATE_PATH.write_text(json.dumps({"offset": offset}))


def listen() -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        sys.exit("Telegram_bot_apply2jobs and TELEGRAM_ID must be set in .env")
    _log(f"listening as @{_call('getMe')['username']}")
    offset = _load_offset()
    while True:
        try:
            updates = _call("getUpdates", {"offset": offset, "timeout": 30,
                                           "allowed_updates": ["message", "callback_query"]}, http_timeout=45)
        except RuntimeError as exc:
            _log(f"poll error: {exc}")
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                handle_update(u)
            except Exception:
                _log("update failed:\n" + traceback.format_exc())
            _save_offset(offset)


# ── Claude's side ─────────────────────────────────────────────────────────────

def _print(records: list[dict]) -> None:
    for r in records:
        extra = f" {r['duration'] // 60}:{r['duration'] % 60:02d}" if r.get("duration") else ""
        print(f"#{r['seq']} {r['at']} {r['kind']}{extra}\n{r['text'] or '(no transcript: ' + r.get('file', '') + ')'}\n")


def wait(timeout: float = 0, settle: float = 20, poll: float = 2) -> int:
    """Block until he sends something; then keep collecting until he's quiet for `settle` s."""
    start = time.time()
    while not unread():
        if timeout and time.time() - start > timeout:
            print("(nothing new)")
            return 1
        time.sleep(poll)
    count = len(unread())
    quiet_since = time.time()
    while time.time() - quiet_since < settle:          # a long answer often comes as several notes
        time.sleep(poll)
        if len(unread()) != count:
            count, quiet_since = len(unread()), time.time()
    records = unread()
    q = interview.current_question()
    if q:
        print(f"OPEN QUESTION ({q['id']}): {q['question']}\n")
    _print(records)
    mark_read(records[-1]["seq"])
    return 0


def turn(path: Path) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    answer = obj.pop("answer")
    keep_open = obj.pop("keep_open", False)
    res = interview.file_note(answer, obj) if keep_open else interview.file_turn(answer, obj)
    lines = []
    for s in res["saved"]:
        if s["kind"] == "entry":
            lines.append(f"• {s['name']} ({s['level']})")
        elif s["kind"] == "interest":
            lines.append(f"• {s['level']}: {s['name']}")
        elif s["kind"] == "dropped":
            lines.append(f"• dropped proposal: {s['name']}")
    if lines:
        send("Filed:\n" + "\n".join(lines))
    if keep_open:
        q = interview.current_question()
        if q:
            _send_question({**q, "question": "Still open: " + q["question"]})
    elif res["question"]:
        _send_question(res["question"])
    else:
        send("That covers everything on the agenda for now.")
    print(json.dumps({k: res[k] for k in ("answered_turn", "done")} | {"filed": len(res["saved"]),
                     "next": (res["question"] or {}).get("question")}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listen", help="run the bot (long-poll); the service")
    w = sub.add_parser("wait", help="block until he sends something new, print it, mark it read")
    w.add_argument("--timeout", type=float, default=0, help="seconds (0 = forever)")
    w.add_argument("--settle", type=float, default=20, help="quiet seconds before printing")
    t = sub.add_parser("turn", help="file an answer and send the next question")
    t.add_argument("file", type=Path)
    sub.add_parser("ask", help="(re)send the open question")
    s = sub.add_parser("send", help="send a plain message")
    s.add_argument("text")
    i = sub.add_parser("inbox", help="print messages without marking them read")
    i.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.cmd == "listen":
        listen()
    elif args.cmd == "wait":
        sys.exit(wait(args.timeout, args.settle))
    elif args.cmd == "turn":
        turn(args.file)
    elif args.cmd == "ask":
        q = interview.current_question()
        _send_question(q) if q else print("no open question")
    elif args.cmd == "send":
        send(args.text)
    elif args.cmd == "inbox":
        _print(load_inbox() if args.all else unread())


if __name__ == "__main__":
    main()
