# Audio Transcriber

Press **Right ⌘ Command** to start recording. Press it again to stop, transcribe, and paste at your cursor.

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

### 3. Add your OpenAI API key

For normal development from this folder, create `.env`:

```bash
printf 'OPENAI_API_KEY=your_key_here\n' > .env
```

For the packaged macOS app, create the personal config file:

```bash
mkdir -p ~/.audio-transcriber
printf 'OPENAI_API_KEY=your_key_here\n' > ~/.audio-transcriber/.env
```

The packaged app reads `~/.audio-transcriber/.env` because apps launched from Spotlight or Launchpad do not inherit terminal environment variables.

### 4. Grant macOS Permissions

Go to **System Settings → Privacy & Security** and grant your terminal app (Terminal / iTerm / VS Code) access to:

- **Accessibility** — required for global key listening and simulated paste
- **Microphone** — required for audio recording
- **Input Monitoring** — required for detecting key presses globally

> You may need to restart your terminal after granting permissions.

### 5. Run from Python

```bash
python main.py
```

## Build as a macOS app

Build the local app bundle:

```bash
bash build_macos_app.sh
```

Launch the built app:

```bash
open "dist/Audio Transcriber.app"
```

If it works, copy it to Applications:

```bash
cp -R "dist/Audio Transcriber.app" /Applications/
```

Then launch **Audio Transcriber** from Spotlight or Launchpad.

The packaged app needs its own macOS permissions, separate from Terminal or VS Code:

- **Accessibility**
- **Microphone**
- **Input Monitoring**

Logs are written to:

```text
~/Library/Logs/Audio Transcriber.log
```

## Usage

1. A small floating indicator appears at the bottom-center of your screen.
2. Press **Right ⌘ Command** to start recording.
3. Press **Right ⌘ Command** again to stop recording.
4. Audio is sent to OpenAI for transcription.
5. The final text is pasted at your current cursor position.

## How it works

```
Right ⌘ → Record audio (sounddevice)
    ↓
Right ⌘ again → Send to OpenAI transcription
    ↓
Copy to clipboard → Simulate Cmd+V → Text appears at cursor
```
