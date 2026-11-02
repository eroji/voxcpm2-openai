/* Voice Studio core: API client, shared state, hash routing, renderers.
 * Screen modules (speak/voices/clone/design/engine) register via registerScreen.
 * Never inject API/user text as HTML (R-044): create nodes + textContent. */

export const state = { voices: [], selectedVoice: null, engine: null };

async function request(path, options = {}) {
  const res = await fetch(path, options);
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
  del: path => request(path, { method: "DELETE" }),
  postForm: (path, form) => request(path, { method: "POST", body: form }),
};

export async function loadVoices() {
  const data = await api.get("/api/voices");
  state.voices = data.voices || [];
  return state.voices;
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

export function renderWave(svg, amps, current = false) {
  const path = svg.querySelector("path");
  if (path) path.setAttribute("d", wavePath(amps));
  svg.classList.toggle("is-current", !!current);
}

let audio = null;
let playingUrl = null;

export function stopAudio() {
  if (audio) audio.pause();
  audio = null;
  playingUrl = null;
}

export function play(url) {
  if (audio && playingUrl === url) {
    stopAudio(); // second click on the same source toggles off
    return;
  }
  stopAudio();
  audio = new Audio(url);
  playingUrl = url;
  audio.addEventListener("ended", stopAudio);
  audio.play().catch(() => stopAudio());
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

const LANGUAGES = { en: "English", ja: "Japanese", ko: "Korean", zh: "Chinese" };

export function formatLang(code) {
  const key = String(code || "").toLowerCase();
  return LANGUAGES[key] || code || "";
}
