import json
import os
import re
import subprocess
from pathlib import Path

CHATS = Path(__file__).resolve().parent / "chats"
TOOLS = ["Read", "Glob", "Grep"]
CLAUDE_MODELS = ["opus", "sonnet", "haiku"]
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def choices():
    """Agent -> model -> efforts for the Ask tab. "" means the CLI's own setting."""
    found = {"claude": {m: CLAUDE_EFFORTS for m in ["", *CLAUDE_MODELS]}, "codex": {"": []}}
    cache = Path.home() / ".codex" / "models_cache.json"
    if cache.exists():
        for m in json.loads(cache.read_text())["models"]:
            if m["visibility"] == "list":
                found["codex"][m["slug"]] = [r["effort"] for r in m["supported_reasoning_levels"]]
                found["codex"][""] += [r["effort"] for r in m["supported_reasoning_levels"] if r["effort"] not in found["codex"][""]]
    # .env picks the starting choice, even a model that isn't listed.
    default = {"agent": os.getenv("ASK_AGENT") or "claude"}
    for who, models in found.items():
        model = os.getenv(f"{who.upper()}_MODEL", "")
        models.setdefault(model, models[""])
        default[who] = [model, os.getenv(f"{who.upper()}_EFFORT", "")]
    return {**found, "default": default}


def ask(text, session, on_piece, who, model, effort):
    return (codex if who == "codex" else claude)(text, session, on_piece, model, effort)


def run(cmd, text):
    proc = subprocess.Popen(cmd, cwd=CHATS, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(text)
    proc.stdin.close()
    for line in proc.stdout:
        yield json.loads(line)
    proc.wait()


def claude(text, session, on_piece, model, effort):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--tools", *TOOLS, "--allowedTools", *TOOLS, "--add-dir", str(Path.home())]
    if session:
        cmd += ["--resume", session]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    for e in run(cmd, text):
        delta = e.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            on_piece(delta["text"])
        elif e["type"] == "result":
            return e.get("result", ""), e["session_id"]
    return "Claude stopped without an answer.", session


def codex(text, session, on_piece, model, effort):
    # Codex sends whole messages, not word by word. It can't open images itself, so they go in with -i.
    cmd = ["codex", "exec", *(["resume", session] if session else []), "-", "--json", "--skip-git-repo-check", "-c", 'sandbox_mode="read-only"']
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
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
