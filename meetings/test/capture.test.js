'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { runOrThrow, run, CAPTURE_FILTER, ffmpegArgs } = require('../script');

const CONFIG = { sampleRate: 16000, bitrateKbps: 24 };

const LOW = 300;
const HIGH = 3000;

async function meanVolume(file, channel, band) {
  const { stderr } = await run('ffmpeg', [
    '-v', 'info', '-i', file,
    '-af', `pan=mono|c0=${channel},${band},volumedetect`,
    '-f', 'null', '-',
  ]);
  const m = stderr.match(/mean_volume:\s*(-?[\d.]+) dB/);
  assert.ok(m, `no volumedetect output for ${channel}/${band}`);
  return Number.parseFloat(m[1]);
}

// Spelled out in full, deliberately — this is the list the transcription
// pipeline has always been fed.
test('ffmpegArgs records stereo Opus at the configured rate', () => {
  const args = ffmpegArgs({ mic: 'M', monitor: 'S', outPath: '/tmp/a.ogg', config: CONFIG });
  assert.deepStrictEqual(args, [
    '-hide_banner', '-nostdin', '-loglevel', 'warning',
    '-f', 'pulse', '-i', 'M',
    '-f', 'pulse', '-i', 'S',
    '-filter_complex', CAPTURE_FILTER, '-map', '[a]',
    '-ar', '16000', '-c:a', 'libopus', '-b:a', '24k', '-y', '/tmp/a.ogg',
  ]);
  // No `-ac 2`: it would re-downmix what join already laid out correctly.
  assert.ok(!args.includes('-ac'));
});

// The load-bearing test of the whole project:
// mic must land on the left channel and system audio on the right. Both PipeWire
// sources are 2-channel, which is exactly the case that used to silently blend
// them, so the fake inputs here are stereo too.
test('capture filter keeps input 0 on the left and input 1 on the right', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lmm-cap-'));
  const out = path.join(dir, 'sep.wav');

  await runOrThrow('ffmpeg', [
    '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', `sine=frequency=${LOW}:duration=2,aformat=channel_layouts=stereo`,
    '-f', 'lavfi', '-i', `sine=frequency=${HIGH}:duration=2,aformat=channel_layouts=stereo`,
    '-filter_complex', CAPTURE_FILTER, '-map', '[a]',
    '-ar', '16000', '-c:a', 'pcm_s16le', '-y', out,
  ]);

  const { stdout } = await runOrThrow('ffprobe', ['-v', 'error', '-show_entries', 'stream=channels', '-of', 'default=nw=1:nk=1', out]);
  assert.strictEqual(stdout.trim(), '2', 'capture must be stereo');

  const leftLow = await meanVolume(out, 'c0', 'lowpass=f=1000');
  const leftHigh = await meanVolume(out, 'c0', 'highpass=f=1500');
  const rightLow = await meanVolume(out, 'c1', 'lowpass=f=1000');
  const rightHigh = await meanVolume(out, 'c1', 'highpass=f=1500');

  // Each channel must carry its own tone and reject the other by a wide margin.
  assert.ok(leftLow - leftHigh > 15, `left channel leaked system audio (${leftLow} vs ${leftHigh})`);
  assert.ok(rightHigh - rightLow > 15, `right channel leaked mic audio (${rightHigh} vs ${rightLow})`);

  fs.rmSync(dir, { recursive: true, force: true });
});
