# Audio LLM Transcriber

Hold **Left ⌘ Command** to record your voice. Release to transcribe and paste at your cursor.

## Setup

### 1. Install PortAudio (required by sounddevice)
```bash
brew install portaudio
```

### 2. Install Python dependencies
```bash
conda activate audio_app
pip install -r requirements.txt
```

### 3. Grant macOS Permissions

Go to **System Settings → Privacy & Security** and grant your terminal app (Terminal / iTerm / VS Code) access to:

- **Accessibility** — required for global key listening and simulated paste
- **Microphone** — required for audio recording
- **Input Monitoring** — required for detecting key presses globally

> You may need to restart your terminal after granting permissions.

### 4. Run
```bash
python main.py
```

## Usage

1. A small floating indicator appears at the bottom-center of your screen showing **🎙 Ready**
2. **Hold Left ⌘** — indicator turns red, recording starts
3. **Release Left ⌘** — recording stops, audio is sent to OpenAI Whisper for transcription
4. The transcript is cleaned up by GPT-4o-mini (grammar + punctuation fixes)
5. The final text is pasted at your current cursor position

## How it works

```
Hold Left ⌘ → Record audio (sounddevice)
    ↓
Release → Send to Whisper API (transcription)
    ↓
Send transcript → GPT-4o-mini (grammar/punctuation cleanup)
    ↓
Copy to clipboard → Simulate Cmd+V → Text appears at cursor
```
