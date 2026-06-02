// cdp_probe.mjs - inspect raw Chromium CDP behavior without OpenClaw browser wrappers.
import { execFile } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ARTIFACT_DIR = resolve(ROOT, ".debugloop/artifacts/browser");
const DEFAULT_CDP_URL = process.env.OPENCLAW_CDP_URL || "http://127.0.0.1:18800";
const DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.CDP_PROBE_TIMEOUT_MS || "5000", 10);

function usage() {
  return `Usage: node scripts/wsl/cdp_probe.mjs [options]

Options:
  --cdp-url <url>      CDP HTTP endpoint to inspect (default: ${DEFAULT_CDP_URL})
  --timeout-ms <ms>    Per-command timeout in milliseconds (default: ${DEFAULT_TIMEOUT_MS})
  --out <path>         JSON artifact path (default: .debugloop/artifacts/browser/cdp-probe.json)
  -h, --help           Show this help
`;
}

function parseArgs(argv) {
  const args = {
    cdpUrl: DEFAULT_CDP_URL,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    out: resolve(ARTIFACT_DIR, "cdp-probe.json"),
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
    if (arg === "--timeout-ms") {
      const value = Number.parseInt(requiredValue(argv, ++index, arg), 10);
      if (!Number.isFinite(value) || value <= 0) {
        throw new Error("--timeout-ms must be a positive integer");
      }
      args.timeoutMs = value;
      continue;
    }
    if (arg === "--out") {
      const value = requiredValue(argv, ++index, arg);
      args.out = isAbsolute(value) ? value : resolve(ROOT, value);
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

function nowIso() {
  return new Date().toISOString();
}

function short(value, limit = 1000) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length <= limit ? text : `${text.slice(0, limit - 3)}...`;
}

function cdpEndpoint(cdpUrl, path) {
  return new URL(path, cdpUrl.endsWith("/") ? cdpUrl : `${cdpUrl}/`).toString();
}

async function fetchJson(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} ${response.statusText}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
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

      const cleanup = () => {
        clearTimeout(timer);
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("error", onError);
      };
      const onOpen = () => {
        cleanup();
        resolvePromise();
      };
      const onError = () => {
        cleanup();
        reject(new Error("websocket error before open"));
      };

      socket.addEventListener("open", onOpen);
      socket.addEventListener("error", onError);
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

  send(method, params = {}, sessionId = null) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("websocket is not open"));
    }

    const id = this.nextId;
    this.nextId += 1;
    const message = { id, method, params };
    if (sessionId) {
      message.sessionId = sessionId;
    }

    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { method, resolve: resolvePromise, reject, timer });

      try {
        this.socket.send(JSON.stringify(message));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  close() {
    if (!this.socket) {
      return;
    }
    try {
      this.socket.close();
    } catch {
      // Best effort cleanup only.
    }
  }
}

async function cdpCommand(wsUrl, method, params, timeoutMs) {
  const connection = new CdpConnection(wsUrl, timeoutMs);
  await connection.connect();
  try {
    return await connection.send(method, params);
  } finally {
    connection.close();
  }
}

async function withAttachedSession(version, target, timeoutMs, callback) {
  if (!version?.webSocketDebuggerUrl) {
    throw new Error("missing browser webSocketDebuggerUrl");
  }
  if (!target?.id) {
    throw new Error("missing target id");
  }

  const connection = new CdpConnection(version.webSocketDebuggerUrl, timeoutMs);
  await connection.connect();
  let sessionId = null;
  try {
    const attach = await connection.send("Target.attachToTarget", {
      targetId: target.id,
      flatten: true,
    });
    sessionId = attach.sessionId;
    if (!sessionId) {
      throw new Error("Target.attachToTarget returned no sessionId");
    }
    return await callback(connection, sessionId);
  } finally {
    if (sessionId) {
      await connection.send("Target.detachFromTarget", { sessionId }).catch(() => {});
    }
    connection.close();
  }
}

async function runStep(name, fn) {
  const started = Date.now();
  try {
    const result = await fn();
    return {
      name,
      status: "ok",
      durationMs: Date.now() - started,
      result,
    };
  } catch (error) {
    return {
      name,
      status: "blocked",
      durationMs: Date.now() - started,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function choosePageTarget(targets) {
  return (
    targets.find((target) => target.type === "page" && target.url === "https://example.com/") ||
    targets.find((target) => target.type === "page" && target.url?.startsWith("https://example.com")) ||
    targets.find((target) => target.type === "page" && target.url && !target.url.startsWith("chrome://")) ||
    targets.find((target) => target.type === "page")
  );
}

async function getProcessDiagnostics(cdpUrl) {
  const port = new URL(cdpUrl).port;
  const { stdout } = await execFileAsync("ps", ["-eo", "pid=,ppid=,stat=,args="], {
    maxBuffer: 2 * 1024 * 1024,
  });
  const rows = stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.includes("chrome") || line.includes("chromium"));
  const browserRow = rows.find((line) => line.includes(`--remote-debugging-port=${port}`)) || null;
  const rendererRows = rows.filter((line) => line.includes("--type=renderer"));
  const gpuRows = rows.filter((line) => line.includes("--type=gpu-process"));
  const utilityRows = rows.filter((line) => line.includes("--type=utility"));

  return {
    cdpPort: port,
    processCount: rows.length,
    rendererCount: rendererRows.length,
    gpuProcessCount: gpuRows.length,
    utilityProcessCount: utilityRows.length,
    hasRemoteDebuggingBrowser: Boolean(browserRow),
    browserArgs: browserRow ? browserRow.replace(/\s+/g, " ") : null,
    sampleProcessArgs: rows.slice(0, 8).map((line) => line.replace(/\s+/g, " ")),
  };
}

function pageTargetsFor(targets, preferredTarget) {
  const pageTargets = targets.filter((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (preferredTarget) {
    pageTargets.sort((left, right) => {
      if (left.id === preferredTarget.id) {
        return -1;
      }
      if (right.id === preferredTarget.id) {
        return 1;
      }
      return 0;
    });
  }
  return pageTargets;
}

async function tryDirectRuntimeEvaluate(targets, preferredTarget, record, timeoutMs) {
  const pageTargets = pageTargetsFor(targets, preferredTarget);
  if (pageTargets.length === 0) {
    throw new Error("missing page webSocketDebuggerUrl");
  }

  const attempts = [];
  for (const target of pageTargets) {
    try {
      const result = await cdpCommand(
        target.webSocketDebuggerUrl,
        "Runtime.evaluate",
        {
          expression: "document.title",
          returnByValue: true,
        },
        timeoutMs
      );
      record.target = {
        id: target.id,
        title: target.title,
        type: target.type,
        url: target.url,
      };
      return {
        activeTarget: target,
        result: {
          targetId: target.id,
          targetUrl: target.url,
          targetTitle: target.title,
          attempts,
          type: result.result?.type,
          value: result.result?.value ?? null,
        },
      };
    } catch (error) {
      attempts.push({
        targetId: target.id,
        targetUrl: target.url,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  throw new Error(`Runtime.evaluate failed on all page targets: ${JSON.stringify(attempts)}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }

  await mkdir(dirname(args.out), { recursive: true });
  await mkdir(ARTIFACT_DIR, { recursive: true });

  const record = {
    timestamp: nowIso(),
    mode: "raw_cdp_probe",
    cdpUrl: args.cdpUrl,
    timeoutMs: args.timeoutMs,
    status: "blocked",
    target: null,
    checks: [],
    artifacts: {},
  };

  let version = null;
  let targets = [];
  let pageTarget = null;
  let activePageTarget = null;

  record.checks.push(
    await runStep("process_diagnostics", async () => getProcessDiagnostics(args.cdpUrl))
  );

  const versionStep = await runStep("json_version", async () => {
    version = await fetchJson(cdpEndpoint(args.cdpUrl, "json/version"), args.timeoutMs);
    return {
      browser: version.Browser,
      protocolVersion: version["Protocol-Version"],
      hasBrowserWebSocket: Boolean(version.webSocketDebuggerUrl),
    };
  });
  record.checks.push(versionStep);

  const listStep = await runStep("json_list", async () => {
    targets = await fetchJson(cdpEndpoint(args.cdpUrl, "json/list"), args.timeoutMs);
    pageTarget = choosePageTarget(targets);
    record.target = pageTarget
      ? {
          id: pageTarget.id,
          title: pageTarget.title,
          type: pageTarget.type,
          url: pageTarget.url,
        }
      : null;
    return {
      targetCount: Array.isArray(targets) ? targets.length : 0,
      pageCount: Array.isArray(targets) ? targets.filter((target) => target.type === "page").length : 0,
      selectedUrl: pageTarget?.url || null,
      selectedTitle: pageTarget?.title || null,
    };
  });
  record.checks.push(listStep);

  record.checks.push(
    await runStep("browser_get_version", async () => {
      if (!version?.webSocketDebuggerUrl) {
        throw new Error("missing browser webSocketDebuggerUrl");
      }
      const result = await cdpCommand(version.webSocketDebuggerUrl, "Browser.getVersion", {}, args.timeoutMs);
      return {
        product: result.product,
        protocolVersion: result.protocolVersion,
        userAgent: result.userAgent,
      };
    })
  );

  record.checks.push(
    await runStep("runtime_evaluate_title", async () => {
      const outcome = await tryDirectRuntimeEvaluate(targets, pageTarget, record, args.timeoutMs);
      activePageTarget = outcome.activeTarget;
      return outcome.result;
    })
  );

  record.checks.push(
    await runStep("target_attach_runtime_evaluate", async () => {
      const target = activePageTarget || pageTarget;
      const result = await withAttachedSession(version, target, args.timeoutMs, (connection, sessionId) =>
        connection.send(
          "Runtime.evaluate",
          {
            expression: "document.title",
            returnByValue: true,
          },
          sessionId
        )
      );
      return {
        targetId: target.id,
        targetUrl: target.url,
        targetTitle: target.title,
        type: result.result?.type,
        value: result.result?.value ?? null,
      };
    })
  );

  record.checks.push(
    await runStep("page_capture_screenshot", async () => {
      const target = activePageTarget || pageTarget;
      if (!target?.webSocketDebuggerUrl) {
        throw new Error("missing page webSocketDebuggerUrl");
      }
      await cdpCommand(target.webSocketDebuggerUrl, "Page.enable", {}, args.timeoutMs);
      const result = await cdpCommand(
        target.webSocketDebuggerUrl,
        "Page.captureScreenshot",
        {
          format: "png",
          fromSurface: true,
        },
        args.timeoutMs
      );
      if (!result.data) {
        throw new Error("Page.captureScreenshot returned no data");
      }
      const outPath = resolve(ARTIFACT_DIR, "cdp-screenshot.png");
      await writeFile(outPath, Buffer.from(result.data, "base64"));
      record.artifacts.screenshot = relative(ROOT, outPath);
      return {
        base64Length: result.data.length,
        artifact: record.artifacts.screenshot,
      };
    })
  );

  record.checks.push(
    await runStep("target_attach_capture_screenshot", async () => {
      const target = activePageTarget || pageTarget;
      const result = await withAttachedSession(version, target, args.timeoutMs, async (connection, sessionId) => {
        await connection.send("Page.enable", {}, sessionId);
        return connection.send(
          "Page.captureScreenshot",
          {
            format: "png",
            fromSurface: true,
          },
          sessionId
        );
      });
      if (!result.data) {
        throw new Error("Page.captureScreenshot returned no data");
      }
      const outPath = resolve(ARTIFACT_DIR, "cdp-attach-screenshot.png");
      await writeFile(outPath, Buffer.from(result.data, "base64"));
      record.artifacts.attachScreenshot = relative(ROOT, outPath);
      return {
        base64Length: result.data.length,
        artifact: record.artifacts.attachScreenshot,
      };
    })
  );

  record.checks.push(
    await runStep("accessibility_get_full_ax_tree", async () => {
      const target = activePageTarget || pageTarget;
      if (!target?.webSocketDebuggerUrl) {
        throw new Error("missing page webSocketDebuggerUrl");
      }
      const result = await cdpCommand(target.webSocketDebuggerUrl, "Accessibility.getFullAXTree", {}, args.timeoutMs);
      return {
        nodeCount: Array.isArray(result.nodes) ? result.nodes.length : 0,
        firstNodeName: result.nodes?.[0]?.name?.value || null,
        firstNodeRole: result.nodes?.[0]?.role?.value || null,
      };
    })
  );

  const checksByName = Object.fromEntries(record.checks.map((check) => [check.name, check]));
  const requiredBaseline = ["json_version", "json_list", "browser_get_version"];
  const baselineOk = requiredBaseline.every((name) => checksByName[name]?.status === "ok");
  const runtimeOk =
    checksByName.runtime_evaluate_title?.status === "ok" ||
    checksByName.target_attach_runtime_evaluate?.status === "ok";
  const screenshotOk =
    checksByName.page_capture_screenshot?.status === "ok" ||
    checksByName.target_attach_capture_screenshot?.status === "ok";

  record.status = baselineOk && runtimeOk && screenshotOk ? "ok" : "blocked";
  record.failedAt =
    requiredBaseline.find((name) => checksByName[name]?.status !== "ok") ||
    (!runtimeOk ? "runtime_evaluate_title" : null) ||
    (!screenshotOk ? "page_capture_screenshot" : null);

  await writeFile(args.out, `${JSON.stringify(record, null, 2)}\n`, "utf-8");

  console.log(`status: ${record.status}`);
  if (record.failedAt) {
    console.log(`failedAt: ${record.failedAt}`);
  }
  console.log(`target: ${record.target?.title || "<none>"} ${record.target?.url || ""}`.trim());
  for (const check of record.checks) {
    console.log(`- ${check.name}: ${check.status} (${check.durationMs}ms)`);
    if (check.status === "ok") {
      console.log(`  result: ${short(check.result)}`);
    } else {
      console.log(`  error: ${check.error}`);
    }
  }
  for (const [name, path] of Object.entries(record.artifacts)) {
    console.log(`artifact[${name}]: ${path}`);
  }
  console.log(`artifact[record]: ${relative(ROOT, args.out)}`);

  process.exitCode = record.status === "ok" ? 0 : 1;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
