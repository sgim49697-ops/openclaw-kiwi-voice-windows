// kiwi_browser_stt_capture_probe.mjs - capture dashboard browser microphone WAV samples through CDP.
// debug-autoloop: command=node scripts/wsl/kiwi_browser_stt_capture_probe.mjs --status
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { Buffer } from "node:buffer";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ARTIFACT_DIR = resolve(ROOT, ".debugloop/artifacts/kiwi");
const DEFAULT_CDP_URL = process.env.KIWI_CDP_URL || "http://127.0.0.1:9222";
const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:7789";
const DEFAULT_PHRASE = "오픈클로, 테스트 알림 보내줘";
const KIWI_SPEECH_GATE = 0.015;

function usage() {
  return `Usage: node scripts/wsl/kiwi_browser_stt_capture_probe.mjs [options]

Options:
  --cdp-url <url>        Chrome CDP HTTP endpoint (default: ${DEFAULT_CDP_URL})
  --dashboard-url <url>  Kiwi dashboard URL prefix (default: ${DEFAULT_DASHBOARD_URL})
  --count <n>            Number of WAV samples to capture (default: 3)
  --duration-ms <ms>     Recording duration per sample (default: 6000)
  --gap-ms <ms>          Delay between samples (default: 2500)
  --timeout-ms <ms>      CDP command timeout (default: 15000)
  --raw-audio            Disable echo cancellation/noise suppression/auto gain
  --device-id <id>       Optional getUserMedia deviceId, e.g. default or communications
  --min-rms <value>      Minimum RMS gate for pass verdict (default: ${KIWI_SPEECH_GATE})
  --phrase <text>        Expected spoken phrase for artifact metadata
  --out-dir <path>       WAV output directory (default: .debugloop/artifacts/kiwi/stt-samples-v7.2.11-browser)
  --status               Print existing capture manifest status without recording
  -h, --help             Show this help
`;
}

function parseArgs(argv) {
  const args = {
    cdpUrl: DEFAULT_CDP_URL,
    dashboardUrl: DEFAULT_DASHBOARD_URL,
    count: 3,
    durationMs: 6000,
    gapMs: 2500,
    timeoutMs: 15000,
    rawAudio: false,
    deviceId: null,
    minRms: KIWI_SPEECH_GATE,
    phrase: DEFAULT_PHRASE,
    outDir: resolve(ARTIFACT_DIR, "stt-samples-v7.2.11-browser"),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "-h" || arg === "--help") {
      args.help = true;
      continue;
    }
    if (arg === "--cdp-url") {
      args.cdpUrl = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--dashboard-url") {
      args.dashboardUrl = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--count") {
      args.count = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--duration-ms") {
      args.durationMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--gap-ms") {
      args.gapMs = nonNegativeInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--timeout-ms") {
      args.timeoutMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--raw-audio") {
      args.rawAudio = true;
      continue;
    }
    if (arg === "--status") {
      args.status = true;
      continue;
    }
    if (arg === "--device-id") {
      args.deviceId = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--min-rms") {
      args.minRms = positiveNumber(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--phrase") {
      args.phrase = requiredValue(argv, ++index, arg);
      continue;
    }
    if (arg === "--out-dir") {
      const value = requiredValue(argv, ++index, arg);
      args.outDir = isAbsolute(value) ? value : resolve(ROOT, value);
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
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

function nonNegativeInteger(raw, flag) {
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${flag} must be a non-negative integer`);
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

function endpoint(cdpUrl, path) {
  return new URL(path, cdpUrl.endsWith("/") ? cdpUrl : `${cdpUrl}/`).toString();
}

function nowIso() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function fetchJson(url, timeoutMs) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${response.statusText} for ${url}`);
  }
  return await response.json();
}

class CdpConnection {
  constructor(wsUrl, timeoutMs) {
    this.wsUrl = wsUrl;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
    this.socket = null;
  }

  async connect() {
    if (typeof WebSocket === "undefined") {
      throw new Error("global WebSocket is unavailable; Node 22+ is required");
    }
    await new Promise((resolvePromise, reject) => {
      const socket = new WebSocket(this.wsUrl);
      this.socket = socket;
      const timer = setTimeout(() => reject(new Error(`WebSocket open timed out after ${this.timeoutMs}ms`)), this.timeoutMs);
      socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolvePromise();
      });
      socket.addEventListener("error", () => {
        clearTimeout(timer);
        reject(new Error("websocket error before open"));
      });
      socket.addEventListener("message", (event) => this.handleMessage(event));
      socket.addEventListener("close", () => this.rejectPending("websocket closed before response"));
      socket.addEventListener("error", () => this.rejectPending("websocket error"));
    });
  }

  handleMessage(event) {
    const raw = typeof event.data === "string" ? event.data : event.data.toString();
    const message = JSON.parse(raw);
    if (!message.id) {
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) {
      return;
    }
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.error) {
      pending.reject(new Error(`${pending.method} failed: ${JSON.stringify(message.error)}`));
      return;
    }
    pending.resolve(message.result || {});
  }

  rejectPending(reason) {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`${pending.method} ${reason}`));
      this.pending.delete(id);
    }
  }

  send(method, params = {}) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("websocket is not open"));
    }
    const id = this.nextId;
    this.nextId += 1;
    const message = { id, method, params };
    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { method, resolve: resolvePromise, reject, timer });
      this.socket.send(JSON.stringify(message));
    });
  }

  close() {
    if (this.socket) {
      this.socket.close();
    }
  }
}

function findDashboardTarget(tabs, dashboardUrl) {
  const normalized = dashboardUrl.replace(/\/$/, "");
  return tabs.find((tab) => tab.type === "page" && String(tab.url || "").startsWith(normalized));
}

function captureExpression(args) {
  return `
(async () => {
  const durationMs = ${args.durationMs};
  const threshold = ${args.minRms};
  const rawAudio = ${JSON.stringify(Boolean(args.rawAudio))};
  const deviceId = ${JSON.stringify(args.deviceId || null)};
  const audioConstraints = rawAudio
    ? { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
    : true;
  if (deviceId && audioConstraints !== true) {
    audioConstraints.deviceId = { exact: deviceId };
  }
  const constraints = {
    audio: audioConstraints === true && deviceId ? { deviceId: { exact: deviceId } } : audioConstraints,
    video: false
  };
  const result = {
    permissionState: null,
    requestedConstraints: constraints,
    selectedAudioTrack: null,
    sampleRate: null,
    sampleCount: 0,
    maxRms: 0,
    peak: 0,
    meanAbs: 0,
    aboveThresholdCount: 0,
    wavBase64: null,
    error: null
  };

  function floatTo16BitPcm(floatData) {
    const bytes = new Uint8Array(floatData.length * 2);
    const view = new DataView(bytes.buffer);
    for (let index = 0; index < floatData.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, floatData[index]));
      view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return bytes;
  }

  function writeString(view, offset, value) {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  }

  function wavBytesFromFloat(floatData, sampleRate) {
    const pcm = floatTo16BitPcm(floatData);
    const bytes = new Uint8Array(44 + pcm.length);
    const view = new DataView(bytes.buffer);
    writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + pcm.length, true);
    writeString(view, 8, "WAVE");
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(view, 36, "data");
    view.setUint32(40, pcm.length, true);
    bytes.set(pcm, 44);
    return bytes;
  }

  function base64FromBytes(bytes) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
    }
    return btoa(binary);
  }

  try {
    if (navigator.permissions && navigator.permissions.query) {
      try {
        const permission = await navigator.permissions.query({ name: "microphone" });
        result.permissionState = permission.state;
      } catch (error) {
        result.permissionState = "unknown";
      }
    }
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const track = stream.getAudioTracks()[0];
    result.selectedAudioTrack = track ? {
      label: track.label,
      settings: track.getSettings ? track.getSettings() : null
    } : null;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContextClass({ sampleRate: 16000 });
    result.sampleRate = context.sampleRate;
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    processor.onaudioprocess = (event) => {
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(processor);
    processor.connect(context.destination);
    await new Promise((resolvePromise) => setTimeout(resolvePromise, durationMs));
    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((item) => item.stop());
    await context.close();
    const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
    const audio = new Float32Array(sampleCount);
    let offset = 0;
    for (const chunk of chunks) {
      audio.set(chunk, offset);
      offset += chunk.length;
    }
    let sumSquares = 0;
    let sumAbs = 0;
    let peak = 0;
    let above = 0;
    const windowSize = Math.max(1, Math.floor(result.sampleRate * 0.1));
    let windowSquares = 0;
    for (let index = 0; index < audio.length; index += 1) {
      const value = audio[index];
      const abs = Math.abs(value);
      sumSquares += value * value;
      sumAbs += abs;
      peak = Math.max(peak, abs);
      windowSquares += value * value;
      if ((index + 1) % windowSize === 0) {
        const rms = Math.sqrt(windowSquares / windowSize);
        if (rms >= threshold) {
          above += 1;
        }
        windowSquares = 0;
      }
    }
    const rms = audio.length ? Math.sqrt(sumSquares / audio.length) : 0;
    result.sampleCount = audio.length;
    result.maxRms = rms;
    result.peak = peak;
    result.meanAbs = audio.length ? sumAbs / audio.length : 0;
    result.aboveThresholdCount = above;
    result.wavBase64 = base64FromBytes(wavBytesFromFloat(audio, result.sampleRate));
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  }
  return result;
})()
`;
}

async function connectDashboard(args) {
  const tabs = await fetchJson(endpoint(args.cdpUrl, "/json/list"), args.timeoutMs);
  const target = findDashboardTarget(tabs, args.dashboardUrl);
  if (!target) {
    throw new Error(`Kiwi dashboard tab not found at ${args.dashboardUrl}; open it in the windows-cdp profile first`);
  }
  const cdp = new CdpConnection(target.webSocketDebuggerUrl, args.timeoutMs);
  await cdp.connect();
  await cdp.send("Page.bringToFront");
  return cdp;
}

async function existingSamples(outDir) {
  try {
    const entries = await readdir(outDir);
    return entries.filter((entry) => entry.endsWith(".wav")).sort().map((entry) => resolve(outDir, entry));
  } catch {
    return [];
  }
}

async function readManifest(manifestPath) {
  try {
    return JSON.parse(await readFile(manifestPath, "utf8"));
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

async function printStatus(args) {
  const manifestPath = resolve(args.outDir, "manifest.json");
  const samples = await existingSamples(args.outDir);
  const report = {
    mode: "kiwi_browser_stt_capture_status",
    status: "pending",
    outDir: args.outDir,
    manifest: manifestPath,
    sampleCount: samples.length,
    samples,
    manifestStatus: null,
    phrase: null,
    error: null,
  };
  try {
    const manifest = await readManifest(manifestPath);
    if (manifest) {
      report.manifestStatus = manifest.status || null;
      report.phrase = manifest.phrase || null;
      report.status = samples.length > 0 ? "ok" : manifest.status || "pending";
    }
  } catch (error) {
    report.status = "warning";
    report.error = String(error && error.message ? error.message : error);
  }
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  if (args.status) {
    await printStatus(args);
    return;
  }

  await mkdir(args.outDir, { recursive: true });
  const report = {
    timestamp: nowIso(),
    mode: "kiwi_browser_stt_capture_probe",
    status: "blocked",
    cdpUrl: args.cdpUrl,
    dashboardUrl: args.dashboardUrl,
    count: args.count,
    durationMs: args.durationMs,
    gapMs: args.gapMs,
    rawAudio: args.rawAudio,
    deviceId: args.deviceId,
    minRms: args.minRms,
    phrase: args.phrase,
    samples: [],
    error: null,
  };

  let cdp;
  try {
    cdp = await connectDashboard(args);
    for (let index = 1; index <= args.count; index += 1) {
      if (index > 1 && args.gapMs > 0) {
        await sleep(args.gapMs);
      }
      const evaluation = await cdp.send("Runtime.evaluate", {
        expression: captureExpression(args),
        awaitPromise: true,
        returnByValue: true,
      });
      const value = evaluation.result?.value;
      if (!value || value.error) {
        throw new Error(value?.error || "browser capture returned no value");
      }
      const wavPath = resolve(args.outDir, `sample-${String(index).padStart(2, "0")}.wav`);
      await writeFile(wavPath, Buffer.from(value.wavBase64, "base64"));
      delete value.wavBase64;
      report.samples.push({
        index,
        path: wavPath,
        measurement: {
          rms: Number(value.maxRms.toFixed(6)),
          peak: Number(value.peak.toFixed(6)),
          meanAbs: Number(value.meanAbs.toFixed(6)),
          samples: value.sampleCount,
          sampleRate: value.sampleRate,
          aboveThresholdCount: value.aboveThresholdCount,
        },
        permissionState: value.permissionState,
        selectedAudioTrack: value.selectedAudioTrack,
        passedRmsGate: value.maxRms >= args.minRms || value.aboveThresholdCount > 0,
      });
    }
    report.status = report.samples.some((sample) => sample.passedRmsGate) ? "passed" : "blocked";
  } catch (error) {
    report.status = "failed";
    report.error = String(error && error.message ? error.message : error);
  } finally {
    if (cdp) {
      cdp.close();
    }
  }

  const manifestPath = resolve(args.outDir, "manifest.json");
  await writeFile(manifestPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (report.status === "failed") {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
