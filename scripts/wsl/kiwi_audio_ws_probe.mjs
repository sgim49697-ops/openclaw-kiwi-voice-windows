// kiwi_audio_ws_probe.mjs - send synthetic PCM to Kiwi WebAudioBridge without executing actions.
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ARTIFACT_DIR = resolve(ROOT, ".debugloop/artifacts/kiwi");
const RUNS_DIR = resolve(ROOT, ".debugloop/runs");
const DEFAULT_WS_URL = "ws://127.0.0.1:7789/api/audio";
const DEFAULT_STATUS_URL = "http://127.0.0.1:7789/api/status";
const DEFAULT_THRESHOLD = 0.015;

function usage() {
  return `Usage: node scripts/wsl/kiwi_audio_ws_probe.mjs [options]

Options:
  --ws-url <url>          Kiwi WebAudioBridge WebSocket URL (default: ${DEFAULT_WS_URL})
  --status-url <url>      Kiwi status endpoint (default: ${DEFAULT_STATUS_URL})
  --mode <mode>           silence, tone, or sequence (default: sequence)
  --sample-rate <hz>      PCM sample rate (default: 16000)
  --frame-ms <ms>         PCM frame duration (default: 20)
  --tone-hz <hz>          Tone frequency for tone/sequence mode (default: 440)
  --amplitude <0..1>      Tone amplitude (default: 0.08)
  --speech-seconds <sec>  Tone duration for tone/sequence mode (default: 1.2)
  --silence-seconds <sec> Silence duration (default: 1.2)
  --wait-after-ms <ms>    Wait after sending audio before final status (default: 2500)
  --out <path>            JSON artifact path (default: .debugloop/artifacts/kiwi/audio-ws-probe.json)
  --jsonl <path>          JSONL run log path (default: .debugloop/runs/latest.jsonl)
  -h, --help              Show this help
`;
}

function parseArgs(argv) {
  const args = {
    wsUrl: DEFAULT_WS_URL,
    statusUrl: DEFAULT_STATUS_URL,
    mode: "sequence",
    sampleRate: 16000,
    frameMs: 20,
    toneHz: 440,
    amplitude: 0.08,
    speechSeconds: 1.2,
    silenceSeconds: 1.2,
    waitAfterMs: 2500,
    out: resolve(ARTIFACT_DIR, "audio-ws-probe.json"),
    jsonl: resolve(RUNS_DIR, "latest.jsonl"),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "-h" || arg === "--help") {
      args.help = true;
      continue;
    }
    if (arg === "--ws-url") {
      args.wsUrl = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--status-url") {
      args.statusUrl = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--mode") {
      args.mode = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--sample-rate") {
      args.sampleRate = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--frame-ms") {
      args.frameMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--tone-hz") {
      args.toneHz = positiveNumber(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--amplitude") {
      args.amplitude = boundedNumber(requiredValue(argv, ++index, arg), arg, 0, 1);
      continue;
    }
    if (arg === "--speech-seconds") {
      args.speechSeconds = positiveNumber(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--silence-seconds") {
      args.silenceSeconds = positiveNumber(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--wait-after-ms") {
      args.waitAfterMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--out") {
      const value = requiredValue(argv, ++index, arg);
      args.out = isAbsolute(value) ? value : resolve(ROOT, value);
      continue;
    }
    if (arg === "--jsonl") {
      const value = requiredValue(argv, ++index, arg);
      args.jsonl = isAbsolute(value) ? value : resolve(ROOT, value);
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  if (!["silence", "tone", "sequence"].includes(args.mode)) {
    throw new Error("--mode must be silence, tone, or sequence");
  }
  return args;
}

function requiredValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function positiveInteger(raw, flag) {
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${flag} must be a positive integer`);
  }
  return value;
}

function positiveNumber(raw, flag) {
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${flag} must be a positive number`);
  }
  return value;
}

function boundedNumber(raw, flag, min, max) {
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${flag} must be between ${min} and ${max}`);
  }
  return value;
}

function nowIso() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function fetchStatus(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return {
      ok: response.ok,
      status: response.status,
      body: await response.json().catch(() => null),
    };
  } catch (error) {
    return {
      ok: false,
      error: error.message,
    };
  }
}

function makeFrame({ sampleRate, frameMs, toneHz, amplitude, frameIndex, kind }) {
  const sampleCount = Math.round((sampleRate * frameMs) / 1000);
  const buffer = Buffer.alloc(sampleCount * 2);
  let absSum = 0;
  let peak = 0;

  for (let i = 0; i < sampleCount; i += 1) {
    const sampleNumber = frameIndex * sampleCount + i;
    const normalized =
      kind === "tone" ? Math.sin((2 * Math.PI * toneHz * sampleNumber) / sampleRate) * amplitude : 0;
    const clamped = Math.max(-1, Math.min(1, normalized));
    const int16 = Math.round(clamped * 32767);
    buffer.writeInt16LE(int16, i * 2);
    const abs = Math.abs(clamped);
    absSum += abs;
    peak = Math.max(peak, abs);
  }

  return {
    buffer,
    rmsApprox: sampleCount ? absSum / sampleCount : 0,
    peak,
  };
}

function framePlan(args) {
  const silenceFrames = Math.ceil((args.silenceSeconds * 1000) / args.frameMs);
  const toneFrames = Math.ceil((args.speechSeconds * 1000) / args.frameMs);
  if (args.mode === "silence") {
    return [{ kind: "silence", frames: silenceFrames }];
  }
  if (args.mode === "tone") {
    return [{ kind: "tone", frames: toneFrames }];
  }
  return [
    { kind: "silence", frames: Math.ceil(silenceFrames / 2) },
    { kind: "tone", frames: toneFrames },
    { kind: "silence", frames: silenceFrames },
  ];
}

async function sendFrames(args) {
  if (typeof WebSocket === "undefined") {
    throw new Error("global WebSocket is unavailable; Node 22+ is required");
  }

  const events = [];
  const stats = {
    sentFrames: 0,
    sentBytes: 0,
    toneFrames: 0,
    silenceFrames: 0,
    maxFrameMeanAbs: 0,
    maxFramePeak: 0,
  };

  const socket = new WebSocket(args.wsUrl);
  socket.binaryType = "arraybuffer";

  await new Promise((resolvePromise, reject) => {
    const timer = setTimeout(() => reject(new Error("WebSocket open timed out")), 5000);
    socket.addEventListener("open", () => {
      clearTimeout(timer);
      resolvePromise();
    });
    socket.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error("WebSocket error before open"));
    });
  });

  socket.addEventListener("message", (event) => {
    const raw = typeof event.data === "string" ? event.data : Buffer.from(event.data).toString("utf8");
    try {
      events.push(JSON.parse(raw));
    } catch {
      events.push({ raw });
    }
  });

  socket.send(
    JSON.stringify({
      type: "hello",
      sample_rate: args.sampleRate,
      sampleRate: args.sampleRate,
      channels: 1,
      format: "pcm_s16le",
    }),
  );

  let frameIndex = 0;
  for (const segment of framePlan(args)) {
    for (let index = 0; index < segment.frames; index += 1) {
      const frame = makeFrame({ ...args, frameIndex, kind: segment.kind });
      socket.send(frame.buffer);
      stats.sentFrames += 1;
      stats.sentBytes += frame.buffer.byteLength;
      stats.maxFrameMeanAbs = Math.max(stats.maxFrameMeanAbs, frame.rmsApprox);
      stats.maxFramePeak = Math.max(stats.maxFramePeak, frame.peak);
      if (segment.kind === "tone") {
        stats.toneFrames += 1;
      } else {
        stats.silenceFrames += 1;
      }
      frameIndex += 1;
      await sleep(args.frameMs);
    }
  }

  await sleep(250);
  socket.close();
  return { events, stats };
}

function classify(args, before, sent, after) {
  const statusOk = Boolean(before.ok && after.ok);
  const expectedToneMean = args.mode === "silence" ? 0 : (2 / Math.PI) * args.amplitude;
  const aboveGate = expectedToneMean > DEFAULT_THRESHOLD;
  let status = "ok";
  const notes = [];

  if (!statusOk) {
    status = "blocked";
    notes.push("Kiwi status endpoint was not reachable before and after the probe.");
  }
  if (args.mode !== "silence" && !aboveGate) {
    status = "warning";
    notes.push("Synthetic tone amplitude is below the Kiwi speech gate; increase --amplitude.");
  }
  if (after.body?.web_audio_clients !== 0) {
    status = "warning";
    notes.push("web_audio_clients did not return to 0 after closing the probe socket.");
  }

  return {
    status,
    expectedToneMeanAbs: Number(expectedToneMean.toFixed(6)),
    kiwiSpeechGateMeanAbs: DEFAULT_THRESHOLD,
    expectedAboveGate: aboveGate,
    notes,
  };
}

async function appendJsonl(path, record) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(record)}\n`, { encoding: "utf8", flag: "a" });
}

async function writeJson(path, data) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return 0;
  }

  const before = await fetchStatus(args.statusUrl);
  const sent = await sendFrames(args);
  await sleep(args.waitAfterMs);
  const after = await fetchStatus(args.statusUrl);
  const verdict = classify(args, before, sent, after);
  const record = {
    timestamp: nowIso(),
    mode: "kiwi_audio_ws_probe",
    status: verdict.status,
    wsUrl: args.wsUrl,
    statusUrl: args.statusUrl,
    probe: {
      mode: args.mode,
      sampleRate: args.sampleRate,
      frameMs: args.frameMs,
      toneHz: args.toneHz,
      amplitude: args.amplitude,
      speechSeconds: args.speechSeconds,
      silenceSeconds: args.silenceSeconds,
      waitAfterMs: args.waitAfterMs,
    },
    verdict,
    before,
    sent,
    after,
  };

  await writeJson(args.out, record);
  await appendJsonl(args.jsonl, {
    timestamp: record.timestamp,
    mode: record.mode,
    status: record.status,
    artifact: args.out.replace(`${ROOT}/`, ""),
    verdict,
  });

  console.log(JSON.stringify(record, null, 2));
  return record.status === "blocked" ? 1 : 0;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(error.stack || error.message);
    process.exitCode = 1;
  });
