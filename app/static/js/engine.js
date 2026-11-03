/* Engine screen: stats, warm-up, restart, endpoint example (R-037, R-032, R-006, R-044).
 * All API text goes through textContent; classes mirror ui/studio.html .vs-stat blocks. */

import { api, state, registerScreen, refreshEngine, loadVoices, showError, clearError } from "./core.js";

const WARM_LABEL = "Run warm-up";
const RESTART_LABEL = "Restart engine";

function stat(label, value, sub, title) {
  const box = document.createElement("div");
  box.className = "vs-stat";
  const mk = (cls, text) => {
    const s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  };
  const v = mk("v", value);
  if (title) v.title = title;
  box.append(mk("meta", label), v);
  if (sub) box.append(mk("meta", sub));
  return box;
}

const gb = mb => (mb == null ? "—" : `${(mb / 1024).toFixed(1)} GB`);

// "vLLM-Omni 0.30.0 · vLLM 0.30.0" → note "0.30.0 · vLLM 0.30.0" (value is the product name)
const RUNTIME_NAME = "vLLM-Omni";
const runtimeNote = runtime => (runtime || "").slice(RUNTIME_NAME.length).trim();

function renderStats() {
  const host = document.getElementById("engine-stats");
  const e = state.engine;
  if (!host || !e) return;
  const model = e.model || "—";
  const languages = new Set(state.voices.map(v => v.language)).size;
  const gpu = e.gpu || "Unknown";
  host.replaceChildren(
    stat("Model", model.split("/").pop(), `${model} · 2B · 48 kHz`, model),
    stat("GPU", gpu.replace(/^NVIDIA GeForce /, ""), "Shared with whisper-stt, TEI", gpu),
    stat(
      "GPU memory used",
      gb(e.vram_used_mb),
      e.vram_total_mb == null ? "" : `of ${Math.round(e.vram_total_mb / 1024)} GB, all processes on the card`,
    ),
    stat("KV cache", e.kv_cache || "—", `Max ${e.max_concurrency} concurrent requests`),
    stat("Warm RTF", e.rtf == null ? "—" : Number(e.rtf).toFixed(2), "Last 20 requests, warm-up excluded"),
    stat("First audio", e.first_audio_ms == null ? "—" : `${Math.round(e.first_audio_ms)} ms`, "Streamed PCM, warm"),
    stat("Voices", String(e.voices ?? state.voices.length), `${languages} languages`),
    stat("Runtime", RUNTIME_NAME, runtimeNote(e.runtime)),
  );
}

function renderEndpoint() {
  const pre = document.querySelector("#engine-endpoint pre");
  if (!pre) return;
  const voice = (state.voices[0] && state.voices[0].key) || "ashley-en";
  const body = { model: "voxcpm2", voice, input: "…", response_format: "pcm", stream: true };
  pre.textContent = `POST ${location.origin}/v1/audio/speech\n${JSON.stringify(body, null, 2)}`;
}

async function refresh() {
  await Promise.all([refreshEngine(), loadVoices().catch(() => {})]);
  renderStats();
  renderEndpoint();
}

let restartArmedAt = 0;
let restartTimer = null;

function disarmRestart(btn) {
  clearTimeout(restartTimer);
  restartArmedAt = 0;
  btn.textContent = RESTART_LABEL;
}

function init() {
  const warmBtn = document.getElementById("run-warmup");
  const restartBtn = document.getElementById("restart-engine");
  if (!warmBtn || !restartBtn) return;

  warmBtn.addEventListener("click", async () => {
    warmBtn.disabled = true;
    warmBtn.textContent = "Warming up…";
    clearError("engine");
    try {
      await api.post("/api/engine/warmup");
      await refresh();
    } catch (err) {
      showError("engine", err.message);
    } finally {
      warmBtn.disabled = false;
      warmBtn.textContent = WARM_LABEL;
    }
  });

  restartBtn.addEventListener("click", async () => {
    const now = Date.now();
    if (!restartArmedAt || now - restartArmedAt > 5000) {
      restartArmedAt = now;
      restartBtn.textContent = "Confirm restart";
      restartTimer = setTimeout(() => disarmRestart(restartBtn), 5000);
      return;
    }
    disarmRestart(restartBtn);
    restartBtn.disabled = true;
    restartBtn.textContent = "Restarting…";
    clearError("engine");
    try {
      await api.post("/api/engine/restart");
      await refresh();
    } catch (err) {
      showError("engine", err.message);
    } finally {
      restartBtn.disabled = false;
      restartBtn.textContent = RESTART_LABEL;
    }
  });

  const timer = setInterval(() => {
    const screen = document.getElementById("screen-engine");
    if (!screen) {
      clearInterval(timer); // element removed: stop polling
      return;
    }
    if (screen.hidden) return; // refresh only while visible
    refresh().catch(err => showError("engine", err.message));
  }, 5000);
}

function show() {
  return refresh();
}

registerScreen("engine", { init, show });
