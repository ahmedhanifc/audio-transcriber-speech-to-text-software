import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pyperclip

ROOT = Path(__file__).resolve().parent
MEETINGS = ROOT / "meetings"
DB_PATH = MEETINGS / "data" / "meetings.db"
PID_FILE = MEETINGS / "data" / "recorder.pid"


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS dictations (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL);"
    )
    return conn


def save_dictation(text):
    with db() as conn:
        conn.execute("INSERT INTO dictations (text, created_at) VALUES (?, ?)", (text, datetime.now().isoformat()))


def meeting_status():
    try:
        rec = json.loads(PID_FILE.read_text())
        os.kill(rec["pid"], 0)
        return rec
    except (OSError, ValueError):
        return None


def meetings_cli(*args):
    # Own session: Ctrl+C on the pill must not kill a transcription in progress.
    return subprocess.Popen(["node", "script.js", *args], cwd=MEETINGS, start_new_session=True)


class Api:
    def state(self):
        with db() as conn:
            rows = lambda sql: [dict(r) for r in conn.execute(sql)]
            return {
                "recording": meeting_status(),
                "meetings": rows("SELECT id, title, state, error, transcribe, started_at, ended_at FROM sessions ORDER BY started_at DESC"),
                "dictations": rows("SELECT * FROM dictations ORDER BY id DESC"),
                "notes": rows("SELECT * FROM notes ORDER BY id DESC"),
            }

    def start_meeting(self, title):
        meetings_cli("start", *title.split()).wait()

    def stop_meeting(self):
        meetings_cli("stop")

    def delete_meeting(self, id):
        meetings_cli("delete", id).wait()

    def transcript(self, id):
        path = MEETINGS / "data" / "sessions" / id / "transcript.md"
        return path.read_text() if path.exists() else ""

    def copy(self, text):
        pyperclip.copy(text)

    def delete_dictation(self, id):
        with db() as conn:
            conn.execute("DELETE FROM dictations WHERE id = ?", (id,))

    def add_note(self, text):
        with db() as conn:
            conn.execute("INSERT INTO notes (text, created_at) VALUES (?, ?)", (text, datetime.now().isoformat()))

    def delete_note(self, id):
        with db() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (id,))


if __name__ == "__main__":
    import webview

    webview.create_window("Audio Transcriber", str(ROOT / "panel.html"), js_api=Api(), width=760, height=640)
    webview.start(gui="qt")
