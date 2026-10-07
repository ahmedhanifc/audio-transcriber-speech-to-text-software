'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { openStore } = require('../script');

function temp() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lmm-'));
  return openStore(path.join(dir, 'test.db'));
}

test('a session walks the state machine and unfinished() tracks it', () => {
  const store = temp();
  store.sessions.create('s1', 'Standup');
  assert.strictEqual(store.sessions.get('s1').state, 'recording');
  assert.deepStrictEqual(store.sessions.unfinished().map((s) => s.id), ['s1']);

  store.sessions.setState('s1', 'recorded', { endedAt: '2026-07-29T10:00:00Z' });
  store.sessions.setState('s1', 'prepared');
  store.sessions.setState('s1', 'transcribed');

  assert.strictEqual(store.sessions.get('s1').ended_at, '2026-07-29T10:00:00Z');
  assert.deepStrictEqual(store.sessions.unfinished(), []);
  store.close();
});

test('setError records the failure and setState clears it', () => {
  const store = temp();
  store.sessions.create('s1');
  store.sessions.setError('s1', 'OpenAI 500');
  assert.strictEqual(store.sessions.get('s1').error, 'OpenAI 500');
  // State stays at the last good step, so retry resumes from there.
  assert.strictEqual(store.sessions.get('s1').state, 'recording');
  store.sessions.setState('s1', 'recorded');
  assert.strictEqual(store.sessions.get('s1').error, null);
  store.close();
});

test('unknown states are rejected', () => {
  const store = temp();
  store.sessions.create('s1');
  assert.throws(() => store.sessions.setState('s1', 'summarised'), /Unknown state/);
  store.close();
});

test('transcribe flag defaults on, can be set at create and flipped later', () => {
  const store = temp();
  store.sessions.create('s1');
  assert.strictEqual(store.sessions.get('s1').transcribe, 1);

  store.sessions.create('s2', 'Audio only', false);
  assert.strictEqual(store.sessions.get('s2').transcribe, 0);

  store.sessions.setTranscribe('s2', true);
  assert.strictEqual(store.sessions.get('s2').transcribe, 1);
  store.close();
});

test('opening a pre-transcribe-column DB adds the column, defaulting on', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lmm-'));
  const dbPath = path.join(dir, 'test.db');

  const { DatabaseSync } = require('node:sqlite');
  const old = new DatabaseSync(dbPath);
  old.exec(`CREATE TABLE sessions (
    id TEXT PRIMARY KEY, title TEXT, state TEXT NOT NULL,
    started_at TEXT NOT NULL, ended_at TEXT, error TEXT
  )`);
  old.prepare('INSERT INTO sessions (id, state, started_at) VALUES (?, ?, ?)')
    .run('legacy', 'recorded', '2026-07-29T10:00:00Z');
  old.close();

  const store = openStore(dbPath);
  assert.strictEqual(store.sessions.get('legacy').transcribe, 1);
  store.close();
});

test('re-preparing carries transcribed text over by content hash', () => {
  const store = temp();
  store.sessions.create('s1');
  store.chunks.replaceAll('s1', [
    { hash: 'aaa', path: '/x/000.ogg', offset: 0, duration: 10 },
    { hash: 'bbb', path: '/x/001.ogg', offset: 20, duration: 10 },
  ]);
  store.chunks.setText('s1', 0, 'hello');

  // Same audio prepared again: chunk 0 keeps its paid-for text, chunk 1 changed.
  const rows = store.chunks.replaceAll('s1', [
    { hash: 'aaa', path: '/x/000.ogg', offset: 0, duration: 10 },
    { hash: 'ccc', path: '/x/001.ogg', offset: 20, duration: 12 },
  ]);
  assert.strictEqual(rows[0].text, 'hello');
  assert.strictEqual(rows[1].text, null);
  assert.strictEqual(rows[1].duration_s, 12);
  store.close();
});

test('segments round-trip as JSON and carry over with the text', () => {
  const store = temp();
  store.sessions.create('s1');
  store.chunks.replaceAll('s1', [{ hash: 'aaa', path: '/x/000.ogg', offset: 0, duration: 10 }]);

  const segments = [{ start: 0, end: 4, text: 'hello' }, { start: 4, end: 9, text: 'there' }];
  store.chunks.setText('s1', 0, 'hello there', segments);
  assert.deepStrictEqual(store.chunks.forSession('s1')[0].segments, segments);

  // Same audio, new offset: chunk-relative times mean the carry-over stays right.
  const rows = store.chunks.replaceAll('s1', [{ hash: 'aaa', path: '/x/000.ogg', offset: 60, duration: 10 }]);
  assert.deepStrictEqual(rows[0].segments, segments);
  store.close();
});

test('a chunk with no segments reads back as null, not a parse error', () => {
  const store = temp();
  store.sessions.create('s1');
  store.chunks.replaceAll('s1', [{ hash: 'aaa', path: '/x/000.ogg', offset: 0, duration: 10 }]);
  store.chunks.setText('s1', 0, 'text only');
  assert.strictEqual(store.chunks.forSession('s1')[0].segments, null);
  store.close();
});

test('remove() deletes a session and its chunks', () => {
  const store = temp();
  store.sessions.create('s1');
  store.chunks.replaceAll('s1', [{ hash: 'h', path: '/a.ogg', offset: 0, duration: 1 }]);
  store.sessions.remove('s1');
  assert.strictEqual(store.sessions.get('s1'), null);
  assert.deepStrictEqual(store.chunks.forSession('s1'), []);
  store.close();
});
