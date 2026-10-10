"""
Audio LLM Transcriber
Press the platform recording key to toggle recording, transcribe, and paste at the cursor.
"""

import threading
import queue
import wave
import tempfile
import os
import re
import subprocess
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
from dotenv import load_dotenv
from panel import POPUP_SIZE, ROOT, meeting_status, meetings_cli, save_dictation
from voice import find_mic, transcribe

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
MIN_VOLUME = 100        # clips quieter than this are silence; the model invents words for them

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

def get_mic_device():
    mic = find_mic()
    if mic is None:
        log("No preferred mic found, using default input device")
    else:
        log(f"Using mic: {sd.query_devices(mic)['name']} (device {mic})")
    return mic

def start_recording(mode="hold"):
    global is_recording, audio_frames, stream, recording_mode
    if is_recording:
        return False

    audio_frames = []
    is_recording = True
    recording_mode = mode
    
    mic = get_mic_device()

    stream = sd.InputStream(
        device=mic,
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
        if rms < MIN_VOLUME:
            log("Too quiet - not sending (silent clips come back as made-up words)")
            ui_queue.put("no_sound")
            return

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

        final_text = transcribe(tmp.name)
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
        save_dictation(final_text)

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


# ── Tkinter UI (floating bubble) ─────────────────────────────────────────────
def get_monitors():
    """Return (x, y, w, h) for each active monitor. X11 only; empty elsewhere."""
    if not IS_LINUX:
        return []
    try:
        out = subprocess.run(
            ["xrandr", "--listactivemonitors"], capture_output=True, text=True, timeout=2
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # Lines look like: " 1: +HDMI-1 1920/480x1080/270+1920+0  HDMI-1"
    return [
        (int(x), int(y), int(w), int(h))
        for w, h, x, y in re.findall(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", out)
    ]


BUBBLE = 44
COLORS = {"idle": "#26262A", "recording": "#DC143C", "recording_locked": "#B22222", "processing": "#5A5A60", "no_sound": "#B8860B"}
SPOT_PATH = LOG_PATH.parent / "bubble.txt"


class Indicator:
    """Round bubble with a waveform. Drag it anywhere; tap it to open or close the panel."""

    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.canvas = tk.Canvas(root, width=BUBBLE, height=BUBBLE, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.circle = self.canvas.create_oval(0, 0, BUBBLE - 1, BUBBLE - 1, width=3)
        for i, h in enumerate((8, 14, 20, 14, 8)):
            x = BUBBLE // 2 + (i - 2) * 6
            self.canvas.create_line(x, (BUBBLE - h) // 2, x, (BUBBLE + h) // 2, fill="white", width=3, capstyle="round")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        self.current_state = "idle"
        self.meeting = meeting_status()
        self.panel = None
        self.panel_open = False
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.monitors = get_monitors()

        x, y = self._home()
        self.root.geometry(f"{BUBBLE}x{BUBBLE}+{x}+{y}")
        self._make_round()
        self._start_panel()

        self._update_display()
        self._poll_queue()
        self._poll_meeting()

    def _home(self):
        """The saved spot, or bottom-right if it's gone (say, its monitor was unplugged)."""
        try:
            x, y = map(int, SPOT_PATH.read_text().split())
            if not self.monitors or any(mx <= x < mx + mw and my <= y < my + mh for mx, my, mw, mh in self.monitors):
                return x, y
        except (OSError, ValueError):
            pass
        mx, my, mw, mh = self._monitor_at(*self.root.winfo_pointerxy())
        return mx + mw - BUBBLE - 24, my + mh - BUBBLE - 80

    def _make_round(self):
        """Tk windows are square, so cut this one into a circle with the X11 shape extension."""
        self.root.update()
        if not IS_LINUX:
            return
        from Xlib.display import Display
        from Xlib.ext import shape
        d = Display()
        win = d.create_resource_object("window", self.root.winfo_id()).query_tree().parent  # Tk's outer wrapper
        r = BUBBLE / 2
        rows = [(x, y, BUBBLE - 2 * x, 1) for y in range(BUBBLE) for x in [round(r - (r * r - (y + 0.5 - r) ** 2) ** 0.5)]]
        win.shape_rectangles(shape.SO.Set, shape.SK.Bounding, 0, 0, 0, rows)
        d.sync()
        d.close()

    def _press(self, e):
        self.start = (e.x_root, e.y_root)
        self.grab = (e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty())
        self.dragged = False

    def _drag(self, e):
        if abs(e.x_root - self.start[0]) + abs(e.y_root - self.start[1]) > 5:
            self.dragged = True
        if self.dragged:
            self.root.geometry(f"+{e.x_root - self.grab[0]}+{e.y_root - self.grab[1]}")

    def _release(self, e):
        if not self.dragged:
            self.panel_open = not self.panel_open
            self._show_panel() if self.panel_open else self._send_panel("hide")
            return
        SPOT_PATH.write_text(f"{e.x_root - self.grab[0]} {e.y_root - self.grab[1]}")
        self.root.update_idletasks()
        if self.panel_open:
            self._show_panel()

    def _start_panel(self):
        self.panel = subprocess.Popen([sys.executable, str(ROOT / "panel.py"), "--popup"], stdin=subprocess.PIPE, text=True)

    def _send_panel(self, cmd):
        if self.panel.poll() is not None:
            self._start_panel()
        self.panel.stdin.write(cmd + "\n")
        self.panel.stdin.flush()

    def _show_panel(self):
        """Next to the bubble, on the side with room, kept inside the bubble's monitor."""
        w, h = POPUP_SIZE
        bx, by = self.root.winfo_rootx(), self.root.winfo_rooty()
        mx, my, mw, mh = self._monitor_at(bx + BUBBLE // 2, by + BUBBLE // 2)
        x = bx + BUBBLE + 8 if bx + BUBBLE + 8 + w <= mx + mw else bx - w - 8
        x = min(max(x, mx + 8), mx + mw - w - 8)
        y = min(max(by + BUBBLE // 2 - h // 2, my + 8), my + mh - h - 8)
        self._send_panel(f"show {x} {y}")

    def _monitor_at(self, px, py):
        """Bounds of the monitor holding this point, or the whole screen if unknown."""
        for x, y, w, h in self.monitors:
            if x <= px < x + w and y <= py < y + h:
                return x, y, w, h
        return 0, 0, self.screen_w, self.screen_h

    def _update_display(self):
        """Colour shows the state; a red ring means a meeting is recording."""
        fill = COLORS[self.current_state]
        ring = "#FF453A" if self.meeting and self.current_state == "idle" else fill
        self.canvas.configure(bg=fill)
        self.canvas.itemconfig(self.circle, fill=fill, outline=ring)
        if self.current_state == "no_sound":
            self.root.after(2000, lambda: self.current_state == "no_sound" and ui_queue.put("idle"))

    def _poll_queue(self):
        """Check the UI queue for state changes."""
        while not ui_queue.empty():
            self.current_state = ui_queue.get_nowait()
            self._update_display()

        self.root.after(100, self._poll_queue)  # poll every 100ms

    def _poll_meeting(self):
        """Follow the meeting recorder, whoever started it."""
        was = self.meeting
        self.meeting = meeting_status()
        if bool(was) != bool(self.meeting):
            self._update_display()
        self.root.after(1000, self._poll_meeting)


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
    log(f"Speech model: {os.getenv('STT_MODEL') or 'gpt-4o-transcribe'}")
    log("Audio devices:")
    log(f"   System default input: {sd.query_devices(kind='input')['name']}")
    
    # Show which device will actually be used
    mic_idx = get_mic_device()
    if mic_idx is not None:
        log(f"   Using for recording: {sd.query_devices(mic_idx)['name']}")
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
