import json
import sys

import onnx_asr


def main():
    opts = json.loads(sys.argv[1])

    model = onnx_asr.load_model(opts["model"], quantization=opts["quantization"] or None)
    vad = onnx_asr.load_vad(opts["vad"])

    # Parakeet reads 20-30s at a time, so VAD does the cutting. Segments are
    # only hard-cut at maxSegmentSeconds when someone talks that long without
    # a pause; everything else is split on real silence.
    recognizer = model.with_vad(
        vad,
        batch_size=opts["batchSize"],
        max_speech_duration_s=opts["maxSegmentSeconds"],
        min_silence_duration_ms=opts["minSilenceMs"],
        speech_pad_ms=opts["speechPadMs"],
    )

    segments = []
    for res in recognizer.recognize(opts["path"]):
        text = res.text.strip()
        if text:
            segments.append({"start": res.start, "end": res.end, "text": text})
        # Batches arrive one at a time, so stderr can report how far in we are.
        # Without this a 30 minute chunk is minutes of nothing on screen.
        print(f"progress {res.end:.0f}", file=sys.stderr, flush=True)

    json.dump(segments, sys.stdout)


main()
