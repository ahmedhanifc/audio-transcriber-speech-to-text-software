'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { parseSources, pickMonitor } = require('../script');

const PACTL = [
  '1542\talsa_output.card.HiFi__HDMI3__sink.monitor\tPipeWire\ts24-32le 2ch 48000Hz\tSUSPENDED',
  '1545\talsa_output.card.HiFi__Speaker__sink.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED',
  '1547\talsa_input.card.HiFi__Mic1__source\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED',
  '',
].join('\n');

test('parseSources takes the name column and drops blank lines', () => {
  assert.deepStrictEqual(parseSources(PACTL), [
    'alsa_output.card.HiFi__HDMI3__sink.monitor',
    'alsa_output.card.HiFi__Speaker__sink.monitor',
    'alsa_input.card.HiFi__Mic1__source',
  ]);
});

test('pickMonitor prefers the default sink monitor', () => {
  const sources = parseSources(PACTL);
  assert.strictEqual(
    pickMonitor(sources, 'alsa_output.card.HiFi__Speaker__sink'),
    'alsa_output.card.HiFi__Speaker__sink.monitor',
  );
});

test('pickMonitor avoids HDMI when the default sink has no monitor', () => {
  const sources = parseSources(PACTL);
  assert.strictEqual(
    pickMonitor(sources, 'alsa_output.dock.sink'),
    'alsa_output.card.HiFi__Speaker__sink.monitor',
  );
});

test('pickMonitor returns null when nothing can capture system audio', () => {
  assert.strictEqual(pickMonitor(['alsa_input.card.Mic1__source'], 'any.sink'), null);
});
