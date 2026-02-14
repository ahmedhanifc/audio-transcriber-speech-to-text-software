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

def get_builtin_mic_device():
    """Find and return the built-in MacBook microphone device index."""
    devices = sd.query_devices()
    
    # Search for built-in microphone
    for idx, device in enumerate(devices):
        device_name = device['name'].lower()
        print()
        # Look for MacBook built-in mic keywords
        if any(keyword in device_name for keyword in ['macbook', 'built-in', 'internal']):
            if device['max_input_channels'] > 0:  # Ensure it's an input device
                print(f"✓ Found built-in mic: {device['name']} (device {idx})")
                return idx
    
    # Fallback to default if built-in not found
    print("⚠️  Built-in mic not found, using default input device")
    return None

def start_recording():
    global is_recording, audio_frames, stream
    audio_frames = []
    is_recording = True
    
    # Get the built-in microphone device
    builtin_mic = get_builtin_mic_device()
    
    stream = sd.InputStream(
        device=builtin_mic,  # Explicitly use built-in mic
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

    # Transcription with cleanup prompt (single API call)
    with open(tmp.name, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file,
            language="en",  # English-only for better accuracy
            # prompt=(
            #     "Clean transcription. Remove filler words (um, uh, like, you know). "
            #     "Use proper punctuation and grammar. "
            #     "If the speaker corrects themselves, keep only the corrected version."
            # ),
        )
    final_text = transcript.text.strip()
    print("Transcribed text: ", final_text)
    os.unlink(tmp.name)  # clean up temp file
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
    """Minimal pill-shaped indicator at bottom-center with hover expansion."""

    def __init__(self, root):
        self.root = root
        self.root.title("")
        self.root.overrideredirect(True)           # no title bar
        self.root.attributes("-topmost", True)      # always on top
        self.root.attributes("-alpha", 0.9)         # slight transparency
        self.root.configure(bg="gray20")

        # Create a frame for rounded appearance
        self.frame = tk.Frame(root, bg="gray20", highlightthickness=0)
        self.frame.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.frame,
            text="",  # Start with no text (just a pill)
            font=("SF Pro", 11),
            fg="white",
            bg="gray20",
            padx=0,
            pady=0,
        )
        self.label.pack()

        # State tracking
        self.current_state = "idle"
        self.is_hovered = False
        
        # Position at bottom center - start as small pill
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        
        # Bind hover events
        self.root.bind("<Enter>", self._on_hover_enter)
        self.root.bind("<Leave>", self._on_hover_leave)
        self.label.bind("<Enter>", self._on_hover_enter)
        self.label.bind("<Leave>", self._on_hover_leave)
        
        self._set_idle_geometry()
        self._poll_queue()

    def _on_hover_enter(self, event=None):
        """Handle mouse entering the indicator."""
        self.is_hovered = True
        self._update_display()

    def _on_hover_leave(self, event=None):
        """Handle mouse leaving the indicator."""
        self.is_hovered = False
        self._update_display()

    def _set_idle_geometry(self):
        """Set geometry for idle state - small pill shape."""
        if self.is_hovered:
            # Expanded on hover
            line_width = 120
            line_height = 20
        else:
            # Minimal pill
            line_width = 60
            line_height = 3
        
        x = (self.screen_w - line_width) // 2
        y = self.screen_h - 120
        self.root.geometry(f"{line_width}x{line_height}+{x}+{y}")

    def _set_active_geometry(self):
        """Set geometry for active state - expanded with text."""
        self.root.update_idletasks()
        win_w = max(self.label.winfo_reqwidth(), 140)
        win_h = max(self.label.winfo_reqheight(), 24)
        x = (self.screen_w - win_w) // 2
        y = self.screen_h - 120
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def _update_display(self):
        """Update the display based on current state and hover."""
        if self.current_state == "recording":
            self.label.config(
                text="  🔴 Recording...  ",
                bg="#DC143C",
                fg="white",
                padx=10,
                pady=4,
                font=("SF Pro", 11)
            )
            self.root.configure(bg="#DC143C")
            self.frame.configure(bg="#DC143C")
            self._set_active_geometry()
        elif self.current_state == "processing":
            self.label.config(
                text="  ⏳ Processing...  ",
                bg="#2C2C2E",
                fg="white",
                padx=10,
                pady=4,
                font=("SF Pro", 11)
            )
            self.root.configure(bg="#2C2C2E")
            self.frame.configure(bg="#2C2C2E")
            self._set_active_geometry()
        elif self.current_state == "idle":
            if self.is_hovered:
                # Show text on hover
                self.label.config(
                    text="  Ready  ",
                    bg="#4A4A4C",
                    fg="white",
                    padx=8,
                    pady=3,
                    font=("SF Pro", 10)
                )
                self.root.configure(bg="#4A4A4C")
                self.frame.configure(bg="#4A4A4C")
            else:
                # Minimal pill
                self.label.config(
                    text="",
                    bg="gray20",
                    fg="white",
                    padx=0,
                    pady=0
                )
                self.root.configure(bg="gray20")
                self.frame.configure(bg="gray20")
            self._set_idle_geometry()

    def _poll_queue(self):
        """Check the UI queue for state changes."""
        while not ui_queue.empty():
            state = ui_queue.get_nowait()
            self.current_state = state
            self._update_display()

        self.root.after(100, self._poll_queue)  # poll every 100ms


# ── Main ─────────────────────────────────────────────────────────────────────────
def main():
    print("Audio LLM Transcriber")
    print("Hold Right ⌘ (Command) to record. Release to transcribe & paste.")
    print("Close the indicator window or Ctrl+C to quit.")
    print(f"\n🎤 Audio devices:")
    print(f"   System default input: {sd.query_devices(kind='input')['name']}")
    
    # Show which device will actually be used
    builtin_idx = get_builtin_mic_device()
    if builtin_idx is not None:
        builtin_name = sd.query_devices(builtin_idx)['name']
        print(f"   Using for recording: {builtin_name} ✓")
    else:
        print(f"   Using for recording: (system default)")
    
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

