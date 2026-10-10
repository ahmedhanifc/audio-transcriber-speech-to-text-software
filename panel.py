import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pyperclip
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
MEETINGS = ROOT / "meetings"
DB_PATH = MEETINGS / "data" / "meetings.db"
PID_FILE = MEETINGS / "data" / "recorder.pid"
FIELDS = ("did", "learned", "blocked", "better", "people")
ARCHIVED = "status = 'done' AND done_at < ?"


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    first_run = not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'").fetchone()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dictations (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reflections (day TEXT PRIMARY KEY, did TEXT, learned TEXT, blocked TEXT, better TEXT, people TEXT, updated_at TEXT);

        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text, content='notes', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
            INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
        END;

        CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts USING fts5(did, learned, blocked, better, people, content='reflections');
        CREATE TRIGGER IF NOT EXISTS reflections_ai AFTER INSERT ON reflections BEGIN
            INSERT INTO reflections_fts(rowid, did, learned, blocked, better, people) VALUES (new.rowid, new.did, new.learned, new.blocked, new.better, new.people);
        END;
        CREATE TRIGGER IF NOT EXISTS reflections_ad AFTER DELETE ON reflections BEGIN
            INSERT INTO reflections_fts(reflections_fts, rowid, did, learned, blocked, better, people) VALUES ('delete', old.rowid, old.did, old.learned, old.blocked, old.better, old.people);
        END;
        CREATE TRIGGER IF NOT EXISTS reflections_au AFTER UPDATE ON reflections BEGIN
            INSERT INTO reflections_fts(reflections_fts, rowid, did, learned, blocked, better, people) VALUES ('delete', old.rowid, old.did, old.learned, old.blocked, old.better, old.people);
            INSERT INTO reflections_fts(rowid, did, learned, blocked, better, people) VALUES (new.rowid, new.did, new.learned, new.blocked, new.better, new.people);
        END;
    """)
    if "status" not in {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}:
        conn.execute("ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'todo'")
        conn.execute("ALTER TABLE notes ADD COLUMN done_at TEXT")
    if first_run:
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
        conn.execute("INSERT INTO reflections_fts(reflections_fts) VALUES ('rebuild')")
        conn.commit()
    return conn


def backup():
    folder = os.getenv("BACKUP_DIR")
    if not folder:
        return
    target = Path(folder).expanduser() / f"meetings-{date.today()}.db"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(target)
    with db() as conn:
        conn.backup(out)
    out.close()


def archive_cutoff():
    return (datetime.now() - timedelta(days=3)).isoformat()


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


def transcript_path(id):
    return MEETINGS / "data" / "sessions" / id / "transcript.md"


def write_transcript(id, text):
    tmp = transcript_path(id).with_suffix(".md.tmp")
    tmp.write_text(text)
    tmp.replace(transcript_path(id))


def edited(id):
    md, raw = transcript_path(id), transcript_path(id).with_suffix(".json")
    return md.exists() and raw.exists() and md.stat().st_mtime > raw.stat().st_mtime


def meetings_cli(*args):
    # Own session: Ctrl+C on the pill must not kill a transcription in progress.
    return subprocess.Popen(["node", "script.js", *args], cwd=MEETINGS, start_new_session=True)


class Api:
    def state(self):
        with db() as conn:
            rows = lambda sql: [dict(r) for r in conn.execute(sql)]
            meetings = rows("SELECT id, title, state, error, transcribe, started_at, ended_at FROM sessions ORDER BY started_at DESC")
            for m in meetings:
                m["edited"] = edited(m["id"])
            return {
                "recording": meeting_status(),
                "meetings": meetings,
                "dictations": rows("SELECT * FROM dictations ORDER BY id DESC"),
                "notes": [dict(r) for r in conn.execute(f"SELECT * FROM notes WHERE NOT ({ARCHIVED}) ORDER BY id", (archive_cutoff(),))],
            }

    def start_meeting(self, title):
        meetings_cli("start", *title.split()).wait()

    def stop_meeting(self):
        meetings_cli("stop")

    def rename_meeting(self, id, title):
        meetings_cli("rename", id, *title.split()).wait()

    def delete_meeting(self, id):
        meetings_cli("delete", id).wait()

    def transcript(self, id):
        path = transcript_path(id)
        return path.read_text() if path.exists() else ""

    def save_transcript(self, id, text):
        write_transcript(id, text)

    def append_transcript(self, id, text):
        old = self.transcript(id).rstrip("\n")
        head = "" if "\n## My notes\n" in old else "\n\n## My notes\n"
        write_transcript(id, f"{old}{head}\n- {text}\n")

    def copy(self, text):
        pyperclip.copy(text)

    def delete_dictation(self, id):
        with db() as conn:
            conn.execute("DELETE FROM dictations WHERE id = ?", (id,))

    def add_note(self, text):
        with db() as conn:
            conn.execute("INSERT INTO notes (text, created_at) VALUES (?, ?)", (text, datetime.now().isoformat()))

    def update_note(self, id, text):
        with db() as conn:
            conn.execute("UPDATE notes SET text = ? WHERE id = ?", (text, id))

    def set_note_status(self, id, status):
        with db() as conn:
            conn.execute("UPDATE notes SET status = ?, done_at = ? WHERE id = ?", (status, datetime.now().isoformat() if status == "done" else None, id))

    def delete_note(self, id):
        with db() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (id,))

    def get_reflection(self, day):
        with db() as conn:
            row = conn.execute("SELECT * FROM reflections WHERE day = ?", (day,)).fetchone()
        return dict(row) if row else {}

    def save_reflection(self, day, fields):
        with db() as conn:
            conn.execute(
                f"INSERT INTO reflections (day, {', '.join(FIELDS)}, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                f"ON CONFLICT(day) DO UPDATE SET {', '.join(f'{f} = excluded.{f}' for f in FIELDS)}, updated_at = excluded.updated_at",
                (day, *(fields.get(f, "") for f in FIELDS), datetime.now().isoformat()),
            )

    def search(self, query):
        # Quote each word so punctuation can't break the FTS5 syntax; * matches while typing.
        match = " ".join('"' + w.replace('"', '""') + '"*' for w in query.split())
        with db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT 'note' AS kind, substr(n.created_at, 1, 10) AS day, n.created_at AS at, snippet(notes_fts, 0, char(2), char(3), '…', 12) AS snip "
                "FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? "
                "UNION ALL "
                "SELECT 'reflection', r.day, r.updated_at, snippet(reflections_fts, -1, char(2), char(3), '…', 12) "
                "FROM reflections_fts JOIN reflections r ON r.rowid = reflections_fts.rowid WHERE reflections_fts MATCH ? "
                "ORDER BY at DESC",
                (match, match),
            )]

    def days(self):
        with db() as conn:
            reflections = {r["day"]: dict(r) for r in conn.execute("SELECT * FROM reflections")}
            notes = {}
            for n in conn.execute(f"SELECT * FROM notes WHERE {ARCHIVED} ORDER BY done_at", (archive_cutoff(),)):
                notes.setdefault(n["done_at"][:10], []).append(dict(n))
        return [{"day": d, "reflection": reflections.get(d), "notes": notes.get(d, [])} for d in sorted(reflections.keys() | notes.keys(), reverse=True)]

    def typing(self, busy):
        # The pill reads this to keep the popup open while a text box has focus.
        print("busy" if busy else "free", flush=True)


POPUP_SIZE = (720, 520)


def listen(window):
    # The pill sends "show X Y" and "hide"; stdin closes when the pill exits.
    for line in sys.stdin:
        cmd, *args = line.split()
        if cmd == "show":
            window.move(int(args[0]), int(args[1]))
            window.show()
            window.evaluate_js("refresh(true)")
        elif cmd == "hide":
            window.hide()
    window.destroy()


if __name__ == "__main__":
    import webview

    load_dotenv()
    backup()
    if "--popup" in sys.argv:
        w, h = POPUP_SIZE
        window = webview.create_window("Audio Transcriber", str(ROOT / "panel.html"), js_api=Api(), width=w, height=h, frameless=True, on_top=True, hidden=True)
        webview.start(listen, window, gui="qt")
    else:
        webview.create_window("Audio Transcriber", str(ROOT / "panel.html"), js_api=Api(), width=760, height=640)
        webview.start(gui="qt")
