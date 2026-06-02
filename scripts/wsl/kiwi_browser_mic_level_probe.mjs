// kiwi_browser_mic_level_probe.mjs - measure browser microphone RMS/peak through the dashboard CDP tab.
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ARTIFACT_DIR = resolve(ROOT, ".debugloop/artifacts/kiwi");
const RUNS_DIR = resolve(ROOT, ".debugloop/runs");
const DEFAULT_CDP_URL = process.env.KIWI_CDP_URL || "http://127.0.0.1:9222";
const DEFAULT_DASHBOARD_URL = "http://127.0.0.1:7789";
const KIWI_SPEECH_GATE = 0.015;

function usage() {
  return `Usage: node scripts/wsl/kiwi_browser_mic_level_probe.mjs [options]

Options:
  --cdp-url <url>        Chrome CDP HTTP endpoint (default: ${DEFAULT_CDP_URL})
  --dashboard-url <url>  Kiwi dashboard URL prefix (default: ${DEFAULT_DASHBOARD_URL})
  --duration-ms <ms>     Recording duration; speak during this window (default: 6000)
  --interval-ms <ms>     Sampling interval (default: 100)
  --timeout-ms <ms>      CDP command timeout (default: 12000)
  --out <path>           JSON artifact path (default: .debugloop/artifacts/kiwi/browser-mic-level.json)
  --jsonl <path>         JSONL run log path (default: .debugloop/runs/latest.jsonl)
  -h, --help             Show this help
`;
}

function parseArgs(argv) {
  const args = {
    cdpUrl: DEFAULT_CDP_URL,
    dashboardUrl: DEFAULT_DASHBOARD_URL,
    durationMs: 6000,
    intervalMs: 100,
    timeoutMs: 12000,
    out: resolve(ARTIFACT_DIR, "browser-mic-level.json"),
    jsonl: resolve(RUNS_DIR, "latest.jsonl"),
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
    if (arg === "--duration-ms") {
      args.durationMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--interval-ms") {
      args.intervalMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
      continue;
    }
    if (arg === "--timeout-ms") {
      args.timeoutMs = positiveInteger(requiredValue(argv, ++index, arg), arg);
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

function endpoint(cdpUrl, path) {
  return new URL(path, cdpUrl.endsWith("/") ? cdpUrl : `${cdpUrl}/`).toString();
}

function nowIso() {
  return new Date().toISOString();
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

function measurementExpression(durationMs, intervalMs, threshold) {
  return `
(async () => {
  const durationMs = ${durationMs};
  const intervalMs = ${intervalMs};
  const threshold = ${threshold};
  const result = {
    permissionState: null,
    sampleCount: 0,
    maxRms: 0,
    maxPeak: 0,
    meanRms: 0,
    aboveThresholdCount: 0,
    samples: [],
    error: null
  };
  try {
    if (navigator.permissions && navigator.permissions.query) {
      try {
        const permission = await navigator.permissions.query({ name: "microphone" });
        result.permissionState = permission.state;
      } catch (error) {
        result.permissionState = "unknown";
      }
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContextClass();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    const data = new Float32Array(analyser.fftSize);
    const startedAt = performance.now();
    while (performance.now() - startedAt < durationMs) {
      analyser.getFloatTimeDomainData(data);
      let sumSquares = 0;
      let peak = 0;
      for (const value of data) {
        sumSquares += value * value;
        peak = Math.max(peak, Math.abs(value));
      }
      const rms = Math.sqrt(sumSquares / data.length);
      result.sampleCount += 1;
      result.maxRms = Math.max(result.maxRms, rms);
      result.maxPeak = Math.max(result.maxPeak, peak);
      result.meanRms += rms;
      if (rms > threshold) {
        result.aboveThresholdCount += 1;
      }
      result.samples.push({
        tMs: Math.round(performance.now() - startedAt),
        rms: Number(rms.toFixed(6)),
        peak: Number(peak.toFixed(6))
      });
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
    result.meanRms = result.sampleCount ? result.meanRms / result.sampleCount : 0;
    result.meanRms = Number(result.meanRms.toFixed(6));
    result.maxRms = Number(result.maxRms.toFixed(6));
    result.maxPeak = Number(result.maxPeak.toFixed(6));
    stream.getTracks().forEach((track) => track.stop());
    await context.close();
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  }
  return result;
})()
`;
}

function classify(measurement) {
  if (measurement.error) {
    return {
      status: "blocked",
      reason: measurement.error,
      aboveKiwiSpeechGate: false,
    };
  }
  const above = measurement.maxRms > KIWI_SPEECH_GATE || measurement.aboveThresholdCount > 0;
  if (!above) {
    return {
      status: "warning",
      reason: `maxRms ${measurement.maxRms} did not exceed Kiwi speech gate ${KIWI_SPEECH_GATE}`,
      aboveKiwiSpeechGate: false,
    };
  }
  return {
    status: "ok",
    reason: `maxRms ${measurement.maxRms} exceeded Kiwi speech gate ${KIWI_SPEECH_GATE}`,
    aboveKiwiSpeechGate: true,
  };
}

async function writeJson(path, data) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

async function appendJsonl(path, record) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(record)}\n`, { encoding: "utf8", flag: "a" });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return 0;
  }

  const tabs = await fetchJson(endpoint(args.cdpUrl, "/json/list"), args.timeoutMs);
  const target = findDashboardTarget(tabs, args.dashboardUrl);
  if (!target?.webSocketDebuggerUrl) {
    throw new Error(`No Kiwi dashboard tab found at ${args.dashboardUrl}`);
  }

  const connection = new CdpConnection(target.webSocketDebuggerUrl, args.timeoutMs + args.durationMs);
  await connection.connect();
  let evaluated;
  try {
    evaluated = await connection.send("Runtime.evaluate", {
      expression: measurementExpression(args.durationMs, args.intervalMs, KIWI_SPEECH_GATE),
      awaitPromise: true,
      returnByValue: true,
    });
  } finally {
    connection.close();
  }

  const measurement = evaluated?.result?.value || { error: "Runtime.evaluate returned no measurement value" };
  const verdict = classify(measurement);
  const record = {
    timestamp: nowIso(),
    mode: "kiwi_browser_mic_level_probe",
    status: verdict.status,
    cdpUrl: args.cdpUrl,
    dashboardUrl: args.dashboardUrl,
    target: {
      id: target.id,
      title: target.title,
      url: target.url,
    },
    probe: {
      durationMs: args.durationMs,
      intervalMs: args.intervalMs,
      kiwiSpeechGateMeanAbs: KIWI_SPEECH_GATE,
    },
    verdict,
    measurement,
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
