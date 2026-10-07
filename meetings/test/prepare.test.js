'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { parseSilences, speechSpans, planChunks, chunkFilter } = require('../script');

test('parseSilences reads silencedetect pairs', () => {
  const stderr = [
    '[silencedetect @ 0x55] silence_start: 12.345',
    '[silencedetect @ 0x55] silence_end: 15.2 | silence_duration: 2.855',
  ].join('\n');
  assert.deepStrictEqual(parseSilences(stderr, 60), [{ start: 12.345, end: 15.2 }]);
});

test('parseSilences closes a trailing silence at the end of the file', () => {
  const stderr = '[silencedetect @ 0x55] silence_start: 50.0';
  assert.deepStrictEqual(parseSilences(stderr, 60), [{ start: 50, end: 60 }]);
});

test('speechSpans keeps padding on both sides of a cut', () => {
  const spans = speechSpans(60, [{ start: 20, end: 30 }], 0.3);
  assert.deepStrictEqual(spans, [
    { start: 0, end: 20.3 },
    { start: 29.7, end: 60 },
  ]);
});

test('speechSpans returns the whole file when there is no silence', () => {
  assert.deepStrictEqual(speechSpans(60, [], 0.3), [{ start: 0, end: 60 }]);
});

test('planChunks splits on span boundaries once the limit is hit', () => {
  const spans = [
    { start: 0, end: 10 },
    { start: 20, end: 30 },
    { start: 40, end: 50 },
  ];
  const chunks = planChunks(spans, 15);
  assert.strictEqual(chunks.length, 3);
  assert.deepStrictEqual(chunks.map((c) => c.offset), [0, 20, 40]);
  assert.deepStrictEqual(chunks.map((c) => c.duration), [10, 10, 10]);
});

test('planChunks keeps spans together while they fit', () => {
  const chunks = planChunks([{ start: 0, end: 10 }, { start: 20, end: 30 }], 100);
  assert.strictEqual(chunks.length, 1);
  assert.strictEqual(chunks[0].duration, 20);
  assert.strictEqual(chunks[0].offset, 0);
});

test('chunkFilter concats multiple spans and skips concat for one', () => {
  assert.match(chunkFilter([{ start: 0, end: 1 }], 16000), /^\[0:a\]atrim.*\[s0\]aresample=16000\[out\]$/);
  assert.match(chunkFilter([{ start: 0, end: 1 }, { start: 2, end: 3 }], 16000), /concat=n=2:v=0:a=1/);
});
