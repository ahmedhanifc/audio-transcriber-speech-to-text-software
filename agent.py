import json
import os
import re
import subprocess
from pathlib import Path

CHATS = Path(__file__).resolve().parent / "chats"
TOOLS = ["Read", "Glob", "Grep"]


def ask(text, session, on_piece):
    return (codex if os.getenv("ASK_AGENT") == "codex" else claude)(text, session, on_piece)


def run(cmd, text):
    proc = subprocess.Popen(cmd, cwd=CHATS, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(text)
    proc.stdin.close()
    for line in proc.stdout:
        yield json.loads(line)
    proc.wait()


def claude(text, session, on_piece):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--tools", *TOOLS, "--allowedTools", *TOOLS, "--add-dir", str(Path.home())]
    if session:
        cmd += ["--resume", session]
    if os.getenv("CLAUDE_MODEL"):
        cmd += ["--model", os.getenv("CLAUDE_MODEL")]
    if os.getenv("CLAUDE_EFFORT"):
        cmd += ["--effort", os.getenv("CLAUDE_EFFORT")]
    for e in run(cmd, text):
        delta = e.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            on_piece(delta["text"])
        elif e["type"] == "result":
            return e.get("result", ""), e["session_id"]
    return "Claude stopped without an answer.", session


def codex(text, session, on_piece):
    # Codex sends whole messages, not word by word. It can't open images itself, so they go in with -i.
    cmd = ["codex", "exec", *(["resume", session] if session else []), "-", "--json", "--skip-git-repo-check", "-c", 'sandbox_mode="read-only"']
    if os.getenv("CODEX_MODEL"):
        cmd += ["-m", os.getenv("CODEX_MODEL")]
    if os.getenv("CODEX_EFFORT"):
        cmd += ["-c", f'model_reasoning_effort="{os.getenv("CODEX_EFFORT")}"']
    for image in re.findall(r"\S+\.(?:png|jpe?g)\b", text, re.I):
        cmd += ["-i", os.path.expanduser(image)]
    answer = "Codex stopped without an answer."
    for e in run(cmd, text):
        if e["type"] == "thread.started":
            session = e["thread_id"]
        elif e["type"] == "item.completed" and e["item"]["type"] == "agent_message":
            answer = e["item"]["text"]
            on_piece(answer + "\n\n")
        elif e["type"] == "turn.failed":
            answer = e["error"]["message"]
    return answer, session
