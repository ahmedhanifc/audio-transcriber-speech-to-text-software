# Audio Transcriber

Press **Right Ctrl** (macOS: Right Command), speak, and press it again. Your words are pasted where your cursor is.

Hover over the pill for **● Meeting** (records mic + speakers, see [`meetings/`](meetings/README.md)). Keep the mouse on it and the panel (meetings, history, notes) pops up above; move away or click elsewhere to close it.

The panel's **Ask** tab chats with [Claude Code](https://claude.com/claude-code) (install it and log in first). It runs read-only, and answers show Markdown and math. To give it context, paste a file or screenshot path into your message. Chats are saved in `chats/`.

## Install

```bash
sudo apt install libportaudio2 portaudio19-dev python3-tk xclip   # Linux. macOS: brew install portaudio
pip install -r requirements.txt
cp .env.example .env    # then add your API keys
```

## Run

```bash
python main.py
```

## Settings (`.env`)

| Setting | What you pick | Default |
|---|---|---|
| `MIC` | Part of the mic name: `bluez` = AirPods, `pulse` = laptop or wired headset | first headset found |
| `STT_MODEL` | `gpt-4o-transcribe`, `gpt-transcribe`, `gemini-3.5-transcribe` | `gpt-4o-transcribe` |
| `KEYWORDS` | Words it gets wrong, comma-separated, e.g. `GitHub, PipeWire` | none |
| `BACKUP_DIR` | Folder for a daily copy of `meetings.db`, e.g. `~/Backups/audio-transcriber` | no backup |

Wired headset on Linux: the laptop mic stays selected until you switch it:

```bash
pactl set-source-port alsa_input.pci-0000_00_1f.3.analog-stereo analog-input-headset-mic
```

## Find your best mic + model

```bash
MIC=bluez python bench.py record airpods   # read 20 sentences
MIC=pulse python bench.py record laptop
python bench.py score                      # table of wrong-word % per mic and model
```

Put the winner in `.env`.

## Permissions

- **Linux:** log in with an X11 (Cinnamon/Xorg) session, not Wayland.
- **macOS:** allow Accessibility, Microphone and Input Monitoring for your terminal.
