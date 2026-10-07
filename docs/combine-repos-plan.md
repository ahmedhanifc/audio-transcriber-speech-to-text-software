# Plan: one repo for dictation and meetings

Goal: `audio-transcriber` holds both tools. Nothing new is built yet; this is only the move.

## The new shape

```
audio-transcriber/
  main.py            dictation (Right Ctrl), unchanged
  requirements.txt
  meetings/          everything from local-meeting-minutes
    script.js  parakeet.py  package.json  test/
    data/            your old recordings and meetings.db
    .venv-asr/       the local speech model's Python
```

## Steps

- **Copy, don't move.** `local-meeting-minutes` stays as it is, so nothing is lost if something goes wrong.
- **No code changes needed.** `script.js` finds everything next to itself (`__dirname`), so it works from `meetings/`.
- **Fix one thing in the database.** `meetings.db` saves full chunk paths with the old folder name. Swap the old prefix for the new one, so `retry` still finds old chunks.
- **The model is not copied.** Its weights live in `~/.cache/huggingface`, shared by both folders.
- **Keep secrets and recordings out of git.** Add the meetings rules to `.gitignore`: `data/sessions/`, `meetings.db`, `.venv-asr/`, `node_modules/`, `config.json`.
- **README:** add a short "Meetings" section pointing to `meetings/README.md`.

## Done when

- `cd meetings && node --test` passes 34/34, as it does today.
- `node script.js list` shows the old sessions.
- `python main.py` still starts.
- Nothing is committed until you review it.
