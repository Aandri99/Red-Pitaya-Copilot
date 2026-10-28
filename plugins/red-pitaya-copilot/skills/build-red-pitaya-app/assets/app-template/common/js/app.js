"use strict";

const APP = Object.freeze({
  slug: "__APP_SLUG__",
  version: "__APP_VERSION__",
  backend: "__BACKEND__",
  apiEnabled: __API_ENABLED__,
  apiRoot: "/__APP_SLUG__/api",
  websocketEnabled: __WEBSOCKET_ENABLED__,
  websocketPath: "/__APP_SLUG__/ws",
});

const elements = {
  state: document.querySelector("#statePill"),
  connection: document.querySelector("#connectionValue"),
  service: document.querySelector("#serviceValue"),
  safe: document.querySelector("#safeValue"),
  version: document.querySelector("#versionValue"),
  time: document.querySelector("#timeValue"),
  refresh: document.querySelector("#refreshBtn"),
  stop: document.querySelector("#stopBtn"),
  note: document.querySelector("#transportNote"),
  log: document.querySelector("#log"),
  toast: document.querySelector("#toast"),
};

let busy = false;
let requestSequence = 0;
let webSocket = null;
let websocketReconnectAttempt = 0;
let websocketReconnectTimer = null;
let websocketHeartbeatTimer = null;
let websocketEpoch = null;
let websocketSequence = 0;
let websocketClosing = false;

function setBusy(value) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value;
  });
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.className = "show";
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    elements.toast.className = "";
  }, 3000);
}

function writeLog(title, value) {
  const timestamp = new Date().toISOString();
  const rendered = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  elements.log.textContent = `[${timestamp}] ${title}\n${rendered}\n\n${elements.log.textContent}`;
}

async function request(action, method = "GET") {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${APP.apiRoot}/${encodeURIComponent(action)}`, {
      method,
      cache: "no-store",
      headers: { "Accept": "application/json" },
      signal: controller.signal,
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error(`Expected JSON but received ${contentType || "unknown content type"}`);
    }
    const data = await response.json();
    if (!response.ok || data.ok !== true) {
      const detail = data.error?.message || data.error || `HTTP ${response.status}`;
      throw new Error(String(detail));
    }
    return data;
  } finally {
    window.clearTimeout(timeout);
  }
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${APP.websocketPath}`;
}

function clearWebsocketTimers() {
  window.clearTimeout(websocketReconnectTimer);
  window.clearInterval(websocketHeartbeatTimer);
  websocketReconnectTimer = null;
  websocketHeartbeatTimer = null;
}

function scheduleWebsocketReconnect() {
  if (websocketClosing || !APP.websocketEnabled) return;
  const baseSeconds = Math.min(30, 2 ** Math.min(websocketReconnectAttempt, 5));
  const jitter = 0.8 + Math.random() * 0.4;
  websocketReconnectAttempt += 1;
  websocketReconnectTimer = window.setTimeout(
    connectWebsocket,
    Math.round(baseSeconds * jitter * 1000),
  );
}

function sendWebsocketMessage(payload) {
  if (webSocket?.readyState !== WebSocket.OPEN) return;
  webSocket.send(JSON.stringify(payload));
}

function handleWebsocketMessage(event) {
  let message;
  try {
    message = JSON.parse(event.data);
  } catch (_error) {
    writeLog("WebSocket protocol error", { error: "non-JSON message" });
    webSocket?.close(1003, "JSON required");
    return;
  }
  if (
    message === null
    || typeof message !== "object"
    || message.protocol_version !== 1
    || typeof message.service_epoch !== "string"
    || !Number.isSafeInteger(message.sequence)
  ) {
    writeLog("WebSocket protocol error", { error: "invalid envelope", message });
    webSocket?.close(1003, "invalid envelope");
    return;
  }

  if (websocketEpoch !== message.service_epoch) {
    websocketEpoch = message.service_epoch;
    websocketSequence = 0;
  }
  if (websocketSequence && message.sequence !== websocketSequence + 1) {
    writeLog("WebSocket sequence gap", {
      expected: websocketSequence + 1,
      received: message.sequence,
      policy: "refresh current status; no durable-event recovery is implied",
    });
    run("status", "GET");
  }
  websocketSequence = message.sequence;

  if (message.type === "status" && message.status) {
    renderStatus({ ok: true, status: message.status });
  } else if (message.type === "error") {
    writeLog("WebSocket service error", message);
  }
}

function connectWebsocket() {
  if (websocketClosing || !APP.websocketEnabled) return;
  clearWebsocketTimers();
  webSocket = new WebSocket(websocketUrl());
  webSocket.addEventListener("open", () => {
    websocketReconnectAttempt = 0;
    websocketSequence = 0;
    elements.note.textContent = "Live status connected. The stream is best-effort, not durable history.";
    websocketHeartbeatTimer = window.setInterval(() => {
      sendWebsocketMessage({
        type: "ping",
        request_id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      });
    }, 10000);
  });
  webSocket.addEventListener("message", handleWebsocketMessage);
  webSocket.addEventListener("error", () => {
    writeLog("WebSocket transport error", { path: APP.websocketPath });
  });
  webSocket.addEventListener("close", () => {
    clearWebsocketTimers();
    webSocket = null;
    if (!websocketClosing) {
      elements.note.textContent = "Live status disconnected; HTTP status remains authoritative.";
      scheduleWebsocketReconnect();
    }
  });
}

function renderStatus(data) {
  const status = data.status || {};
  elements.connection.textContent = "connected";
  elements.service.textContent = String(status.service ?? "unknown");
  elements.safe.textContent = String(status.safe_state ?? "unknown");
  elements.version.textContent = String(status.version ?? APP.version);
  elements.time.textContent = String(status.server_time ?? "unknown");

  const safe = status.safe_state === true;
  const fault = status.service === "fault" || status.safe_state === false;
  elements.state.textContent = safe ? "Safe" : fault ? "Attention" : "Unknown";
  elements.state.className = safe ? "pill safe" : fault ? "pill fault" : "pill unknown";
}

function renderUnavailable(message) {
  elements.connection.textContent = "unavailable";
  elements.service.textContent = "unknown";
  elements.safe.textContent = "unknown";
  elements.state.textContent = "Unavailable";
  elements.state.className = "pill fault";
  elements.note.textContent = message;
}

async function run(action, method) {
  if (busy || !APP.apiEnabled) return;
  const sequence = ++requestSequence;
  setBusy(true);
  try {
    const data = await request(action, method);
    if (sequence === requestSequence) renderStatus(data);
    writeLog(`${method} ${action}`, data);
    showToast(`${action} complete`);
  } catch (error) {
    const message = error.name === "AbortError" ? "Request timed out" : error.message;
    if (sequence === requestSequence) renderUnavailable(message);
    writeLog(`${method} ${action} failed`, { error: message });
    showToast(message);
  } finally {
    if (sequence === requestSequence) setBusy(false);
  }
}

elements.refresh.addEventListener("click", () => run("status", "GET"));
elements.stop.addEventListener("click", () => run("stop", "POST"));

if (APP.apiEnabled) {
  elements.note.textContent = "Status is board-reported. Safe stop is independent from counter/history reset.";
  run("status", "GET");
  if (APP.websocketEnabled) connectWebsocket();
} else {
  elements.refresh.disabled = true;
  elements.stop.disabled = true;
  elements.connection.textContent = APP.backend === "static" ? "not required" : "integration required";
  elements.service.textContent = APP.backend === "static" ? "static" : "not connected";
  elements.safe.textContent = APP.backend === "static" ? "no hardware access" : "unknown";
  elements.state.textContent = APP.backend === "static" ? "Static" : "Integration required";
  elements.state.className = APP.backend === "static" ? "pill safe" : "pill unknown";
  elements.note.textContent = APP.backend === "static"
    ? "This scaffold has no board API or hardware ownership."
    : "Reconcile the native WebSocket transport with a matching target-image example before deployment.";
}

window.addEventListener("beforeunload", () => {
  websocketClosing = true;
  clearWebsocketTimers();
  webSocket?.close(1000, "page closing");
});
