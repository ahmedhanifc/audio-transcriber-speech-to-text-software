'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { render, clock } = require('../script');

test('clock formats hours, minutes and seconds', () => {
  assert.strictEqual(clock(0), '00:00:00');
  assert.strictEqual(clock(3671.9), '01:01:11');
});

test('render writes a header and the text, without time headings for one chunk', () => {
  const md = render(
    { id: '2026-07-29T10-00-00', title: 'Standup', started_at: '2026-07-29T10:00:00Z', ended_at: null },
    [{ start: 0, end: 30, text: 'Morning everyone.' }],
  );
  assert.match(md, /^# Standup\n/);
  assert.match(md, /Morning everyone\./);
  assert.doesNotMatch(md, /^## /m);
});

test('render adds time headings when there are several chunks', () => {
  const md = render(
    { id: 's1', title: null, started_at: '2026-07-29T10:00:00Z' },
    [{ start: 0, end: 10, text: 'one' }, { start: 600, end: 610, text: 'two' }],
  );
  assert.match(md, /^# s1$/m);
  assert.match(md, /^## 00:10:00$/m);
});

test('render groups many short segments into one block per five minutes', () => {
  const segments = [
    { start: 0, end: 3, text: 'one' },
    { start: 3, end: 6, text: 'two' },
    { start: 301, end: 304, text: 'three' },
  ];
  const md = render({ id: 's1', started_at: 'x' }, segments);
  assert.match(md, /^## 00:00:00\n\none two$/m);
  assert.match(md, /^## 00:05:00\n\nthree$/m);
  assert.strictEqual(md.match(/^## /gm).length, 2);
});

test('render says so when nothing was transcribed', () => {
  const md = render({ id: 's1', started_at: 'x' }, [{ start: 0, end: 1, text: null }]);
  assert.match(md, /No speech transcribed/);
});
