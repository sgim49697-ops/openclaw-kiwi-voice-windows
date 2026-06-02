// cdp_probe.mjs - inspect raw Chromium CDP behavior without OpenClaw browser wrappers.
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ARTIFACT_DIR = resolve(ROOT, ".debugloop/artifacts/browser");
const DEFAULT_CDP_URL = process.env.OPENCLAW_CDP_URL || "http://127.0.0.1:18800";
const TIMEOUT_MS = Number.parseInt(process.env.CDP_PROBE_TIMEOUT_MS || "5000", 10);

function nowIso() {
  return new Date().toISOString();
}

function short(value, limit = 1000) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length <= limit ? text : `${text.slice(0, limit - 3)}...`;
}

async function fetchJson(url, timeoutMs = TIMEOUT_MS) {
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

function cdpCommand(wsUrl, method, params = {}, timeoutMs = TIMEOUT_MS) {
  return new Promise((resolvePromise, reject) => {
    if (typeof WebSocket === "undefined") {
      reject(new Error("global WebSocket is unavailable; Node 22+ is required"));
      return;
    }

    const id = 1;
    const socket = new WebSocket(wsUrl);
    let settled = false;

    const finish = (fn, value) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        // Best effort cleanup only.
      }
      fn(value);
    };

    const timer = setTimeout(() => {
      finish(reject, new Error(`${method} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ id, method, params }));
    });

    socket.addEventListener("message", (event) => {
      const raw = typeof event.data === "string" ? event.data : event.data.toString();
      const message = JSON.parse(raw);
      if (message.id !== id) {
        return;
      }
      if (message.error) {
        finish(reject, new Error(`${method} failed: ${JSON.stringify(message.error)}`));
        return;
      }
      finish(resolvePromise, message.result || {});
    });

    socket.addEventListener("error", () => {
      finish(reject, new Error(`${method} websocket error`));
    });

    socket.addEventListener("close", () => {
      finish(reject, new Error(`${method} websocket closed before response`));
    });
  });
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

async function main() {
  await mkdir(ARTIFACT_DIR, { recursive: true });

  const record = {
    timestamp: nowIso(),
    mode: "raw_cdp_probe",
    cdpUrl: DEFAULT_CDP_URL,
    timeoutMs: TIMEOUT_MS,
    status: "blocked",
    target: null,
    checks: [],
    artifacts: {},
  };

  let version = null;
  let targets = [];
  let pageTarget = null;
  let activePageTarget = null;

  const versionStep = await runStep("json_version", async () => {
    version = await fetchJson(`${DEFAULT_CDP_URL}/json/version`);
    return {
      browser: version.Browser,
      protocolVersion: version["Protocol-Version"],
      hasBrowserWebSocket: Boolean(version.webSocketDebuggerUrl),
    };
  });
  record.checks.push(versionStep);

  const listStep = await runStep("json_list", async () => {
    targets = await fetchJson(`${DEFAULT_CDP_URL}/json/list`);
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
      const result = await cdpCommand(version.webSocketDebuggerUrl, "Browser.getVersion");
      return {
        product: result.product,
        protocolVersion: result.protocolVersion,
        userAgent: result.userAgent,
      };
    })
  );

  record.checks.push(
    await runStep("runtime_evaluate_title", async () => {
      const pageTargets = targets.filter((target) => target.type === "page" && target.webSocketDebuggerUrl);
      if (pageTarget) {
        pageTargets.sort((left, right) => {
          if (left.id === pageTarget.id) {
            return -1;
          }
          if (right.id === pageTarget.id) {
            return 1;
          }
          return 0;
        });
      }
      if (pageTargets.length === 0) {
        throw new Error("missing page webSocketDebuggerUrl");
      }

      const attempts = [];
      for (const target of pageTargets) {
        try {
          const result = await cdpCommand(target.webSocketDebuggerUrl, "Runtime.evaluate", {
            expression: "document.title",
            returnByValue: true,
          });
          activePageTarget = target;
          record.target = {
            id: target.id,
            title: target.title,
            type: target.type,
            url: target.url,
          };
          return {
            targetId: target.id,
            targetUrl: target.url,
            targetTitle: target.title,
            attempts,
            type: result.result?.type,
            value: result.result?.value ?? null,
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
    })
  );

  record.checks.push(
    await runStep("page_capture_screenshot", async () => {
      const target = activePageTarget || pageTarget;
      if (!target?.webSocketDebuggerUrl) {
        throw new Error("missing page webSocketDebuggerUrl");
      }
      await cdpCommand(target.webSocketDebuggerUrl, "Page.enable");
      const result = await cdpCommand(target.webSocketDebuggerUrl, "Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
      });
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
    await runStep("accessibility_get_full_ax_tree", async () => {
      const target = activePageTarget || pageTarget;
      if (!target?.webSocketDebuggerUrl) {
        throw new Error("missing page webSocketDebuggerUrl");
      }
      const result = await cdpCommand(target.webSocketDebuggerUrl, "Accessibility.getFullAXTree");
      return {
        nodeCount: Array.isArray(result.nodes) ? result.nodes.length : 0,
        firstNodeName: result.nodes?.[0]?.name?.value || null,
        firstNodeRole: result.nodes?.[0]?.role?.value || null,
      };
    })
  );

  const required = new Set([
    "json_version",
    "json_list",
    "browser_get_version",
    "runtime_evaluate_title",
    "page_capture_screenshot",
    "accessibility_get_full_ax_tree",
  ]);
  const failures = record.checks.filter((check) => required.has(check.name) && check.status !== "ok");
  record.status = failures.length === 0 ? "ok" : "blocked";
  record.failedAt = failures[0]?.name || null;

  const outPath = resolve(ARTIFACT_DIR, "cdp-probe.json");
  await writeFile(outPath, `${JSON.stringify(record, null, 2)}\n`, "utf-8");

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
  console.log(`artifact[record]: ${relative(ROOT, outPath)}`);

  process.exitCode = record.status === "ok" ? 0 : 1;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
