#!/usr/bin/env bash
set -euo pipefail

python -m PyInstaller \
  --windowed \
  --name "Audio Transcriber" \
  --clean \
  main.py

echo "Built dist/Audio Transcriber.app"
