/* Voice Studio core: API client, shared state, hash routing, renderers.
 * Screen modules (speak/voices/clone/design/languages/engine) register via registerScreen.
 * Never inject API/user text as HTML (R-044): create nodes + textContent. */

export const state = {
  voices: [],
  selectedVoice: null,
  engine: null,
  languages: [], // enabled languages (SPEC §5a), default first, from /api/languages
  supportedLanguages: [], // the engine's supported set, from /api/languages/supported
};

/* Per-page-load Studio session id (M2-T21): takes are scoped to it, so a refresh starts
 * from an empty Takes list. crypto.randomUUID needs a secure context; on plain http the
 * random hex fallback keeps the same `[A-Za-z0-9_-]{1,64}` shape the server accepts. */
export const sessionId =
  (crypto.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`)
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 64);

async function request(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "X-Studio-Session": sessionId, ...(options.headers || {}) },
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.error && body.error.message) message = body.error.message;
    } catch {
      /* non-JSON error body: keep the status text */
    }
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}),
});

export const api = {
  get: path => request(path),
  post: (path, body) => request(path, json("POST", body)),
  patch: (path, body) => request(path, json("PATCH", body)),
  put: (path, body) => request(path, json("PUT", body)),
  del: (path, body) =>
    request(path, body === undefined ? { method: "DELETE" } : json("DELETE", body)),
  postForm: (path, form) => request(path, { method: "POST", body: form }),
};

export async function loadVoices() {
  const data = await api.get("/api/voices");
  state.voices = data.voices || [];
  return state.voices;
}

/* Enabled + supported languages (SPEC §5a): one cached fetch shared by every screen.
 * refreshScreens() drops the cache so the next show() re-reads the server. */
let languagesPromise = null;

export function loadLanguages(force = false) {
  if (force) languagesPromise = null;
  if (!languagesPromise) {
    languagesPromise = Promise.all([
      api.get("/api/languages"),
      api.get("/api/languages/supported"),
    ])
      .then(([enabled, supported]) => {
        state.languages = enabled.languages || [];
        state.supportedLanguages = supported.languages || [];
        return state.languages;
      })
      .catch(err => {
        languagesPromise = null; // a failed fetch must be retried, never cached
        throw err;
      });
  }
  return languagesPromise;
}

/* Enabled languages, default first, as {code,name} options for selects and filters. */
export function languageOptions() {
  return state.languages.map(l => ({ code: l.code, name: l.name }));
}

/* The enabled entry for a code, or null when the app no longer offers it. */
export function languageEntry(code) {
  const key = String(code || "").toLowerCase();
  return state.languages.find(l => l.code === key) || null;
}

/* After a language changes: drop the cache and re-run every screen's show() hook. */
export function refreshScreens() {
  languagesPromise = null;
  for (const [name, handlers] of screens) {
    if (handlers.show) Promise.resolve(handlers.show()).catch(err => showError(name, err.message));
  }
}

/* Header status is text, never colour alone. Unreachable API → reason from the error. */
export async function refreshEngine() {
  const el = document.getElementById("engine-status");
  try {
    const status = await api.get("/api/engine");
    state.engine = status;
    if (!el) return;
    if (status.ready) el.textContent = `Ready · ${status.gpu || status.model}`;
    else if (status.reason) el.textContent = `Not ready · ${status.reason}`;
    else el.textContent = "Warming up…";
  } catch (err) {
    state.engine = null;
    if (el) el.textContent = `Not ready · ${err.message}`;
  }
}

const screens = new Map();
let started = false;
let activeName = null;

export function registerScreen(name, handlers) {
  screens.set(name, handlers);
  if (!started) return;
  runInit(name, handlers);
  // Registered after start() ran (module evaluated late): the initial route()
  // could not show this screen, so show it now when it is the active tab.
  if (name === activeName && handlers.show) {
    Promise.resolve(handlers.show()).catch(err => showError(name, err.message));
  }
}

function runInit(name, { init }) {
  if (init) Promise.resolve(init()).catch(err => showError(name, err.message));
}

function tabs() {
  return Array.from(document.querySelectorAll(".vs-tab"));
}

function route() {
  const wanted = location.hash.slice(1);
  const active = tabs().find(b => b.dataset.tab === wanted) || tabs()[0];
  for (const tab of tabs()) {
    if (tab === active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  const name = active.dataset.tab;
  activeName = name;
  for (const screen of document.querySelectorAll(".vs-screen")) {
    screen.hidden = screen.id !== `screen-${name}`;
  }
  const handlers = screens.get(name);
  if (handlers && handlers.show) {
    Promise.resolve(handlers.show()).catch(err => showError(name, err.message));
  }
}

function start() {
  if (started) return;
  started = true;
  for (const tab of tabs()) {
    tab.addEventListener("click", () => {
      location.hash = tab.dataset.tab;
    });
  }
  for (const button of document.querySelectorAll("[data-goto]")) {
    button.addEventListener("click", () => {
      location.hash = button.dataset.goto;
    });
  }
  addEventListener("hashchange", route);
  for (const [name, handlers] of screens) runInit(name, handlers);
  loadLanguages().catch(() => {
    /* labels fall back to the code until the next screen show() retries */
  });
  refreshEngine();
  setInterval(refreshEngine, 10000);
  route();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start(); // late-evaluated module; registerScreen() shows any sibling that registers after
}

export function selectVoice(name) {
  state.selectedVoice = name;
  try {
    localStorage.setItem("vs.lastVoice", name);
  } catch {
    /* private mode: selection just won't persist */
  }
}

export function fmtDuration(s) {
  return s == null ? "—" : `${Number(s).toFixed(1)} s`;
}

export function fmtClock(unix) {
  return new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/* Smooth mirrored waveform for a 300x24 viewBox — copied exactly from ui/studio.html. */
export function wavePath(amps) {
  const n = amps.length, step = 300 / (n - 1), mid = 12, scale = 11.5;
  const a = amps.map(v => Math.max(0.05, v) * scale);
  let top = `M0,${mid}`;
  for (let i = 0; i < n; i++) {
    const x = i * step, cx = i === 0 ? 0 : (i - 0.5) * step;
    top += ` Q${cx.toFixed(1)},${(mid - a[i]).toFixed(2)} ${x.toFixed(1)},${(mid - a[i]).toFixed(2)}`;
  }
  let bot = '';
  for (let i = n - 1; i >= 0; i--) bot += ` L${(i * step).toFixed(1)},${(mid + a[i]).toFixed(2)}`;
  return top + bot + ' Z';
}

/* Every <path> in the svg gets the same outline: the grey base plus the accent overlay
 * that speak.js clips to the playhead. */
export function renderWave(svg, amps, current = false) {
  const d = wavePath(amps);
  for (const path of svg.querySelectorAll("path")) path.setAttribute("d", d);
  svg.classList.toggle("is-current", !!current);
}

/* Playhead for the accent overlay: fraction 0..1 → clip rect width in the 300-wide viewBox. */
export function setWaveProgress(svg, fraction) {
  const rect = svg.querySelector("clipPath rect");
  if (rect) rect.setAttribute("width", String(300 * Math.min(1, Math.max(0, fraction))));
}

/* One shared Audio element. `onState(phase, el)` (optional) lets a screen mirror the
 * state — phase is "play" | "pause" | "time" | "stop" — so a take row can show pause
 * and animate its waveform without owning the element. */
let audio = null;
let playingUrl = null;
let onState = null;

const emit = (phase, el) => {
  if (onState) onState(phase, el);
};

export function stopAudio() {
  const el = audio;
  const url = playingUrl;
  const cb = onState;
  audio = null;
  playingUrl = null;
  onState = null;
  if (el) el.pause();
  // revoke blob: URLs we created (playWhole / test line) so they cannot leak
  if (url && url.startsWith("blob:")) URL.revokeObjectURL(url);
  if (cb) cb("stop", el);
}

export function play(url, state) {
  if (audio && playingUrl === url) {
    if (audio.paused) {
      audio.play().then(() => emit("play", audio)).catch(() => stopAudio());
    } else {
      audio.pause();
      emit("pause", audio);
    }
    return; // second click on the same source toggles play/pause
  }
  stopAudio();
  audio = new Audio(url);
  playingUrl = url;
  onState = state || null;
  audio.addEventListener("ended", stopAudio);
  audio.addEventListener("error", stopAudio);
  audio.addEventListener("timeupdate", () => emit("time", audio));
  audio.play().then(() => emit("play", audio)).catch(() => stopAudio());
}

/* Shared take/candidate player. A screen hands over its nodes and gets one click-to-toggle
 * player: play/pause icon, `.is-playing` on the row and the waveform playhead. core still owns
 * the single Audio element, so starting another row stops this one and `stop` resets it.
 * Returns { play } so a screen can auto-play without duplicating the state machine. */
const PLAY_D = "M2 1l9 5-9 5z";
const PAUSE_D = "M2 1h3v10H2zM7 1h3v10H7z";
let raf = 0; // one playhead animation at a time (only one clip can play)

export function attachPlayer({ url, row, btn, icon, wave, label = "take" }) {
  let el = null; // our Audio element while it plays, for the rAF playhead
  const paint = () => setWaveProgress(wave, el && el.duration ? el.currentTime / el.duration : 0);
  const sync = (phase, node) => {
    el = phase === "stop" ? null : node || el;
    if (phase !== "time") {
      // timeupdate only moves the playhead: it must not look like the clip stopped (bug report)
      const playing = phase === "play";
      row.classList.toggle("is-playing", playing);
      icon.setAttribute("d", playing ? PAUSE_D : PLAY_D);
      btn.setAttribute("aria-label", `${playing ? "Pause" : "Play"} ${label}`);
      btn.title = playing ? "Pause" : "Play";
      cancelAnimationFrame(raf);
      raf = 0;
      if (playing) {
        const tick = () => {
          paint();
          raf = requestAnimationFrame(tick);
        };
        tick(); // smooth playhead; "time" covers a tab where rAF is throttled
      }
    }
    paint(); // pause lands where the clip stopped, stop resets to 0
  };
  const start = () => play(url, sync);
  btn.addEventListener("click", start);
  sync("stop"); // initial state: triangle, Play label, no highlight, progress 0
  return { play: start };
}

export function showError(screen, message) {
  const el = document.getElementById(`${screen}-error`);
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

export function clearError(screen) {
  const el = document.getElementById(`${screen}-error`);
  if (!el) return;
  el.textContent = "";
  el.hidden = true;
}

/* Language names come from the enabled/supported lists (SPEC §5a); the four seeded
 * names stay as a fallback so a label is never blank before the first fetch. */
const LANGUAGES = { en: "English", ja: "Japanese", ko: "Korean", zh: "Chinese" };

/* The "this language has no test phrases" line: Speak and Design show it instead of chips. */
export function noPhrasesNote(lang) {
  const note = document.createElement("span");
  note.className = "meta";
  note.style.whiteSpace = "normal";
  note.append(`No test phrases for ${formatLang(lang)} yet. `);
  const link = document.createElement("a");
  link.className = "btn-link";
  link.href = "#languages";
  link.textContent = "Add some in Languages";
  note.append(link);
  return note;
}

export function formatLang(code) {
  const key = String(code || "").toLowerCase();
  if (!key) return "";
  const found =
    state.languages.find(l => l.code === key) ||
    state.supportedLanguages.find(l => l.code === key);
  return found ? found.name : LANGUAGES[key] || key.toUpperCase();
}
