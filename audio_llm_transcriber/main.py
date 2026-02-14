"""
Audio LLM Transcriber
Hold Left Command key to record voice → transcribes via Whisper → cleans up via GPT → pastes at cursor.
"""

import threading
import queue
import wave
import tempfile
import os
import tkinter as tk

import numpy as np
import sounddevice as sd
import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, Controller as KeyboardController
from openai import OpenAI
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────────
load_dotenv()

SAMPLE_RATE = 16000      # 16kHz mono — perfect for Whisper
CHANNELS = 1
DTYPE = "int16"

client = OpenAI()         # picks up OPENAI_API_KEY from .env
kb = KeyboardController() # for simulating Cmd+V paste

# ── Shared State ────────────────────────────────────────────────────────────────
audio_frames = []         # list of numpy chunks recorded
is_recording = False
ui_queue = queue.Queue()  # send messages to the tkinter main thread


# ── Audio Recording ─────────────────────────────────────────────────────────────
stream = None

def start_recording():
    global is_recording, audio_frames, stream
    audio_frames = []
    is_recording = True
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=_audio_callback,
    )
    stream.start()
    print("🔴 Recording started...")
    ui_queue.put("recording")

def _audio_callback(indata, frames, time_info, status):
    if is_recording:
        audio_frames.append(indata.copy())

def stop_recording():
    global is_recording, stream
    is_recording = False
    if stream:
        stream.stop()
        stream.close()
        stream = None
    print("⏹ Recording stopped. Processing...")
    ui_queue.put("processing")
    threading.Thread(target=_process_audio, daemon=True).start()


# ── Processing Pipeline ─────────────────────────────────────────────────────────
def _process_audio():
    if not audio_frames:
        ui_queue.put("idle")
        return

    # Save recorded audio to a temp .wav file
    audio_data = np.concatenate(audio_frames, axis=0)

    # Diagnostics
    duration = len(audio_data) / SAMPLE_RATE
    rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
    print(f"📊 Audio: {duration:.1f}s duration, {len(audio_frames)} chunks, RMS volume: {rms:.0f}")
    if rms < 50:
        print("⚠️  Very low volume — mic may not be capturing audio!")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data.tobytes())

    # Step 1: Whisper transcription
    with open(tmp.name, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    raw_text = transcript.text
    print("Raw text: ", raw_text)
    os.unlink(tmp.name)  # clean up temp file

    # Step 2: GPT cleanup
    cleaned = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a text cleanup assistant. "
                    "Fix grammar and punctuation in the following transcribed speech. "
                    "Do not change the meaning or add new content. "
                    "Return only the cleaned text, nothing else."
                ),
            },
            {"role": "user", "content": raw_text},
        ],
    )
    final_text = cleaned.choices[0].message.content.strip()
    print("Cleaned text: ", final_text)

    # Step 3: Paste at cursor
    pyperclip.copy(final_text)
    # Small delay to let clipboard settle, then simulate Cmd+V
    import time
    time.sleep(0.05)
    kb.press(Key.cmd)
    kb.press("v")
    kb.release("v")
    kb.release(Key.cmd)

    ui_queue.put("idle")


# ── Key Listener ─────────────────────────────────────────────────────────────────
cmd_held = False

def on_press(key):
    global cmd_held
    if key == Key.cmd_r and not cmd_held:
        cmd_held = True
        start_recording()

def on_release(key):
    global cmd_held
    if key == Key.cmd_r and cmd_held:
        cmd_held = False
        stop_recording()


# ── Tkinter UI (floating indicator) ─────────────────────────────────────────────
class Indicator:
    """Tiny floating dot at bottom-center of screen."""

    def __init__(self, root):
        self.root = root
        self.root.title("")
        self.root.overrideredirect(True)           # no title bar
        self.root.attributes("-topmost", True)      # always on top
        self.root.attributes("-alpha", 0.85)        # slight transparency
        self.root.configure(bg="black")

        self.label = tk.Label(
            root,
            text="  🎙 Ready  ",
            font=("SF Pro", 14),
            fg="white",
            bg="#1a1a1a",
            padx=12,
            pady=6,
        )
        self.label.pack()

        # Position at bottom center
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = self.root.winfo_width()
        x = (screen_w - win_w) // 2
        y = screen_h - 150
        self.root.geometry(f"+{x}+{y}")

        self._poll_queue()

    def _poll_queue(self):
        """Check the UI queue for state changes."""
        while not ui_queue.empty():
            state = ui_queue.get_nowait()
            if state == "recording":
                self.label.config(text="  🔴 Recording...  ", bg="#8B0000", fg="white")
                self.root.configure(bg="#8B0000")
            elif state == "processing":
                self.label.config(text="  ⏳ Processing...  ", bg="#333333", fg="white")
                self.root.configure(bg="#333333")
            elif state == "idle":
                self.label.config(text="  🎙 Ready  ", bg="#1a1a1a", fg="white")
                self.root.configure(bg="#1a1a1a")

        self.root.after(100, self._poll_queue)  # poll every 100ms


# ── Main ─────────────────────────────────────────────────────────────────────────
def main():
    print("Audio LLM Transcriber")
    print("Hold Right ⌘ (Command) to record. Release to transcribe & paste.")
    print("Close the indicator window or Ctrl+C to quit.")
    print(f"\n🎤 Audio devices:")
    print(f"   Default input: {sd.query_devices(kind='input')['name']}")
    print(f"   Sample rate: {SAMPLE_RATE}Hz, Channels: {CHANNELS}\n")

    # Start the key listener in a background thread
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    # Run tkinter on the main thread (required by macOS)
    root = tk.Tk()
    app = Indicator(root)
    root.mainloop()

    listener.stop()


if __name__ == "__main__":
    main()
