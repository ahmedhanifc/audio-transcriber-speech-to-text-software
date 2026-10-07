# Plan: make dictation hear you right

Goal: find the best mic and model for your voice, then use them.

## 1. Stop the made-up words (`main.py`)

- If the recording is silent (volume below 50), don't send it. Flash "No sound — check mic" on the pill.
- Retry once if the AirPods mic starts silent (it takes a moment to wake up).

## 2. Three mics to test

- **AirPods Pro 3:** Bluetooth "call mode", low quality. Turn on mSBC in PipeWire.
- **White wired headset:** Linux is still using the laptop mic, not the headset mic. Switch the input to "Headset Microphone".
- **Laptop mic:** fine in a quiet room.
- Add `MIC=` to `.env` so you choose the mic, instead of the app guessing.

## 3. Test bench (`bench/`)

- `record.py`: you read the same 20 sentences into each mic. Each clip is saved with the correct text.
- `score.py`: sends every clip to `gpt-4o-transcribe`, `gpt-transcribe` and `gemini-3.5-transcribe`, then counts wrong words (WER, using `jiwer`).
- Prints a table showing the best mic and model pair.

## 4. Use the winner

- Add `STT_MODEL=` to `.env`. Support both OpenAI and Gemini.
- Add `KEYWORDS=` (your project names, jargon) as hints to the model.

## Done when

- No more foreign-language junk.
- The table picks a winner, and the app uses it.
