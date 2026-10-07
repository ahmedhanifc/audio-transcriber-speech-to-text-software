# Plan: the panel (Wispr Flow style)

Goal: click the pill, a window opens. Record meetings, see every transcription, write notes, delete things.

## How the pieces talk

- **Pill (`main.py`)** stays the boss. Right Ctrl dictation is unchanged. Hovering shows two buttons: ● Meeting (start/stop) and ☰ Open panel.
- **Panel (`panel.py`)** is an HTML window (pywebview), started as its own process. Tk and the webview each need the main thread.
- **Meetings (`meetings/script.js`)** stays the meeting engine. The panel runs `node script.js start/stop/list`, so nothing is rewritten.
- **One database** (`meetings/data/meetings.db`) holds everything. Two new tables: `dictations` and `notes`.

## Steps

- `script.js`: add `--json` to `list`/`status`, and a `delete <id>` command that removes the rows and the audio.
- `main.py`: save each dictation, add the hover buttons, turn the pill red during a meeting.
- `panel.py` tabs:
  - **Meetings:** start/stop, timer, "transcribing…", transcript, delete.
  - **History:** dictations, with copy and delete.
  - **Notes:** type, or dictate with Right Ctrl.
- `stop` can take minutes, so it runs in the background while the panel watches the database.

## Done when

- A meeting runs with no terminal.
- Deleting a meeting removes its audio.
- Tests pass.
