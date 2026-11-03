/* Speak screen: voice chips, generate (stream + non-stream), takes list. */
import {
  api,
  state,
  sessionId,
  loadVoices,
  loadLanguages,
  languageEntry,
  noPhrasesNote,
  registerScreen,
  selectVoice,
  renderWave,
  attachPlayer,
  stopAudio,
  showError,
  clearError,
  formatLang,
  fmtDuration,
  fmtClock,
} from "./core.js";

const $ = id => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
let takes = [];

/* Test-phrase presets (SPEC §5a/§8): chips from /api/languages/{code}/presets in the selected
 * voice's language; switching voice keeps the preset id and swaps in that language's text. */
let presets = [];
let presetId = "question";
let presetSeq = 0; // invalidates an in-flight presets fetch when the voice changes

function voiceLang() {
  const voice = state.voices.find(v => v.key === state.selectedVoice);
  const code = String(voice?.language || "").toLowerCase();
  if (code) return code;
  return (state.languages.find(l => l.default) || state.languages[0] || {}).code || "en";
}

async function syncPresets(swapText) {
  const lang = voiceLang();
  const seq = ++presetSeq;
  $("speak-lang-name").textContent = formatLang(lang);
  let items = [];
  try {
    items = (await api.get(`/api/languages/${encodeURIComponent(lang)}/presets`)).presets || [];
  } catch {
    items = []; // no presets for this language: the hint below says so
  }
  if (seq !== presetSeq) return; // a newer voice selection already won
  presets = items;
  const box = $("speak-presets");
  box.replaceChildren();
  if (!items.length) {
    box.append(noPhrasesNote(lang)); // no chips, and the typed text stays untouched
    return;
  }
  for (const p of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "btn-tag";
    chip.dataset.id = p.id;
    chip.textContent = p.label;
    chip.setAttribute("aria-pressed", String(p.id === presetId));
    box.append(chip);
  }
  const sel = items.find(p => p.id === presetId);
  if (sel && swapText) {
    $("speak-text").value = sel.text;
    $("speak-text").lang = lang;
  }
}

/* Empty-library state (SPEC §8): notice instead of chips, Generate aria-disabled, hint shown. */
function applyEmpty() {
  const empty = !state.voices.length;
  $("voice-chips").hidden = empty;
  $("speak-novoice").hidden = !empty;
  $("gen-hint").hidden = !empty;
  $("generate").setAttribute("aria-disabled", String(empty));
}

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
  applyEmpty();
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
  stopAudio(); // rows are replaced: a detached row must not keep playing unseen
  firstPlayer = null;
  const list = $("takes-list");
  list.textContent = ""; // empty takes: no fake rows
  takes.forEach((t, index) => list.append(takeRow(t, index)));
  $("takes-empty").hidden = takes.length > 0;
}

/* Q-024: play/download a take in the container it was requested with; disk stays WAV. */
const takeUrl = take => `${take.audio_url}?format=${take.format || "wav"}`;

let firstPlayer = null; // newest row, so the auto-play after Generate uses the shared player

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
  const icon = document.createElementNS(SVG_NS, "svg");
  icon.setAttribute("width", "8");
  icon.setAttribute("height", "8");
  icon.setAttribute("viewBox", "0 0 12 12");
  icon.setAttribute("aria-hidden", "true");
  const tri = document.createElementNS(SVG_NS, "path");
  tri.setAttribute("fill", "currentColor");
  icon.append(tri);
  playBtn.append(icon);

  const wave = document.createElementNS(SVG_NS, "svg");
  wave.setAttribute("class", "vs-wave");
  wave.setAttribute("viewBox", "0 0 300 24");
  wave.setAttribute("preserveAspectRatio", "none");
  wave.setAttribute("aria-hidden", "true");
  // grey base + accent overlay clipped to the playhead
  const clipId = `wc-${take.id}`;
  const defs = document.createElementNS(SVG_NS, "defs");
  const clipPath = document.createElementNS(SVG_NS, "clipPath");
  clipPath.setAttribute("id", clipId);
  const clipRect = document.createElementNS(SVG_NS, "rect");
  clipRect.setAttribute("width", "0");
  clipRect.setAttribute("height", "24");
  clipPath.append(clipRect);
  defs.append(clipPath);
  const base = document.createElementNS(SVG_NS, "path");
  base.setAttribute("class", "base");
  const accent = document.createElementNS(SVG_NS, "path");
  accent.setAttribute("class", "accent");
  accent.setAttribute("clip-path", `url(#${clipId})`);
  wave.append(defs, base, accent);
  if (index === 0) wave.classList.add("is-current");
  const rowPlayer = attachPlayer({
    url: takeUrl(take),
    row,
    btn: playBtn,
    icon: tri,
    wave,
    label: "take",
  });
  if (index === 0) firstPlayer = rowPlayer;
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
  links.append(dl);

  player.append(playBtn, wave, links);
  row.append(top, p, player);
  return row;
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

/* s16le mono chunks → gapless AudioContext playback, starting on the first chunk.
 * The rate comes from X-Sample-Rate (M1-T20), never hard-coded.
 * Resolves when the response stream ends — scheduled audio keeps playing after that — with
 * { ctx, start, end } (context times of the first sample and the scheduled tail), or null
 * when no audio arrived. */
async function playPcmStream(res) {
  const rate = Number(res.headers.get("X-Sample-Rate")) || 48000;
  const ctx = new AudioContext({ sampleRate: rate });
  const reader = res.body.getReader();
  let when = ctx.currentTime;
  let first = null;
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
      const buffer = ctx.createBuffer(1, samples.length, rate);
      buffer.copyToChannel(samples, 0);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      const start = Math.max(when, ctx.currentTime);
      if (first == null) first = start;
      src.start(start);
      when = start + buffer.duration;
    }
  } finally {
    reader.cancel().catch(() => {}); // no-op once done, releases the body on error
    // close the context after the scheduled tail, never while it still plays
    const tailMs = Math.max(0, when - ctx.currentTime) * 1000 + 50;
    setTimeout(() => ctx.close().catch(() => {}), tailMs);
  }
  return first == null ? null : { ctx, start: first, end: when };
}

/* The streamed take is still audible when its row appears: the row's shared player takes
 * over at the same offset, so the button, highlight and waveform playhead follow it and
 * pause works like any other take. The AudioContext closes once the element is audible. */
function streamHandoff({ ctx, start, end }) {
  return {
    at() {
      if (ctx.state === "closed" || end - ctx.currentTime < 0.25) return null; // already over
      return Math.max(0, ctx.currentTime - start);
    },
    started() {
      ctx.close().catch(() => {});
    },
  };
}

async function playWhole(res) {
  const take = await res.json(); // contract §2: non-stream /api/speak → TakeInfo (Q-024)
  takes.unshift(take);
  renderTakes();
  if (firstPlayer) firstPlayer.play(); // auto-play the new take through the shared player
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
  if (button.getAttribute("aria-disabled") === "true") {
    // empty library: never call the API, move focus to the notice's first button (SPEC §8)
    $("speak-novoice").querySelector("button").focus();
    return;
  }
  const entry = languageEntry(state.voices.find(v => v.key === state.selectedVoice)?.language);
  if (entry && entry.supported === false) {
    // SPEC §5a rule 5: the engine can't do this language — never send garbage
    showError("speak", "This voice's language isn't supported by the current engine");
    return;
  }
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
      headers: { "Content-Type": "application/json", "X-Studio-Session": sessionId },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      showError("speak", await errorFrom(res));
      return;
    }
    if (stream) {
      const playback = await playPcmStream(res);
      await loadTakes();
      if (playback && firstPlayer) firstPlayer.play(streamHandoff(playback));
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
  await Promise.all([loadVoices(), loadLanguages().catch(() => {})]);
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
    syncPresets(true); // keep the preset id, swap in the new voice's language
  });

  $("speak-presets").addEventListener("click", e => {
    const chip = e.target.closest(".btn-tag");
    if (!chip) return;
    for (const c of $("speak-presets").children) c.setAttribute("aria-pressed", String(c === chip));
    presetId = chip.dataset.id;
    const p = presets.find(x => x.id === presetId);
    if (p) {
      $("speak-text").value = p.text;
      $("speak-text").lang = voiceLang();
    }
  });
  syncPresets(true); // default selection: Question

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
  await Promise.all([loadVoices(), loadLanguages().catch(() => {})]);
  // a deleted/renamed voice must never be posted: revalidate against the live keys
  if (!state.voices.some(v => v.key === state.selectedVoice)) {
    state.selectedVoice = state.voices[0] ? state.voices[0].key : null;
  }
  renderChips();
  // re-render chips after a language/preset change; fill an empty box, never clobber typed text
  await syncPresets(!$("speak-text").value.trim());
  await loadTakes();
}

registerScreen("speak", { init, show });
