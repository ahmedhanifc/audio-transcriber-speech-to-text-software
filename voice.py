import os
import sys

import sounddevice as sd
from openai import OpenAI

if sys.platform.startswith("win"):
    MIC_GUESSES = ['airpods', 'headset', 'realtek', 'conexant', 'id', 'high definition audio', 'microphone']
elif sys.platform.startswith("darwin"):
    MIC_GUESSES = ['airpods', 'headset', 'macbook', 'built-in', 'internal']
else:
    MIC_GUESSES = ['bluez', 'airpods', 'headset', 'built-in', 'internal', 'pulse']


def find_mic():
    """Index of the MIC from .env, else the first headset/built-in guess. None means system default."""
    wanted = os.getenv("MIC")
    devices = sd.query_devices()
    for keyword in [wanted] if wanted else MIC_GUESSES:
        for idx, device in enumerate(devices):
            if keyword.lower() in device['name'].lower() and device['max_input_channels'] > 0:
                return idx
    return None


def transcribe(path, model=None):
    model = model or os.getenv("STT_MODEL") or "gpt-4o-transcribe"
    keywords = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]

    if model.startswith("gemini"):
        from google import genai
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        audio = client.files.upload(file=path)
        config = {"language_codes": ["en-US"]}
        if keywords:
            config["custom_vocabulary"] = keywords
        try:
            result = client.interactions.create(
                model=model,
                input=[{"type": "audio", "uri": audio.uri, "mime_type": audio.mime_type}],
                generation_config={"transcription_config": config},
            )
        finally:
            client.files.delete(name=audio.name)
        return result.output_text.strip()

    # gpt-transcribe takes `languages`/`keywords`; the older models take `language`/`prompt`.
    # Sent via extra_body so an older openai package still works.
    if model == "gpt-transcribe":
        args = {"extra_body": {"languages": ["en"], **({"keywords": keywords} if keywords else {})}}
    else:
        args = {"language": "en", **({"prompt": "Vocabulary: " + ", ".join(keywords)} if keywords else {})}
    with open(path, "rb") as f:
        return OpenAI().audio.transcriptions.create(model=model, file=f, **args).text.strip()
