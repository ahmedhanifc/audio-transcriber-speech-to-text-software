# local-meeting-minutes

A local-first meeting minutes tool inspired by Granola. It does **not** join your
meetings. It records what your machine hears, your mic and your speakers, and
turns it into a transcript afterwards. The transcription runs on your own
machine. No audio leaves it.

Works with anything that makes sound: Zoom, Meet, Teams, Slack huddles, a phone on
speaker. Nothing is installed into the meeting.

Design: [docs/architecture.md](docs/architecture.md) · Research:
[docs/feasibility-local-meeting-minutes.md](docs/feasibility-local-meeting-minutes.md)

## Requirements

* Linux with PipeWire or PulseAudio
* `ffmpeg`
* Node 24+, for the built-in `node:sqlite`
   * the CLI has no npm dependencies
* Python 3.10+ with `venv`
* No GPU needed
   * transcription runs on the CPU, on purpose
   * on an i7-8650U laptop, a 31 minute meeting transcribes in 5 minutes
   * the old whisper default took about 18 minutes for the same recording, and
     found 1% more words
   * more cores make it faster; see
     [docs/parakeet-on-cpu.md](docs/parakeet-on-cpu.md) for measurements

## Setup

Build the transcription environment once. It is about 200 MB.

```bash
python3 -m venv .venv-asr
.venv-asr/bin/pip install "onnx-asr[cpu,hub]"
```

The first transcription also downloads about 650 MB of model weights, Parakeet
TDT 0.6B v3 plus Silero VAD. That is the one step that needs internet.
Everything after it is offline.

## Use

```bash
node script.js start "Weekly sync"   # begin recording
node script.js status                # how long have I been recording?
node script.js stop                  # stop, then transcribe
node script.js list                  # every session and its state
```

To record now and transcribe later, add `--no-transcribe`. Useful when you want
the laptop quiet, or all your cores back for something else:

```bash
node script.js start --no-transcribe "Weekly sync"   # audio only
node script.js stop                                  # stops, keeps the audio
node script.js transcribe                            # transcribe it when you like
```

`stop` prints where the transcript landed:

```
data/sessions/<id>/
  audio.ogg         stereo capture, you on the left, everyone else on the right
  chunks/           silence-trimmed pieces the model actually reads
  transcript.json   canonical segments, with timestamps
  transcript.md     read this one
```

There is no summarising step by design. Open `transcript.md` and paste it into
whatever LLM you like ([architecture.md §0](docs/architecture.md)).

If something failed, or the machine died mid-meeting:

```bash
node script.js retry      # resume from the last completed step
node script.js recover    # pick up anything left unfinished
```

Retries never redo work already done. Chunks are cached by content hash.

### Re-running the transcriber on one session

The folder name under `data/sessions/` is the session id. If it was never
transcribed, audio-only or failed part way:

```bash
node script.js transcribe 2026-09-01_10-01-31
```

To redo one that already has a transcript, say after changing the model in
`config.json`, clear its cached chunks first, otherwise the old text is carried
straight over:

```bash
node -e "const {DatabaseSync}=require('node:sqlite');const db=new DatabaseSync('data/meetings.db');const id=process.argv[1];db.prepare('DELETE FROM chunks WHERE session_id = ?').run(id);db.prepare(\"UPDATE sessions SET state='recorded', transcribe=1, error=NULL WHERE id=?\").run(id);" 2026-09-01_10-01-31
node script.js retry 2026-09-01_10-01-31
```

It needs `audio.ogg` to still be there, so `deleteAudioAfterTranscription` must
have been off when it first ran.

## Cost

Nothing. The model runs on your machine.

## Config

Optional `config.json` in the project root overrides any default in
[`script.js`](script.js). The ones you might touch:

* `parakeetModel`, default `nemo-parakeet-tdt-0.6b-v2`
   * English only. For another language use `nemo-parakeet-tdt-0.6b-v3`,
     which covers 25 European ones, and set `parakeetQuantization` to `null`
* `parakeetQuantization`, default `int8`
   * `null` downloads the full-precision weights instead: 2.4 GB, and about
     1.8× the wait. It matters for v3, hardly at all for v2
* `parakeetMaxSegmentSeconds`, default `30`
   * the longest run of unbroken speech handed to the model at once
* `deleteAudioAfterTranscription`, default `false`
   * left off so you can re-transcribe later with a better model

## Tests

```bash
node --test
```

The one that matters is `test/capture.test.js`. It proves your mic lands on the
left channel and system audio on the right.
