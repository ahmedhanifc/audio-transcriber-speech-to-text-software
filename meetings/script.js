#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { DatabaseSync } = require('node:sqlite');

const ROOT = __dirname;
const DATA_DIR = path.join(ROOT, 'data');
const DB_PATH = path.join(DATA_DIR, 'meetings.db');
const CONFIG_PATH = path.join(ROOT, 'config.json');

const DEFAULTS = {
  // Parakeet TDT via onnx-asr, on the CPU, no GPU anywhere in the picture.
  // Parakeet TDT decodes far fewer steps than Whisper, so it runs about twice
  // as fast on this laptop for the same words. v2 is the English-only model:
  // int8 costs the multilingual v3 real accuracy, v2 it barely touches.
  // Use v3 for a meeting in another language.
  parakeetModel: 'nemo-parakeet-tdt-0.6b-v2',
  parakeetQuantization: 'int8',
  parakeetPython: '.venv-asr/bin/python3',
  parakeetVad: 'silero',
  parakeetBatchSize: 1, // measured: batching only wastes CPU on 4 cores
  parakeetMaxSegmentSeconds: 30, // the model reads 20-30s of audio at a time
  parakeetMinSilenceMs: 300,
  parakeetSpeechPadMs: 200,

  sampleRate: 16000,
  bitrateKbps: 24,
  maxChunkSeconds: 3300,
  silenceThreshold: '-35dB',
  minSilenceSeconds: 1.5,
  keepSilenceSeconds: 0.3, // padding kept on each side of a cut, so words aren't clipped
  deleteAudioAfterTranscription: false,
  mic: null, // null = resolve from PipeWire at start time
  monitor: null,
};

function loadConfig() {
  const file = fs.existsSync(CONFIG_PATH)
    ? JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    : {};
  return { ...DEFAULTS, ...file };
}

function sessionDir(id) {
  return path.join(DATA_DIR, 'sessions', id);
}

// onStderrLine, when given, sees each complete stderr line as it arrives. Long
// jobs report progress that way; stderr is still buffered for the error path.
function run(bin, args, { onStderrLine, ...opts } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'], ...opts });
    let stdout = '';
    let stderr = '';
    let partial = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => {
      stderr += d;
      if (!onStderrLine) return;
      const lines = (partial + d).split('\n');
      partial = lines.pop();
      for (const line of lines) onStderrLine(line);
    });
    child.on('error', reject);
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

async function runOrThrow(bin, args, opts) {
  const res = await run(bin, args, opts);
  if (res.code !== 0) {
    throw new Error(`${bin} exited ${res.code}\n${res.stderr.trim().split('\n').slice(-8).join('\n')}`);
  }
  return res;
}

const BLOCK_SECONDS = 300;

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const parts = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60];
  return parts.map((n) => String(n).padStart(2, '0')).join(':');
}

function render(session, segments) {
  const lines = [
    `# ${session.title || session.id}`,
    '',
    `- Session: \`${session.id}\``,
    `- Started: ${session.started_at}`,
  ];
  if (session.ended_at) lines.push(`- Ended: ${session.ended_at}`);
  lines.push('');

  const spoken = segments.filter((s) => s.text);
  if (spoken.length === 0) lines.push('_No speech transcribed._');

  // A local model returns hundreds of short segments, so group them into
  // readable blocks rather than giving every one its own heading.
  const blocks = [];
  for (const segment of spoken) {
    const at = Math.floor(segment.start / BLOCK_SECONDS) * BLOCK_SECONDS;
    if (blocks.at(-1)?.at !== at) blocks.push({ at, texts: [] });
    blocks.at(-1).texts.push(segment.text);
  }

  for (const block of blocks) {
    if (blocks.length > 1) lines.push(`## ${clock(block.at)}`, '');
    lines.push(block.texts.join(' '), '');
  }
  return `${lines.join('\n').trimEnd()}\n`;
}

// `pactl list short sources` → one source per line, name in column 2.
function parseSources(stdout) {
  return stdout
    .split('\n')
    .map((line) => line.split('\t')[1])
    .filter(Boolean);
}

// Prefer the monitor of the default sink: that is literally "what you hear".
// Fall back to any non-HDMI monitor, because HDMI sinks are usually idle.
function pickMonitor(sources, defaultSink) {
  const wanted = `${defaultSink}.monitor`;
  if (sources.includes(wanted)) return wanted;
  const monitors = sources.filter((s) => s.endsWith('.monitor'));
  return monitors.find((s) => !/HDMI/i.test(s)) || monitors[0] || null;
}

async function pactl(args) {
  const { stdout } = await runOrThrow('pactl', args);
  return stdout.trim();
}

// Resolved fresh on every start: monitor names encode the physical sink,
// so plugging in headphones or a dock changes them.
async function resolveDevices(overrides = {}) {
  if ((await run('pactl', ['info'])).code !== 0) {
    throw new Error('pactl is not available — is PipeWire/PulseAudio running?');
  }

  const sources = parseSources(await pactl(['list', 'short', 'sources']));
  const mic = overrides.mic || (await pactl(['get-default-source']));
  const monitor = overrides.monitor || pickMonitor(sources, await pactl(['get-default-sink']));

  if (!sources.includes(mic)) throw new Error(`Mic source not found: ${mic}`);
  if (!monitor) throw new Error('No monitor source found — cannot capture system audio.');
  if (!sources.includes(monitor)) throw new Error(`Monitor source not found: ${monitor}`);

  return { mic, monitor };
}

async function probeDuration(audioPath) {
  const { stdout } = await runOrThrow('ffprobe', [
    '-v', 'error',
    '-show_entries', 'format=duration',
    '-of', 'default=nw=1:nk=1',
    audioPath,
  ]);
  const seconds = Number.parseFloat(stdout.trim());
  if (!Number.isFinite(seconds)) throw new Error(`Could not read duration of ${audioPath}`);
  return seconds;
}

// astats prints a "Channel: N" block per channel, then an "Overall" block.
function parseChannelRms(stderr) {
  const levels = [];
  let channel = null;
  for (const line of stderr.split('\n')) {
    if (/Channel:\s*\d+/.test(line)) channel = Number.parseInt(line.match(/Channel:\s*(\d+)/)[1], 10);
    else if (/Overall/.test(line)) channel = null;
    else if (channel !== null) {
      const m = line.match(/RMS level dB:\s*(-?[\d.]+|-?inf)/);
      if (m) {
        levels[channel - 1] = m[1] === '-inf' ? -Infinity : Number.parseFloat(m[1]);
        channel = null;
      }
    }
  }
  return levels;
}

// A flat channel means we captured the wrong source. Silent recordings are
// the #1 failure of this kind of tool, so check every one and say so loudly.
async function channelLevels(audioPath) {
  const { stderr } = await run('ffmpeg', ['-v', 'info', '-i', audioPath, '-af', 'astats=metadata=1', '-f', 'null', '-']);
  return parseChannelRms(stderr);
}

const NAMES = ['mic', 'system audio'];
const DEAD_GAP_DB = 20; // one channel this far under the other is not just quiet
const DEAD_FLOOR_DB = -45; // ...and a live channel does not sit this low
const QUIET_DB = -50; // measured: a real but faint signal sits around -60 dB

// Both conditions are needed. A relative test alone false-positives when one side
// is simply much louder (measured: a live pair at -19.5 and -40.5 dB). An absolute
// test alone flags the wrong channel, because Opus re-encodes a digitally dead
// channel with ~-47 dB of coding noise while a real-but-faint channel sits at ~-61.
function assessChannels(levels) {
  const warnings = [];
  const finite = levels.filter((l) => Number.isFinite(l));
  if (levels.length < 2) {
    warnings.push('Recording is not stereo — channel separation was lost.');
    return warnings;
  }
  const loudest = finite.length ? Math.max(...finite) : -Infinity;
  levels.forEach((level, i) => {
    if (loudest - level >= DEAD_GAP_DB && level < DEAD_FLOOR_DB) {
      warnings.push(`The ${NAMES[i] ?? `channel ${i + 1}`} channel looks dead (${fmtDb(level)} vs ${fmtDb(loudest)}) — wrong source, or nothing was captured.`);
    }
  });
  if (loudest < QUIET_DB) warnings.push(`The whole recording is very quiet (${fmtDb(loudest)}) — check your volume levels.`);
  return warnings;
}

function fmtDb(db) {
  if (db === undefined) return 'n/a';
  return db === -Infinity ? 'silent' : `${db.toFixed(1)} dB`;
}

function parseSilences(stderr, duration) {
  const silences = [];
  let open = null;
  for (const line of stderr.split('\n')) {
    const s = line.match(/silence_start:\s*(-?[\d.]+)/);
    if (s) { open = Number.parseFloat(s[1]); continue; }
    const e = line.match(/silence_end:\s*([\d.]+)/);
    if (e && open !== null) {
      silences.push({ start: Math.max(0, open), end: Number.parseFloat(e[1]) });
      open = null;
    }
  }
  // A meeting that ends in silence never emits silence_end.
  if (open !== null) silences.push({ start: Math.max(0, open), end: duration });
  return silences;
}

// The complement of the silences, with `keep` seconds of padding left on each
// side of every cut so no word gets clipped at a seam.
function speechSpans(duration, silences, keep) {
  const spans = [];
  let cursor = 0;
  for (const s of silences) {
    const end = Math.min(duration, s.start + keep);
    if (end > cursor) spans.push({ start: cursor, end });
    cursor = Math.max(cursor, Math.min(duration, s.end - keep));
  }
  if (cursor < duration) spans.push({ start: cursor, end: duration });
  return spans.filter((s) => s.end - s.start > 0.05);
}

// A span with no interior silence can exceed the duration ceiling on its own
// (a noisy meeting detects no silence at all). Split any such span into
// equal pieces under the cap; these cuts can land mid-word, but there is no
// silence to cut on.
function splitOversizedSpans(spans, maxSeconds) {
  const out = [];
  for (const span of spans) {
    const length = span.end - span.start;
    if (length <= maxSeconds) { out.push(span); continue; }
    const pieces = Math.ceil(length / maxSeconds);
    const step = length / pieces;
    for (let i = 0; i < pieces; i++) {
      out.push({ start: span.start + i * step, end: i === pieces - 1 ? span.end : span.start + (i + 1) * step });
    }
  }
  return out;
}

// Group spans into chunks that stay under the ceiling. Cuts land on
// silence boundaries, never byte offsets.
function planChunks(spans, maxSeconds) {
  const chunks = [];
  let current = null;
  for (const span of spans) {
    const length = span.end - span.start;
    if (!current || current.duration + length > maxSeconds) {
      current = { offset: span.start, duration: 0, spans: [] };
      chunks.push(current);
    }
    current.spans.push(span);
    current.duration += length;
  }
  return chunks;
}

// atrim each kept span, concat them, downmix to mono for transcription.
function chunkFilter(spans, sampleRate) {
  const parts = spans.map((s, i) =>
    `[0:a]atrim=start=${s.start.toFixed(3)}:end=${s.end.toFixed(3)},asetpts=N/SR/TB[s${i}]`);
  if (spans.length === 1) return `${parts[0]};[s0]aresample=${sampleRate}[out]`;
  const labels = spans.map((_, i) => `[s${i}]`).join('');
  return `${parts.join(';')};${labels}concat=n=${spans.length}:v=0:a=1,aresample=${sampleRate}[out]`;
}

function hashFile(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

async function prepare(audioPath, config) {
  const total = await probeDuration(audioPath);

  const { stderr } = await run('ffmpeg', [
    '-hide_banner', '-nostdin', '-i', audioPath,
    '-af', `silencedetect=noise=${config.silenceThreshold}:d=${config.minSilenceSeconds}`,
    '-f', 'null', '-',
  ]);

  const silences = parseSilences(stderr, total);
  const spans = speechSpans(total, silences, config.keepSilenceSeconds);
  if (spans.length === 0) throw new Error('Recording contains no detectable speech.');

  const maxSeconds = config.maxChunkSeconds;
  const plan = planChunks(splitOversizedSpans(spans, maxSeconds), maxSeconds);

  const outDir = path.join(path.dirname(audioPath), 'chunks');
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });

  const chunks = [];
  for (const [i, chunk] of plan.entries()) {
    const file = path.join(outDir, `${String(i).padStart(3, '0')}.ogg`);
    await runOrThrow('ffmpeg', [
      '-hide_banner', '-nostdin', '-loglevel', 'error',
      '-i', audioPath,
      '-filter_complex', chunkFilter(chunk.spans, config.sampleRate), '-map', '[out]',
      '-ac', '1', '-ar', String(config.sampleRate),
      '-c:a', 'libopus', '-b:a', `${config.bitrateKbps}k`,
      '-y', file,
    ]);
    chunks.push({ path: file, offset: chunk.offset, duration: chunk.duration, hash: hashFile(file) });
  }

  return { chunks, originalDuration: total, keptDuration: chunks.reduce((a, c) => a + c.duration, 0) };
}

const PID_FILE = path.join(DATA_DIR, 'recorder.pid');

// Stereo, always: mic left, system audio right. This is the one
// irreversible decision — channel separation cannot be recovered from a mixdown.
//
// Both PipeWire sources are themselves 2-channel, so amerge would produce four
// channels and a later `-ac 2` would blend the mic into both sides, quietly
// destroying the separation. Fold each input to mono first, then join: that maps
// the mic input to the left channel and the system input to the right, explicitly.
const join = (micIn, sysIn, out) => `[${micIn}]aformat=channel_layouts=mono[mic];`
  + `[${sysIn}]aformat=channel_layouts=mono[sys];`
  + `[mic][sys]join=inputs=2:channel_layout=stereo${out}`;

const CAPTURE_FILTER = join('0:a', '1:a', '[a]');

function ffmpegArgs({ mic, monitor, outPath, config }) {
  return [
    '-hide_banner', '-nostdin', '-loglevel', 'warning',
    '-f', 'pulse', '-i', mic,
    '-f', 'pulse', '-i', monitor,
    '-filter_complex', CAPTURE_FILTER, '-map', '[a]',
    '-ar', String(config.sampleRate),
    '-c:a', 'libopus', '-b:a', `${config.bitrateKbps}k`,
    '-y', outPath,
  ];
}

function readPidFile() {
  if (!fs.existsSync(PID_FILE)) return null;
  try {
    return JSON.parse(fs.readFileSync(PID_FILE, 'utf8'));
  } catch {
    return null;
  }
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function recorderStatus() {
  const rec = readPidFile();
  if (!rec) return { recording: false };
  if (!alive(rec.pid)) return { recording: false, stale: rec };
  return { recording: true, ...rec };
}

function logTail(dir, lines = 5) {
  return fs.readFileSync(path.join(dir, 'recorder.log'), 'utf8').trim().split('\n').slice(-lines).join('\n');
}

// Spawns ffmpeg and returns the pid record, or null if it died on the way up.
// The pid file is written before the liveness wait so a `status` in that window
// still sees the recording, and removed again if the process turned out to be dead.
async function attempt({ dir, args, base }) {
  const log = fs.openSync(path.join(dir, 'recorder.log'), 'a');
  const child = spawn('ffmpeg', args, {
    detached: true, // survives the CLI process exiting
    stdio: ['ignore', log, log],
  });
  child.unref();

  const record = { pid: child.pid, ...base, startedAt: new Date().toISOString() };
  fs.writeFileSync(PID_FILE, JSON.stringify(record, null, 2));

  // ffmpeg exits within ~1s if a source name is bad; catch that before returning.
  await new Promise((r) => setTimeout(r, 1200));
  if (alive(child.pid)) return record;
  fs.rmSync(PID_FILE, { force: true });
  return null;
}

async function startRecording({ id, config }) {
  const current = recorderStatus();
  if (current.recording) throw new Error(`Already recording session ${current.sessionId}`);

  const dir = sessionDir(id);
  fs.mkdirSync(dir, { recursive: true });
  const outPath = path.join(dir, 'audio.ogg');
  const devices = await resolveDevices({ mic: config.mic, monitor: config.monitor });

  const record = await attempt({
    dir,
    args: ffmpegArgs({ ...devices, outPath, config }),
    base: { sessionId: id, audioPath: outPath, ...devices },
  });

  if (!record) throw new Error(`ffmpeg failed to start:\n${logTail(dir)}`);
  return record;
}

// SIGINT, never SIGKILL — ffmpeg must finalise the Ogg container.
async function stopRecording({ timeoutMs = 10000 } = {}) {
  const rec = readPidFile();
  if (!rec) throw new Error('Not recording.');
  fs.rmSync(PID_FILE, { force: true });

  if (alive(rec.pid)) {
    process.kill(rec.pid, 'SIGINT');
    const deadline = Date.now() + timeoutMs;
    while (alive(rec.pid) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 200));
    }
    if (alive(rec.pid)) throw new Error(`ffmpeg (pid ${rec.pid}) did not exit after SIGINT.`);
  }
  return rec;
}

// A pid file whose process is gone means the daemon or machine died mid-meeting.
// The Ogg is readable up to the last written page — keep it.
function clearStale() {
  const s = recorderStatus();
  if (s.stale) fs.rmSync(PID_FILE, { force: true });
  return s.stale || null;
}

// A session's state is the last step that succeeded; `error` being non-null
// means the *next* step failed. That makes "failed" and "resumable" the same thing.
const STATES = ['recording', 'recorded', 'prepared', 'transcribed'];
const TERMINAL = 'transcribed';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT,
  state       TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  error       TEXT,
  transcribe  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS chunks (
  session_id  TEXT NOT NULL,
  idx         INTEGER NOT NULL,
  hash        TEXT NOT NULL,
  path        TEXT NOT NULL,
  offset_s    REAL NOT NULL,
  duration_s  REAL NOT NULL,
  text        TEXT,
  segments    TEXT,
  PRIMARY KEY (session_id, idx)
);
`;

const encode = (segments) => (segments == null ? null : JSON.stringify(segments));

function openStore(dbPath = DB_PATH) {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec(SCHEMA);
  for (const migration of [
    'ALTER TABLE sessions ADD COLUMN transcribe INTEGER NOT NULL DEFAULT 1',
    'ALTER TABLE chunks ADD COLUMN segments TEXT',
  ]) {
    try {
      db.exec(migration);
    } catch {
      // Column already exists (fresh DBs get it from SCHEMA).
    }
  }

  const sessions = {
    create(id, title, transcribe = true) {
      db.prepare('INSERT INTO sessions (id, title, state, started_at, transcribe) VALUES (?, ?, ?, ?, ?)')
        .run(id, title ?? null, 'recording', new Date().toISOString(), transcribe ? 1 : 0);
      return sessions.get(id);
    },
    get(id) {
      return db.prepare('SELECT * FROM sessions WHERE id = ?').get(id) ?? null;
    },
    all() {
      return db.prepare('SELECT * FROM sessions ORDER BY started_at DESC').all();
    },
    setState(id, state, { endedAt } = {}) {
      if (!STATES.includes(state)) throw new Error(`Unknown state: ${state}`);
      db.prepare('UPDATE sessions SET state = ?, error = NULL, ended_at = COALESCE(?, ended_at) WHERE id = ?')
        .run(state, endedAt ?? null, id);
    },
    setError(id, message) {
      db.prepare('UPDATE sessions SET error = ? WHERE id = ?').run(message, id);
    },
    remove(id) {
      db.prepare('DELETE FROM chunks WHERE session_id = ?').run(id);
      db.prepare('DELETE FROM sessions WHERE id = ?').run(id);
    },
    setTitle(id, title) {
      db.prepare('UPDATE sessions SET title = ? WHERE id = ?').run(title, id);
    },
    setTranscribe(id, on) {
      db.prepare('UPDATE sessions SET transcribe = ? WHERE id = ?').run(on ? 1 : 0, id);
    },
    // Anything not finished yet — what recover() picks up at startup.
    unfinished() {
      return db.prepare('SELECT * FROM sessions WHERE state != ? ORDER BY started_at').all(TERMINAL);
    },
  };

  const chunks = {
    // Re-preparing must not lose transcribed text: carry it over by content hash.
    replaceAll(sessionId, rows) {
      const previous = new Map(chunks.forSession(sessionId).map((c) => [c.hash, c]));
      db.prepare('DELETE FROM chunks WHERE session_id = ?').run(sessionId);
      const insert = db.prepare(
        'INSERT INTO chunks (session_id, idx, hash, path, offset_s, duration_s, text, segments) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      );
      rows.forEach((r, i) => {
        const prior = previous.get(r.hash);
        insert.run(sessionId, i, r.hash, r.path, r.offset, r.duration, prior?.text ?? null, encode(prior?.segments));
      });
      return chunks.forSession(sessionId);
    },
    forSession(sessionId) {
      return db.prepare('SELECT * FROM chunks WHERE session_id = ? ORDER BY idx').all(sessionId)
        .map((c) => ({ ...c, segments: c.segments == null ? null : JSON.parse(c.segments) }));
    },
    // Segment times are relative to the chunk, so the hash carry-over above stays
    // correct even when re-preparing moves the chunk's offset.
    setText(sessionId, idx, text, segments) {
      db.prepare('UPDATE chunks SET text = ?, segments = ? WHERE session_id = ? AND idx = ?')
        .run(text, encode(segments), sessionId, idx);
    },
  };

  return { db, sessions, chunks, close: () => db.close() };
}

const WORKER = path.join(__dirname, 'parakeet.py');
const SETUP_HINT = 'python3 -m venv .venv-asr && .venv-asr/bin/pip install "onnx-asr[cpu,hub]"';

// One line per minute of audio, not per segment: there are hundreds of those.
const PROGRESS_EVERY_SECONDS = 60;

function interpreter(relPath, setupHint) {
  const bin = path.resolve(ROOT, relPath);
  if (!fs.existsSync(bin)) throw new Error(`No Python at ${bin}. Create it with:\n  ${setupHint}`);
  return bin;
}

async function toWav(chunkPath, dir) {
  const wav = path.join(dir, 'chunk.wav');
  await runOrThrow('ffmpeg', [
    '-hide_banner', '-nostdin', '-loglevel', 'error',
    '-i', chunkPath, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-y', wav,
  ]);
  return wav;
}

// Timestamps are relative to the chunk; the runner adds the chunk's offset.
async function transcribeChunk(chunk, config, log = () => {}) {
  const bin = interpreter(config.parakeetPython, SETUP_HINT);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lmm-'));

  try {
    const opts = {
      path: await toWav(chunk.path, dir),
      model: config.parakeetModel,
      quantization: config.parakeetQuantization,
      vad: config.parakeetVad,
      batchSize: config.parakeetBatchSize,
      maxSegmentSeconds: config.parakeetMaxSegmentSeconds,
      minSilenceMs: config.parakeetMinSilenceMs,
      speechPadMs: config.parakeetSpeechPadMs,
    };

    let announced = 0;
    const { stdout } = await runOrThrow(bin, [WORKER, JSON.stringify(opts)], {
      onStderrLine: (line) => {
        if (!line.startsWith('progress ')) return; // model download bars, warnings
        const done = Number(line.slice('progress '.length));
        if (!(done - announced >= PROGRESS_EVERY_SECONDS)) return;
        announced = done;
        log(`  ${clock(done)} / ${clock(chunk.duration_s)}`);
      },
    });

    try {
      return JSON.parse(stdout);
    } catch {
      throw new Error(`onnx-asr produced no JSON for ${chunk.path}`);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// Concurrency is 1. Serial jobs make failures legible, and this is
// single-user software.
let queue = Promise.resolve();

function enqueue(task) {
  const result = queue.then(task, task);
  queue = result.catch(() => {});
  return result;
}

async function stepPrepare(store, session, config, log) {
  const audioPath = path.join(sessionDir(session.id), 'audio.ogg');
  if (!fs.existsSync(audioPath)) throw new Error(`Missing recording: ${audioPath}`);

  // Silent recordings are the #1 failure of this kind of tool.
  const levels = await channelLevels(audioPath);
  log(`levels: mic ${fmtDb(levels[0])} · system ${fmtDb(levels[1])}`);
  for (const warning of assessChannels(levels)) log(`WARNING: ${warning}`);

  const { chunks, originalDuration, keptDuration } = await prepare(audioPath, config);
  store.chunks.replaceAll(session.id, chunks);
  log(`prepared ${chunks.length} chunk(s): ${clock(keptDuration)} of speech from ${clock(originalDuration)}`);
  store.sessions.setState(session.id, 'prepared');
}

async function stepTranscribe(store, session, config, log) {
  const rows = store.chunks.forSession(session.id);
  if (rows.length === 0) throw new Error('No chunks — run prepare first.');

  for (const row of rows) {
    if (row.text !== null) { // already done: the hash cache carried it over
      log(`chunk ${row.idx}: cached`);
      continue;
    }
    const segments = await transcribeChunk(row, config, log);
    store.chunks.setText(session.id, row.idx, segments.map((s) => s.text).join(' '), segments);
    log(`chunk ${row.idx}: ${clock(row.duration_s)} transcribed, ${segments.length} segment(s)`);
  }

  const dir = sessionDir(session.id);
  const segments = store.chunks.forSession(session.id).flatMap((c) => (c.segments
    // Chunks transcribed before segments were persisted have text but no times.
    ? c.segments.map((s) => ({ start: c.offset_s + s.start, end: c.offset_s + s.end, text: s.text }))
    : [{ start: c.offset_s, end: c.offset_s + c.duration_s, text: c.text }]));
  fs.writeFileSync(path.join(dir, 'transcript.json'), `${JSON.stringify(segments, null, 2)}\n`);
  const mdPath = path.join(dir, 'transcript.md');
  if (fs.existsSync(mdPath)) fs.copyFileSync(mdPath, path.join(dir, 'transcript.bak.md')); // keep panel edits
  // Re-read: the title may have been renamed while this chunk loop ran.
  fs.writeFileSync(mdPath, render(store.sessions.get(session.id), segments));
  store.sessions.setState(session.id, 'transcribed');
  log(`wrote ${path.join(dir, 'transcript.md')}`);

  if (config.deleteAudioAfterTranscription) {
    fs.rmSync(path.join(dir, 'audio.ogg'), { force: true });
    fs.rmSync(path.join(dir, 'chunks'), { recursive: true, force: true });
    log('deleted audio (deleteAudioAfterTranscription is on)');
  }
}

const STEPS = { recorded: stepPrepare, prepared: stepTranscribe };

// Advance a session as far as it will go. Errors are recorded, not thrown away:
// the state stays at the last completed step, so `retry` resumes from there.
function processSession(store, sessionId, config, log = () => {}) {
  return enqueue(async () => {
    for (;;) {
      const session = store.sessions.get(sessionId);
      if (!session) throw new Error(`Unknown session: ${sessionId}`);

      if (session.state === 'recording') {
        if (recorderStatus().recording) return session;
        clearStale();
        log('recording was interrupted — keeping the partial audio');
        store.sessions.setState(sessionId, 'recorded', { endedAt: new Date().toISOString() });
        continue;
      }

      // Audio-only session: keep the recording, never prepare or transcribe.
      // The `transcribe` command flips the flag before reprocessing.
      if (!session.transcribe) return session;

      const step = STEPS[session.state];
      if (!step) return session;

      try {
        await step(store, session, config, log);
      } catch (err) {
        store.sessions.setError(sessionId, err.message);
        throw err;
      }
    }
  });
}

// Runs at startup: anything unfinished gets picked up where it stopped.
async function recoverSessions(store, config, log = () => {}) {
  const pending = store.sessions.unfinished().filter((s) => !(s.state === 'recording' && recorderStatus().recording));
  for (const session of pending) {
    log(`recovering ${session.id} (${session.state})`);
    try {
      await processSession(store, session.id, config, log);
    } catch (err) {
      log(`${session.id} failed: ${err.message}`);
    }
  }
  return pending.map((s) => s.id);
}

const log = (msg) => console.log(msg);

// Local time, not UTC: these become directory names you have to recognise.
function newSessionId(now = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
    + `_${p(now.getHours())}-${p(now.getMinutes())}-${p(now.getSeconds())}`;
}

// Flags are stripped from the title, or they end up in your meeting name.
const START_FLAGS = ['--no-transcribe'];

async function cmdStart(store, config, args) {
  const id = newSessionId();
  const transcribe = !args.includes('--no-transcribe');
  const title = args.filter((a) => !START_FLAGS.includes(a)).join(' ') || null;
  store.sessions.create(id, title, transcribe);
  try {
    const rec = await startRecording({ id, config });
    log(`recording ${id}`);
    log(`  mic     ${rec.mic}`);
    log(`  system  ${rec.monitor}`);
    log(`  file    ${rec.audioPath}`);
    if (!transcribe) log('  transcription: off (audio only)');
    log('\nStop with:  npm start -- stop');
  } catch (err) {
    store.sessions.setError(id, err.message);
    throw err;
  }
}

async function cmdStop(store, config) {
  const rec = await stopRecording();
  store.sessions.setState(rec.sessionId, 'recorded', { endedAt: new Date().toISOString() });
  log(`stopped ${rec.sessionId}`);
  await processSession(store, rec.sessionId, config, (m) => log(`  ${m}`));
  if (!store.sessions.get(rec.sessionId).transcribe) {
    log(`  audio saved: ${sessionDir(rec.sessionId)}/audio.ogg`);
    log(`  transcribe later with:  npm start -- transcribe ${rec.sessionId}`);
  }
}

function cmdList(store) {
  const rows = store.sessions.all();
  if (rows.length === 0) return log('No sessions yet.');
  for (const s of rows) {
    const flag = s.error ? ' !' : '  ';
    const mode = s.transcribe ? '' : ' [audio-only]';
    log(`${flag}${s.id}  ${s.state.padEnd(12)} ${s.title || ''}${mode}`);
    if (s.error) log(`    error: ${s.error}`);
  }
}

function cmdStatus() {
  const s = recorderStatus();
  if (s.recording) {
    const elapsed = (Date.now() - new Date(s.startedAt)) / 1000;
    return log(`recording ${s.sessionId} — ${clock(elapsed)} elapsed`);
  }
  if (s.stale) return log(`not recording (stale pid for ${s.stale.sessionId}; run "recover")`);
  log('not recording');
}

function cmdShow(store, args) {
  const id = args[0] || store.sessions.all()[0]?.id;
  const session = id && store.sessions.get(id);
  if (!session) return log('No such session.');
  log(`${session.id}  state=${session.state}${session.error ? `  error=${session.error}` : ''}`);
  for (const c of store.chunks.forSession(session.id)) {
    log(`  chunk ${c.idx} @${clock(c.offset_s)} ${clock(c.duration_s)} ${c.text === null ? 'pending' : `${c.text.length} chars`}`);
  }
  log(`  dir ${sessionDir(session.id)}`);
}

function cmdDelete(store, args) {
  const id = args[0];
  if (!id || !store.sessions.get(id)) return log('No such session.');
  if (recorderStatus().sessionId === id) throw new Error('Stop the recording first.');
  store.sessions.remove(id);
  fs.rmSync(sessionDir(id), { recursive: true, force: true });
  log(`deleted ${id}`);
}

// The title also heads transcript.md. Swap only that line so edits made in the panel survive.
function cmdRename(store, args) {
  const [id, ...words] = args;
  if (!id || !store.sessions.get(id)) return log('No such session.');
  const title = words.join(' ').trim() || null;
  store.sessions.setTitle(id, title);
  const mdPath = path.join(sessionDir(id), 'transcript.md');
  if (fs.existsSync(mdPath)) {
    const [, ...rest] = fs.readFileSync(mdPath, 'utf8').split('\n');
    fs.writeFileSync(mdPath, [`# ${title || id}`, ...rest].join('\n'));
  }
  log(`renamed ${id}`);
}

const USAGE = `local-meeting-minutes

  start [title]     begin recording this machine's mic + system audio
    --no-transcribe   audio only: keep the recording, transcribe when you say so
  stop              stop recording, then prepare and transcribe
  transcribe [id]   transcribe an audio-only session (default: most recent)
  list              list sessions and their state
  status            is a recording running?
  show [id]         chunk detail for a session (default: most recent)
  retry [id]        resume a failed session from its last good step
  recover           pick up anything left unfinished by a crash
  rename <id> [title]  rename a session (no title clears it)
  delete <id>       delete a session and its audio
`;

async function main() {
  const config = loadConfig();
  const [command, ...args] = process.argv.slice(2);
  const store = openStore();

  try {
    switch (command) {
      case 'start': return await cmdStart(store, config, args);
      case 'stop': return await cmdStop(store, config);
      case 'list': return cmdList(store);
      case 'status': return cmdStatus();
      case 'show': return cmdShow(store, args);
      case 'rename': return cmdRename(store, args);
      case 'delete': return cmdDelete(store, args);
      case 'retry': {
        const id = args[0] || store.sessions.all()[0]?.id;
        if (!id) return log('No sessions to retry.');
        await processSession(store, id, config, (m) => log(`  ${m}`));
        return;
      }
      case 'transcribe': {
        const id = args[0] || store.sessions.all()[0]?.id;
        if (!id || !store.sessions.get(id)) return log('No such session.');
        store.sessions.setTranscribe(id, true);
        await processSession(store, id, config, (m) => log(`  ${m}`));
        return;
      }
      case 'recover': {
        const ids = await recoverSessions(store, config, (m) => log(`  ${m}`));
        return log(ids.length ? `recovered ${ids.length} session(s)` : 'nothing to recover');
      }
      default:
        return log(USAGE);
    }
  } finally {
    store.close();
  }
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`error: ${err.message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  run,
  runOrThrow,
  CAPTURE_FILTER,
  ffmpegArgs,
  parseSources,
  pickMonitor,
  parseChannelRms,
  assessChannels,
  parseSilences,
  speechSpans,
  planChunks,
  chunkFilter,
  openStore,
  transcribeChunk,
  render,
  clock,
};
