import json
import subprocess
from pathlib import Path

CHATS = Path(__file__).resolve().parent / "chats"
TOOLS = ["Read", "Glob", "Grep"]


def ask(text, session, on_piece):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--tools", *TOOLS, "--allowedTools", *TOOLS, "--add-dir", str(Path.home())]
    if session:
        cmd += ["--resume", session]
    proc = subprocess.Popen(cmd, cwd=CHATS, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(text)
    proc.stdin.close()
    for line in proc.stdout:
        e = json.loads(line)
        delta = e.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            on_piece(delta["text"])
        elif e["type"] == "result":
            proc.wait()
            return e.get("result", ""), e["session_id"]
    proc.wait()
    return "Claude stopped without an answer.", session
