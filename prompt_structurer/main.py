"""
Prompt Structurer
Right Cmd + Right Shift → grab clipboard text → floating toolbar appears →
pick a task type → LLM restructures the text into a clean prompt → ready to paste.
"""

import threading
import queue
import tkinter as tk

import pyperclip
from pynput import keyboard
from pynput.keyboard import Key
from openai import OpenAI
from dotenv import load_dotenv

# ── Config ───────────────────────────────────────────────────────────────────────
load_dotenv()

client = OpenAI()
ui_queue = queue.Queue()

# ── Task Prompts ─────────────────────────────────────────────────────────────────
TASK_PROMPTS = {
    "Code": (
        "You are a prompt engineer. The user has verbally dictated a coding request that may be rambling or unstructured. "
        "Rewrite it as a clear, concise, and well-structured prompt for a coding assistant (e.g. Claude Code). "
        "Include: the goal, relevant context, constraints, and expected output format. "
        "Do not add anything the user didn't mention. Output only the restructured prompt, no preamble."
    ),
    "Data Science": (
        "You are a prompt engineer. The user has verbally dictated a data science or analysis request that may be rambling. "
        "Rewrite it as a clear, structured prompt for a data science assistant. "
        "Include: the objective, dataset/data context if mentioned, methods or tools if mentioned, and expected output. "
        "Do not add anything the user didn't mention. Output only the restructured prompt, no preamble."
    ),
    "Writing": (
        "You are a prompt engineer. The user has verbally dictated a writing request that may be rambling or unstructured. "
        "Rewrite it as a clear, structured prompt for a writing assistant. "
        "Include: the topic, tone/style if mentioned, audience if mentioned, and format/length if mentioned. "
        "Do not add anything the user didn't mention. Output only the restructured prompt, no preamble."
    ),
    "Email": (
        "You are a prompt engineer. The user has verbally dictated an email drafting request that may be rambling. "
        "Rewrite it as a clear, structured prompt for drafting an email. "
        "Include: recipient context, purpose of the email, key points to convey, and tone if mentioned. "
        "Do not add anything the user didn't mention. Output only the restructured prompt, no preamble."
    ),
    "General": (
        "You are a prompt engineer. The user has verbally dictated a request that may be rambling or unstructured. "
        "Rewrite it as a clear, concise, well-structured prompt for an LLM assistant. "
        "Preserve all the user's intent. Remove filler, repetition, and unclear phrasing. "
        "Do not add anything the user didn't mention. Output only the restructured prompt, no preamble."
    ),
}

def _structure_text(raw_text, task_type):
    system_prompt = TASK_PROMPTS.get(task_type, TASK_PROMPTS["General"])
    print(f"🧠 Structuring as [{task_type}]...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.3,
    )
    structured = response.choices[0].message.content.strip()
    print("✅ Done.")
    return structured


# ── Key Listener ─────────────────────────────────────────────────────────────────
# shift_held = False

# def on_press(key):
#     global alt_held
#     if key == Key.alt_r:
#         alt_held = True
#     elif key == Key.shift_r and alt_held:
#         ui_queue.put("show_structurer")

# def on_release(key):
#     global alt_held
#     if key == Key.alt_r:
#         alt_held = False

shift_held = False

def on_press(key):
    global shift_held
    if key == Key.shift_r and not shift_held:
        shift_held = True
        ui_queue.put("show_structurer")

def on_release(key):
    global shift_held
    if key == Key.shift_r:
        shift_held = False


# ── Structurer Toolbar ───────────────────────────────────────────────────────────
class StructurerToolbar:
    TASK_TYPES = ["Code", "Data Science", "Writing", "Email", "General"]

    COLORS = {
        "Code":         "#4A90D9",
        "Data Science": "#7B68EE",
        "Writing":      "#50C878",
        "Email":        "#F4A460",
        "General":      "#A8A8A8",
    }

    def __init__(self, parent_root, on_done):
        self.on_done = on_done
        self.raw_text = pyperclip.paste()

        self.win = tk.Toplevel(parent_root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)
        self.win.configure(bg="#1C1C1E")

        screen_w = parent_root.winfo_screenwidth()
        screen_h = parent_root.winfo_screenheight()

        # Header
        tk.Label(
            self.win,
            text="Structure prompt as:",
            font=("SF Pro", 11),
            fg="#AEAEB2",
            bg="#1C1C1E",
            pady=8,
        ).pack(fill="x", padx=14)

        # Buttons
        btn_frame = tk.Frame(self.win, bg="#1C1C1E")
        btn_frame.pack(padx=10, pady=(0, 8))

        self.buttons = {}
        for task in self.TASK_TYPES:
            btn = tk.Button(
                btn_frame,
                text=task,
                font=("SF Pro", 10, "bold"),
                fg="white",
                bg=self.COLORS[task],
                activebackground=self.COLORS[task],
                activeforeground="white",
                relief="flat",
                padx=10,
                pady=5,
                cursor="hand2",
                command=lambda t=task: self._on_task_selected(t),
            )
            btn.pack(side="left", padx=4)
            self.buttons[task] = btn

        # Status
        self.status = tk.Label(
            self.win,
            text="",
            font=("SF Pro", 10),
            fg="#AEAEB2",
            bg="#1C1C1E",
            pady=4,
        )
        self.status.pack(fill="x", padx=14)

        # Close button
        tk.Button(
            self.win,
            text="✕  dismiss",
            font=("SF Pro", 10),
            fg="#AEAEB2",
            bg="#1C1C1E",
            activebackground="#1C1C1E",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            command=self._close,
        ).pack(pady=(0, 8))

        # Position: bottom center, above the indicator pill
        self.win.update_idletasks()
        win_w = self.win.winfo_reqwidth()
        win_h = self.win.winfo_reqheight()
        x = (screen_w - win_w) // 2
        y = screen_h - 120 - win_h - 12
        self.win.geometry(f"+{x}+{y}")

    def _on_task_selected(self, task_type):
        for btn in self.buttons.values():
            btn.config(state="disabled")
        self.status.config(text=f"Structuring as {task_type}...", fg="#AEAEB2")

        def run():
            try:
                structured = _structure_text(self.raw_text, task_type)
                pyperclip.copy(structured)
                self.win.after(0, lambda: self._on_done(task_type))
            except Exception as e:
                self.win.after(0, lambda: self._on_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, task_type):
        self.status.config(text="✓ Structured! Paste with Cmd+V", fg="#50C878")
        self.win.after(3000, self._close)

    def _on_error(self, error_msg):
        self.status.config(text=f"Error: {error_msg[:50]}", fg="#FF6B6B")
        for btn in self.buttons.values():
            btn.config(state="normal")

    def _close(self):
        self.win.destroy()
        self.on_done()


# ── Indicator (pill) ─────────────────────────────────────────────────────────────
class Indicator:
    """Minimal pill at bottom-center. Shows the structurer toolbar on hotkey."""

    def __init__(self, root):
        self.root = root
        self.root.title("")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg="gray20")

        self.frame = tk.Frame(root, bg="gray20", highlightthickness=0)
        self.frame.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.frame,
            text="",
            font=("SF Pro", 11),
            fg="white",
            bg="gray20",
            padx=0,
            pady=0,
        )
        self.label.pack()

        self.is_hovered = False
        self._structurer = None

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        self.root.bind("<Enter>", self._on_hover_enter)
        self.root.bind("<Leave>", self._on_hover_leave)
        self.label.bind("<Enter>", self._on_hover_enter)
        self.label.bind("<Leave>", self._on_hover_leave)

        self._set_pill()
        self._poll_queue()

    def _on_hover_enter(self, event=None):
        self.is_hovered = True
        self._set_pill()

    def _on_hover_leave(self, event=None):
        self.is_hovered = False
        self._set_pill()

    def _set_pill(self):
        if self.is_hovered:
            self.label.config(text="  ⌥⇧ Structure  ", bg="#4A4A4C", fg="white", padx=8, pady=3, font=("SF Pro", 10))
            self.root.configure(bg="#4A4A4C")
            self.frame.configure(bg="#4A4A4C")
            self.root.update_idletasks()
            w = max(self.label.winfo_reqwidth(), 140)
            h = max(self.label.winfo_reqheight(), 24)
        else:
            self.label.config(text="", bg="gray20", fg="white", padx=0, pady=0)
            self.root.configure(bg="gray20")
            self.frame.configure(bg="gray20")
            w, h = 60, 3

        x = (self.screen_w - w) // 2
        y = self.screen_h - 120
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _show_structurer(self):
        if self._structurer is not None:
            return
        text = pyperclip.paste()
        if not text or not text.strip():
            print("⚠️  Clipboard is empty — nothing to structure.")
            return
        print(f"📋 Clipboard text ({len(text)} chars). Opening toolbar.")
        self._structurer = StructurerToolbar(
            parent_root=self.root,
            on_done=self._on_structurer_closed,
        )

    def _on_structurer_closed(self):
        self._structurer = None

    def _poll_queue(self):
        while not ui_queue.empty():
            msg = ui_queue.get_nowait()
            if msg == "show_structurer":
                self._show_structurer()
        self.root.after(100, self._poll_queue)


# ── Main ─────────────────────────────────────────────────────────────────────────
def main():
    print("Prompt Structurer")
    print("Right ⌥ (Option) + Right Shift → structure clipboard text as a prompt")
    print("Close window or Ctrl+C to quit.\n")

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    root = tk.Tk()
    Indicator(root)
    root.mainloop()

    listener.stop()


if __name__ == "__main__":
    main()
