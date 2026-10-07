'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { parseChannelRms, assessChannels } = require('../script');

const ASTATS = [
  '[Parsed_astats_0 @ 0x55] Channel: 1',
  '[Parsed_astats_0 @ 0x55] Peak level dB: -17.904454',
  '[Parsed_astats_0 @ 0x55] RMS level dB: -21.626851',
  '[Parsed_astats_0 @ 0x55] Channel: 2',
  '[Parsed_astats_0 @ 0x55] Peak level dB: -15.508081',
  '[Parsed_astats_0 @ 0x55] RMS level dB: -46.822079',
  '[Parsed_astats_0 @ 0x55] Overall',
  '[Parsed_astats_0 @ 0x55] RMS level dB: -24.624041',
].join('\n');

test('parseChannelRms reads per-channel RMS and ignores the Overall block', () => {
  assert.deepStrictEqual(parseChannelRms(ASTATS), [-21.626851, -46.822079]);
});

test('parseChannelRms handles -inf', () => {
  const out = parseChannelRms('Channel: 1\nRMS level dB: -inf');
  assert.deepStrictEqual(out, [-Infinity]);
});

// Both numbers below are measured, not invented: a dead channel re-encoded as
// Opus, and a real signal 40 dB down. An absolute floor would flag them backwards.
test('assessChannels flags a dead channel that Opus noise keeps above -47 dB', () => {
  const warnings = assessChannels([-21.6, -46.8]);
  assert.strictEqual(warnings.length, 1);
  assert.match(warnings[0], /system audio channel looks dead/);
});

test('assessChannels does not flag a faint but live stereo pair as dead', () => {
  const warnings = assessChannels([-61.4, -60.9]);
  assert.strictEqual(warnings.length, 1);
  assert.match(warnings[0], /very quiet/); // quiet, yes — dead, no
});

test('assessChannels stays silent on a healthy recording', () => {
  assert.deepStrictEqual(assessChannels([-24.46, -24.53]), []);
});

// Measured on a real capture where both channels carried signal: a 21 dB gap
// alone is not evidence of a dead channel.
test('assessChannels tolerates one channel being much louder than the other', () => {
  assert.deepStrictEqual(assessChannels([-19.5, -40.5]), []);
});

test('assessChannels flags a mono recording', () => {
  assert.match(assessChannels([-24])[0], /not stereo/);
});
