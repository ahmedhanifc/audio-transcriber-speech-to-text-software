# Audio Transcriber

Press the recording hotkey to start recording. Press it again to stop, transcribe, and paste at your cursor.

- **Windows / Linux Mint:** Right Ctrl
- **macOS:** Right Command

## Setup

### 1. Install PortAudio (required by sounddevice)

macOS:

```bash
brew install portaudio
```

Linux Mint / Ubuntu:

```bash
sudo apt install libportaudio2 portaudio19-dev python3-tk xclip
```

On Windows, install the Python dependencies below; the `sounddevice` wheel normally includes the required PortAudio support.

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

### 4. Platform permissions and session requirements

#### macOS

Go to **System Settings → Privacy & Security** and grant your terminal app (Terminal / iTerm / VS Code) access to:

- **Accessibility** — required for global key listening and simulated paste
- **Microphone** — required for audio recording
- **Input Monitoring** — required for detecting key presses globally

> You may need to restart your terminal after granting permissions.

#### Linux Mint

The global hotkey uses X11. Linux Mint Cinnamon normally offers this as **Cinnamon** or **Cinnamon on Xorg** on the login screen.

Wayland restricts global keyboard listeners. If the app reports that Wayland is active, log out, select a Cinnamon/Xorg session, and sign in again.

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
2. Press **Right Ctrl** on Windows/Linux or **Right Command** on macOS to start recording.
3. Press the same key again to stop recording.
4. Audio is sent to OpenAI for transcription.
5. The final text is pasted at your current cursor position.

## How it works

```
Right Ctrl (Windows/Linux) or Right Command (macOS) → Record audio
    ↓
Same key again → Send to OpenAI transcription
    ↓
Copy to clipboard → Simulate Ctrl+V or Cmd+V → Text appears at cursor
```
