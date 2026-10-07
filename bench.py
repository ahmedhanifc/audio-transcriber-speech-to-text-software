"""
Find the best mic + model for your voice.

  MIC=bluez python bench.py record airpods     # read 20 sentences into a mic
  python bench.py score                        # every mic x every model -> wrong-word %
"""

import re
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path.home() / ".audio-transcriber" / ".env", override=True)

from voice import find_mic, transcribe

BENCH = Path(__file__).parent / "bench"
MODELS = ["gpt-4o-transcribe", "gpt-transcribe", "gemini-3.5-transcribe"]
SENTENCES = [
    "Can you push the changes to GitHub?",
    "Open a pull request and add a short description.",
    "The meeting is moved to Thursday at three thirty.",
    "Please check why the build failed on the main branch.",
    "I want the panel to show my dictation history.",
    "Use the Gemini API key from the dot env file.",
    "My laptop is a Dell Latitude seven four nine zero.",
    "Run the tests again and tell me what broke.",
    "Send the invoice to the finance team by Friday.",
    "The AirPods microphone sounds worse than the laptop one.",
    "Can we add a copy button next to each transcript?",
    "Delete the old recordings from last week.",
    "PipeWire switched the headset into handsfree mode.",
    "Write a plan in less than two hundred words.",
    "The JSON response has a missing field called status.",
    "Install the package with pip and restart the terminal.",
    "Rename the function to transcribe and update the imports.",
    "We need to compare OpenAI and Google on accuracy.",
    "Remind me to call the bank about the insurance claim.",
    "Thanks, that looks good, let's ship it.",
]


def record(label):
    out = BENCH / label
    out.mkdir(parents=True, exist_ok=True)
    mic = find_mic()
    print(f"Mic: {sd.query_devices(mic)['name'] if mic is not None else 'system default'}")
    for i, sentence in enumerate(SENTENCES):
        input(f"\n[{i + 1}/{len(SENTENCES)}] Press Enter, then say:\n  {sentence}\n")
        frames = []
        with sd.InputStream(device=mic, samplerate=16000, channels=1, dtype="int16",
                            callback=lambda data, *_: frames.append(data.copy())):
            input("  Recording... press Enter when done.")
        with wave.open(str(out / f"{i:02d}.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(np.concatenate(frames).tobytes())
    print(f"\nSaved to {out}")


def words(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", "")).split()


def wer(ref, hyp):
    """Wrong words / total words, by word-level edit distance."""
    r, h = words(ref), words(hyp)
    row = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, row[0] = row[0], i
        for j in range(1, len(h) + 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return row[-1] / len(r)


def score():
    results = []
    for mic_dir in sorted(p for p in BENCH.iterdir() if p.is_dir()):
        for model in MODELS:
            errors = []
            for clip in sorted(mic_dir.glob("*.wav")):
                ref = SENTENCES[int(clip.stem)]
                try:
                    hyp = transcribe(str(clip), model)
                except Exception as e:
                    print(f"  {model} failed: {e}")
                    break
                errors.append(wer(ref, hyp))
                if hyp.lower().strip(" .?!") != ref.lower().strip(" .?!"):
                    print(f"  {mic_dir.name}/{model}: heard \"{hyp}\"")
            if errors:
                results.append((sum(errors) / len(errors), mic_dir.name, model))

    print(f"\n{'mic':<12} {'model':<24} wrong words")
    for err, mic, model in sorted(results):
        print(f"{mic:<12} {model:<24} {err:.1%}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record(sys.argv[2])
    elif sys.argv[1:2] == ["score"]:
        score()
    else:
        print(__doc__)
