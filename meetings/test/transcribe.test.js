'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { transcribeChunk } = require('../script');

test('the parakeet backend explains how to build the venv when it is missing', async () => {
  await assert.rejects(
    () => transcribeChunk({ path: '/x/000.ogg', duration_s: 10 }, { parakeetPython: 'nope/bin/python3' }),
    /No Python at .*nope\/bin\/python3[\s\S]*venv \.venv-asr/,
  );
});
