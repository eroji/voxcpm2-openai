/* Speak screen: voice chips, generate (stream + non-stream), takes list. */
import {
  api,
  state,
  loadVoices,
  registerScreen,
  selectVoice,
  renderWave,
  play,
  showError,
  clearError,
  fmtDuration,
  fmtClock,
} from "./core.js";

const $ = id => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
let takes = [];

function renderChips() {
  const box = $("voice-chips");
  box.textContent = "";
  for (const v of state.voices) {
    const chip = document.createElement("button");
    chip.className = "vs-chip";
    chip.dataset.key = v.key;
    chip.setAttribute("aria-pressed", String(v.key === state.selectedVoice));
    const lang = document.createElement("span");
    lang.className = "lang";
    lang.textContent = String(v.language || "").toUpperCase();
    chip.append(v.name, " ", lang);
    box.append(chip);
  }
}

async function loadTakes() {
  const data = await api.get("/api/takes");
  takes = data.takes || [];
  renderTakes();
}

function takeStats(t) {
  const parts = [fmtDuration(t.duration_s)];
  if (t.rtf != null) parts.push(`RTF ${Number(t.rtf).toFixed(2)}`);
  if (t.first_audio_ms != null) parts.push(`${Math.round(t.first_audio_ms)} ms`);
  return parts.join(" · ");
}

function renderTakes() {
  const list = $("takes-list");
  list.textContent = ""; // empty takes: no fake rows
  takes.forEach((t, index) => list.append(takeRow(t, index)));
}

/* Q-024: play/download a take in the container it was requested with; disk stays WAV. */
const takeUrl = take => `${take.audio_url}?format=${take.format || "wav"}`;

function takeRow(take, index) {
  const row = document.createElement("article");
  row.className = "vs-take";

  const top = document.createElement("div");
  top.className = "top";
  const who = document.createElement("span");
  who.className = "who";
  const name = document.createElement("b");
  name.textContent = take.voice;
  const stats = document.createElement("span");
  stats.className = "meta";
  stats.textContent = takeStats(take);
  who.append(name, stats);
  const time = document.createElement("span");
  time.className = "meta";
  time.textContent = fmtClock(take.created_at);
  top.append(who, time);

  const p = document.createElement("p");
  p.textContent = take.text; // clamped to 2 lines by .vs-take p CSS

  const player = document.createElement("div");
  player.className = "player";
  const playBtn = document.createElement("button");
  playBtn.className = "btn-play";
  playBtn.setAttribute("aria-label", "Play take");
  const icon = document.createElementNS(SVG_NS, "svg");
  icon.setAttribute("width", "8");
  icon.setAttribute("height", "8");
  icon.setAttribute("viewBox", "0 0 12 12");
  icon.setAttribute("aria-hidden", "true");
  const tri = document.createElementNS(SVG_NS, "path");
  tri.setAttribute("d", "M2 1l9 5-9 5z");
  tri.setAttribute("fill", "currentColor");
  icon.append(tri);
  playBtn.append(icon);
  playBtn.addEventListener("click", () => play(takeUrl(take)));

  const wave = document.createElementNS(SVG_NS, "svg");
  wave.setAttribute("class", "vs-wave");
  wave.setAttribute("viewBox", "0 0 300 24");
  wave.setAttribute("preserveAspectRatio", "none");
  wave.setAttribute("aria-hidden", "true");
  wave.append(document.createElementNS(SVG_NS, "path"));
  if (index === 0) wave.classList.add("is-current");
  api
    .get(take.wave_url)
    .then(w => renderWave(wave, w.amps, index === 0))
    .catch(() => {}); // missing waveform must not break the row

  const links = document.createElement("span");
  links.className = "links";
  const dl = document.createElement("a");
  dl.className = "btn-link";
  dl.href = takeUrl(take);
  dl.download = `${take.id}.${take.format || "wav"}`;
  dl.textContent = "Download";
  const save = document.createElement("button");
  save.className = "btn-link";
  save.textContent = "Save as voice";
  save.addEventListener("click", () => saveAsVoice(take));
  links.append(dl, save);

  player.append(playBtn, wave, links);
  row.append(top, p, player);
  return row;
}

async function saveAsVoice(take) {
  const name = prompt("Voice name");
  if (!name) return;
  try {
    await api.post(`/api/takes/${take.id}/save-as-voice`, { name, language: take.language || "en" });
    await loadVoices();
    renderChips();
  } catch (err) {
    showError("speak", err.message);
  }
}

async function errorFrom(res) {
  try {
    const body = await res.json();
    if (body && body.error && body.error.message) return body.error.message;
  } catch {
    /* non-JSON error body: keep the status text */
  }
  return `HTTP ${res.status}`;
}

/* s16le mono 48 kHz chunks → gapless AudioContext playback, starting on the first chunk.
 * Resolves when the response stream ends — scheduled audio keeps playing after that. */
async function playPcmStream(res) {
  const ctx = new AudioContext({ sampleRate: 48000 });
  const reader = res.body.getReader();
  let when = ctx.currentTime;
  try {
    await ctx.resume();
    let carry = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      let bytes = value;
      if (carry && carry.length) {
        bytes = new Uint8Array(carry.length + value.length);
        bytes.set(carry, 0);
        bytes.set(value, carry.length);
      }
      const usable = bytes.length - (bytes.length % 2); // carry an odd trailing byte
      carry = bytes.slice(usable);
      if (!usable) continue;
      const samples = new Float32Array(usable / 2);
      const view = new DataView(bytes.buffer, bytes.byteOffset, usable);
      for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      const buffer = ctx.createBuffer(1, samples.length, 48000);
      buffer.copyToChannel(samples, 0);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      const start = Math.max(when, ctx.currentTime);
      src.start(start);
      when = start + buffer.duration;
    }
  } finally {
    reader.cancel().catch(() => {}); // no-op once done, releases the body on error
    // close the context after the scheduled tail, never while it still plays
    const tailMs = Math.max(0, when - ctx.currentTime) * 1000 + 50;
    setTimeout(() => ctx.close().catch(() => {}), tailMs);
  }
}

async function playWhole(res) {
  const take = await res.json(); // contract §2: non-stream /api/speak → TakeInfo (Q-024)
  takes.unshift(take);
  renderTakes();
  play(takeUrl(take));
}

/* Streams are PCM at 48 kHz, so Format only applies when streaming is off (Q-024). */
function syncFmt() {
  const streaming = $("stream-toggle").getAttribute("aria-pressed") === "true";
  $("fmt").disabled = streaming;
  $("fmt-hint").hidden = !streaming;
}

async function generate() {
  const button = $("generate");
  if (button.disabled) return;
  clearError("speak");
  button.disabled = true;
  button.textContent = "Generating…";
  const stream = $("stream-toggle").getAttribute("aria-pressed") === "true";
  const body = {
    model: "voxcpm2",
    voice: state.selectedVoice,
    input: $("speak-text").value,
    instructions: $("speak-style").value || undefined,
    cfg: +$("cfg").value,
    steps: +$("steps").value,
    response_format: stream ? "pcm" : $("fmt").value,
    stream,
  };
  if (stream) body.sample_rate = 48000; // playPcmStream decodes 48 kHz: pin the request to it
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      showError("speak", await errorFrom(res));
      return;
    }
    if (stream) {
      await playPcmStream(res);
      await loadTakes();
    } else {
      await playWhole(res);
    }
  } catch (err) {
    showError("speak", err.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate";
  }
}

async function init() {
  syncFmt(); // Format state must be right even if the voices fetch below fails
  await loadVoices();
  let saved = null;
  try {
    saved = localStorage.getItem("vs.lastVoice");
  } catch {
    /* private mode: fall back to the first voice */
  }
  if (!state.voices.some(v => v.key === saved)) saved = state.voices[0] && state.voices[0].key;
  state.selectedVoice = saved || null;
  renderChips();

  $("voice-chips").addEventListener("click", e => {
    const chip = e.target.closest(".vs-chip");
    if (!chip) return;
    for (const c of $("voice-chips").children) c.setAttribute("aria-pressed", String(c === chip));
    selectVoice(chip.dataset.key);
  });

  $("generate").addEventListener("click", generate);
  $("speak-text").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      generate();
    }
  });

  for (const id of ["cfg", "steps"]) {
    const el = $(id);
    const out = $(`${id}-out`);
    el.addEventListener("input", () => {
      out.textContent = id === "cfg" ? (+el.value).toFixed(1) : el.value;
    });
  }

  const toggle = $("stream-toggle");
  toggle.addEventListener("click", () => {
    toggle.setAttribute("aria-pressed", String(toggle.getAttribute("aria-pressed") !== "true"));
    syncFmt();
  });
}

async function show() {
  await loadVoices();
  // a deleted/renamed voice must never be posted: revalidate against the live keys
  if (!state.voices.some(v => v.key === state.selectedVoice)) {
    state.selectedVoice = state.voices[0] ? state.voices[0].key : null;
  }
  renderChips();
  await loadTakes();
}

registerScreen("speak", { init, show });
