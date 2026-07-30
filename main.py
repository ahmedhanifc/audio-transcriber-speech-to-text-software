"""
Audio LLM Transcriber
Press the platform recording key to toggle recording, transcribe, and paste at the cursor.
"""

import threading
import queue
import wave
import tempfile
import os
import sys
import tkinter as tk
from pathlib import Path
import logging

# ── Platform Detection ──────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform.startswith("darwin")
IS_LINUX = sys.platform.startswith("linux")


def validate_linux_session():
    """Fail clearly when Linux is using Wayland, which blocks pynput hotkeys."""
    if not IS_LINUX:
        return

    session_type = os.getenv("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.getenv("WAYLAND_DISPLAY")
    if session_type == "wayland" or wayland_display:
        raise RuntimeError(
            "Wayland does not allow this app's global hotkey listener. "
            "Log out, select a Cinnamon/Xorg (X11) session, and launch the app again."
        )


# Validate before importing pynput so its backend cannot fail with a cryptic error first.
validate_linux_session()

import numpy as np
import sounddevice as sd
import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, Controller as KeyboardController
from openai import OpenAI
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────────
load_dotenv()
USER_ENV_PATH = Path.home() / ".audio-transcriber" / ".env"
load_dotenv(USER_ENV_PATH, override=True)

if IS_MACOS:
    LOG_PATH = Path.home() / "Library" / "Logs" / "Audio Transcriber.log"
elif IS_WINDOWS:
    LOG_PATH = (
        Path(os.getenv("LOCALAPPDATA", Path.home()))
        / "Audio Transcriber"
        / "Audio Transcriber.log"
    )
else:
    LOG_PATH = (
        Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        / "audio-transcriber"
        / "audio-transcriber.log"
    )
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError(
        "OPENAI_API_KEY is missing. Add it to ~/.audio-transcriber/.env"
    )

SAMPLE_RATE = 16000      # 16kHz mono — perfect for Whisper
CHANNELS = 1
DTYPE = "int16"

client = OpenAI()         # picks up OPENAI_API_KEY from .env
kb = KeyboardController() # for simulating paste
# Platform-specific modifier key for paste.
paste_modifier = Key.cmd if IS_MACOS else Key.ctrl

# ── Shared State ────────────────────────────────────────────────────────────────
audio_frames = []          # list of numpy chunks recorded
is_recording = False
recording_mode = None      # "hold" or "toggle"
ui_queue = queue.Queue()   # send messages to the tkinter main thread


# ── Audio Recording ─────────────────────────────────────────────────────────────
stream = None

def log(message):
    """Print a message and save it to the app log file."""
    print(message)
    logging.info(message)

def get_builtin_mic_device():
    """Find and return the built-in microphone device index (cross-platform)."""
    devices = sd.query_devices()
    
    # Platform-specific keywords for built-in mics
    if IS_WINDOWS:
        keywords = ['realtek', 'conexant', 'id', 'high definition audio', 'microphone']
    elif IS_MACOS:
        keywords = ['macbook', 'built-in', 'internal']
    else:  # Linux and others
        keywords = ['built-in', 'internal', 'alsa', 'pulse']
    
    # Search for built-in mic
    for idx, device in enumerate(devices):
        device_name = device['name'].lower()
        if any(keyword in device_name for keyword in keywords):
            if device['max_input_channels'] > 0:  # Ensure it's an input device
                log(f"Found built-in mic: {device['name']} (device {idx})")
                return idx
    
    # Fallback to default if built-in not found
    log("Built-in mic not found, using default input device")
    return None

def start_recording(mode="hold"):
    global is_recording, audio_frames, stream, recording_mode
    if is_recording:
        return False

    audio_frames = []
    is_recording = True
    recording_mode = mode
    
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
    log("Recording started...")
    ui_queue.put("recording_locked" if mode == "toggle" else "recording")
    return True

def _audio_callback(indata, frames, time_info, status):
    if is_recording:
        audio_frames.append(indata.copy())

def stop_recording():
    global is_recording, stream, recording_mode
    if not is_recording:
        return False

    is_recording = False
    recording_mode = None
    if stream:
        stream.stop()
        stream.close()
        stream = None
    log("Recording stopped. Processing...")
    ui_queue.put("processing")
    threading.Thread(target=_process_audio, daemon=True).start()
    return True


# ── Processing Pipeline ─────────────────────────────────────────────────────────
def _process_audio():
    try:
        if not audio_frames:
            ui_queue.put("idle")
            return

        # Save recorded audio to a temp .wav file
        audio_data = np.concatenate(audio_frames, axis=0)

        # Diagnostics
        duration = len(audio_data) / SAMPLE_RATE
        rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
        log(f"Audio: {duration:.1f}s duration, {len(audio_frames)} chunks, RMS volume: {rms:.0f}")
        if rms < 50:
            log("Very low volume - mic may not be capturing audio!")

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
        log(f"Transcribed text: {final_text}")
        os.unlink(tmp.name)  # clean up temp file
        # Step 3: Paste at cursor
        pyperclip.copy(final_text)
        # Small delay to let clipboard settle, then simulate the platform paste shortcut.
        import time
        time.sleep(0.05)
        kb.press(paste_modifier)
        kb.press("v")
        kb.release("v")
        kb.release(paste_modifier)

        ui_queue.put("idle")
    except Exception:
        logging.exception("Failed to process audio")
        ui_queue.put("idle")
        raise


# ── Key Listener ─────────────────────────────────────────────────────────────────
recording_key_held = False
# Use the standalone right-side modifier as the recording toggle.
recording_key = Key.cmd_r if IS_MACOS else Key.ctrl_r

def on_press(key):
    global recording_key_held
    if key == recording_key and not recording_key_held:
        recording_key_held = True
        if is_recording:
            stop_recording()
        else:
            start_recording(mode="toggle")

def on_release(key):
    global recording_key_held
    if key == recording_key:
        recording_key_held = False


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

        # Cross-platform font setup
        if IS_MACOS:
            self.ui_font = ("SF Pro", 11)
            self.ui_font_small = ("SF Pro", 10)
        elif IS_WINDOWS:
            self.ui_font = ("Segoe UI", 11)
            self.ui_font_small = ("Segoe UI", 10)
        else:
            self.ui_font = ("sans-serif", 11)
            self.ui_font_small = ("sans-serif", 10)
        
        # Create a frame for rounded appearance
        self.frame = tk.Frame(root, bg="gray20", highlightthickness=0)
        self.frame.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.frame,
            text="",  # Start with no text (just a pill)
            font=self.ui_font,
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
        y = self.screen_h - 60
        self.root.geometry(f"{line_width}x{line_height}+{x}+{y}")

    def _set_active_geometry(self):
        """Set geometry for active state - expanded with text."""
        self.root.update_idletasks()
        win_w = max(self.label.winfo_reqwidth(), 140)
        win_h = max(self.label.winfo_reqheight(), 24)
        x = (self.screen_w - win_w) // 2
        y = self.screen_h - 60
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
                font=self.ui_font
            )
            self.root.configure(bg="#DC143C")
            self.frame.configure(bg="#DC143C")
            self._set_active_geometry()
        elif self.current_state == "recording_locked":
            self.label.config(
                text="  🔒 Listening...  ",
                bg="#B22222",
                fg="white",
                padx=10,
                pady=4,
                font=self.ui_font
            )
            self.root.configure(bg="#B22222")
            self.frame.configure(bg="#B22222")
            self._set_active_geometry()
        elif self.current_state == "processing":
            self.label.config(
                text="  ⏳ Processing...  ",
                bg="#2C2C2E",
                fg="white",
                padx=10,
                pady=4,
                font=self.ui_font
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
                    font=self.ui_font_small
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
    validate_linux_session()
    log("Audio LLM Transcriber")
    log(f"Loaded user env path: {USER_ENV_PATH}")
    log(f"Log path: {LOG_PATH}")
    if IS_WINDOWS:
        log("Press Right Ctrl to start recording. Press again to stop & transcribe.")
    elif IS_MACOS:
        log("Press Right Command to start recording. Press again to stop & transcribe.")
    elif IS_LINUX:
        log("Press Right Ctrl to start recording. Press again to stop & transcribe.")
    else:
        log("Press Right Ctrl to start recording. Press again to stop & transcribe.")
    log("Close the indicator window or Ctrl+C to quit.")
    log("Audio devices:")
    log(f"   System default input: {sd.query_devices(kind='input')['name']}")
    
    # Show which device will actually be used
    builtin_idx = get_builtin_mic_device()
    if builtin_idx is not None:
        builtin_name = sd.query_devices(builtin_idx)['name']
        log(f"   Using for recording: {builtin_name}")
    else:
        log("   Using for recording: (system default)")
    
    log(f"   Sample rate: {SAMPLE_RATE}Hz, Channels: {CHANNELS}")

    # Start the key listener in a background thread
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    # Run tkinter on the main thread (required by macOS)
    root = tk.Tk()
    app = Indicator(root)
    root.mainloop()

    listener.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Audio Transcriber crashed")
        raise
