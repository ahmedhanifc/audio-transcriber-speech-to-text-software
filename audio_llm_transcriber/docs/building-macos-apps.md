# Building a Personal macOS App from Python

This guide explains how to package a Python desktop app into a local macOS `.app` bundle with PyInstaller. It is intended for personal apps on your own Mac, not public distribution.

## Basic Flow

1. Install your app dependencies.

```bash
pip install -r requirements.txt
```

2. Add PyInstaller if it is not already installed.

```bash
pip install pyinstaller
```

3. Build the app bundle.

```bash
python -m PyInstaller \
  --windowed \
  --name "My App" \
  --clean \
  main.py
```

The output should be:

```text
dist/My App.app
```

4. Launch the built app.

```bash
open "dist/My App.app"
```

5. If it works, copy it to Applications.

```bash
cp -R "dist/My App.app" /Applications/
```

After that, you can launch it from Spotlight, Launchpad, or `/Applications`.

## Build Script Template

For repeatable builds, create `build_macos_app.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m PyInstaller \
  --windowed \
  --name "My App" \
  --clean \
  main.py

echo "Built dist/My App.app"
```

Run it with:

```bash
bash build_macos_app.sh
```

## Environment Variables

Apps launched from Spotlight, Launchpad, or Finder do not reliably inherit the environment variables from your terminal shell.

For personal apps, use an app-specific config file outside the app bundle:

```text
~/.my-app/.env
```

Example:

```bash
mkdir -p ~/.my-app
printf 'MY_SECRET=value_here\n' > ~/.my-app/.env
```

In Python, load both the development `.env` and the user config file:

```python
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path.home() / ".my-app" / ".env", override=True)
```

Do not bundle secrets inside the `.app`.

## Logging

Windowed macOS apps do not show a terminal, so crashes and print output can be hard to see.

For personal apps, write logs to:

```text
~/Library/Logs/My App.log
```

Minimal Python setup:

```python
from pathlib import Path
import logging

log_path = Path.home() / "Library" / "Logs" / "My App.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
```

Use `logging.info(...)` for normal events and `logging.exception(...)` inside exception handlers.

## macOS Permissions

The packaged `.app` gets its own macOS permissions. Permissions granted to Terminal, iTerm, or VS Code do not automatically apply to the app bundle.

Depending on the app, you may need to grant permissions in **System Settings > Privacy & Security**, such as:

- Microphone
- Camera
- Accessibility
- Input Monitoring
- Files and Folders
- Screen Recording

Launch the packaged app once before checking permissions, because macOS often only lists the app after it has requested access.

## Troubleshooting

If PyInstaller is missing:

```bash
pip install pyinstaller
```

If macOS blocks the app because it is unsigned, right-click the app and choose **Open** once. For personal use, this is usually enough.

If the app launches but cannot find configuration, confirm the config file exists:

```bash
ls -la ~/.my-app/.env
```

If the app silently exits, check the log file:

```bash
tail -n 100 ~/Library/Logs/My\ App.log
```

If permissions fail, remove and re-add the app in **System Settings > Privacy & Security**, then restart the app.

If a dependency is missing at runtime, rebuild after installing dependencies:

```bash
pip install -r requirements.txt
bash build_macos_app.sh
```

For apps with dynamic imports, PyInstaller may need `--hidden-import package_name`. Add hidden imports only after seeing a specific missing-module error.
