/* Design screen: description + tags → candidates → select one → save as voice.
 * All server data goes through textContent (R-044). */

import {
  api,
  state,
  loadVoices,
  play,
  renderWave,
  showError,
  clearError,
  registerScreen,
} from "./core.js";

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SVG_NS = "http://www.w3.org/2000/svg";

let candidates = [];
let selectedCandidate = null;

const $ = id => document.getElementById(id);

function appendTag(tag) {
  const desc = $("design-desc");
  const parts = desc.value.split(",").map(s => s.trim());
  if (parts.some(p => p.toLowerCase() === tag.toLowerCase())) return;
  const trimmed = desc.value.trim();
  desc.value = trimmed ? `${trimmed}, ${tag}` : tag;
}

function buildCandidate(c) {
  const art = document.createElement("article");
  art.className = "vs-cand";
  art.dataset.id = c.id;

  const rowTop = document.createElement("div");
  rowTop.className = "row";
  rowTop.style.cssText = "justify-content:flex-start;gap:14px";

  const playBtn = document.createElement("button");
  playBtn.className = "btn-play lg";
  playBtn.setAttribute("aria-label", `Play candidate ${c.index}`);
  playBtn.dataset.url = c.audio_url;
  const icon = document.createElementNS(SVG_NS, "svg");
  icon.setAttribute("width", "10");
  icon.setAttribute("height", "10");
  icon.setAttribute("viewBox", "0 0 12 12");
  icon.setAttribute("aria-hidden", "true");
  const iconPath = document.createElementNS(SVG_NS, "path");
  iconPath.setAttribute("d", "M2 1l9 5-9 5z");
  iconPath.setAttribute("fill", "currentColor");
  icon.append(iconPath);
  playBtn.append(icon);

  const wave = document.createElementNS(SVG_NS, "svg");
  wave.setAttribute("class", "vs-wave");
  wave.setAttribute("viewBox", "0 0 300 24");
  wave.setAttribute("preserveAspectRatio", "none");
  wave.setAttribute("aria-hidden", "true");
  wave.append(document.createElementNS(SVG_NS, "path"));

  rowTop.append(playBtn, wave);

  const rowBottom = document.createElement("div");
  rowBottom.className = "row";
  const label = document.createElement("span");
  const name = document.createElement("b");
  name.textContent = `Candidate ${c.index}`;
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `seed ${c.seed}`;
  label.append(name, " ", meta);
  const pick = document.createElement("button");
  pick.className = "pick";
  pick.setAttribute("aria-pressed", "false");
  pick.textContent = "Select";
  rowBottom.append(label, pick);

  art.append(rowTop, rowBottom);
  api
    .get(c.wave_url)
    .then(d => renderWave(wave, d.amps))
    .catch(() => {
      /* a missing waveform must not hide the candidate */
    });
  return art;
}

function onCandidatesClick(e) {
  const playBtn = e.target.closest(".btn-play");
  if (playBtn) {
    play(playBtn.dataset.url);
    return;
  }
  const pick = e.target.closest(".pick");
  if (!pick) return;
  const chosen = pick.closest(".vs-cand");
  for (const c of document.querySelectorAll("#candidates .vs-cand")) {
    const on = c === chosen;
    c.classList.toggle("is-selected", on);
    const p = c.querySelector(".pick");
    p.setAttribute("aria-pressed", String(on));
    p.textContent = on ? "Selected" : "Select";
    c.querySelector(".vs-wave").classList.toggle("is-current", on);
  }
  selectedCandidate = candidates.find(c => c.id === chosen.dataset.id) || null;
  updateSaveState();
}

function updateSaveState() {
  const name = $("design-name").value.trim().toLowerCase();
  const taken = state.voices.some(v => String(v.name).toLowerCase() === name);
  $("save-designed").disabled = !(selectedCandidate && NAME_RE.test(name) && !taken);
}

async function generate() {
  const description = $("design-desc").value.trim();
  const sampleText = $("design-sample").value.trim();
  if (!description || !sampleText) {
    showError("design", "A description and a sample line are both required.");
    return;
  }
  const btn = $("generate-candidates");
  btn.disabled = true;
  btn.textContent = "Generating…";
  clearError("design");
  try {
    const data = await api.post("/api/design/candidates", {
      description,
      sample_text: sampleText,
      language: $("design-lang").value,
      n: +$("design-n").value,
    });
    candidates = data.candidates || [];
    selectedCandidate = null;
    $("candidates").replaceChildren(...candidates.map(buildCandidate));
    updateSaveState();
  } catch (err) {
    showError("design", err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate candidates";
  }
}

async function save() {
  if (!selectedCandidate) return;
  const btn = $("save-designed");
  btn.disabled = true;
  clearError("design");
  try {
    await api.post("/api/design/save", {
      candidate_id: selectedCandidate.id,
      name: $("design-name").value.trim().toLowerCase(),
      language: $("design-lang").value,
    });
    await loadVoices();
    candidates = [];
    selectedCandidate = null;
    $("candidates").replaceChildren();
    $("design-name").value = "";
    btn.textContent = "Saved ✓";
    setTimeout(() => {
      btn.textContent = "Save as voice";
      updateSaveState();
    }, 1500);
  } catch (err) {
    showError("design", err.message); // 409 duplicate name arrives here
    updateSaveState();
  }
}

registerScreen("design", {
  init() {
    for (const tag of document.querySelectorAll("#design-tags button")) {
      tag.addEventListener("click", () => appendTag(tag.textContent.replace(/^\+\s*/, "")));
    }
    $("generate-candidates").addEventListener("click", generate);
    $("save-designed").addEventListener("click", save);
    $("design-name").addEventListener("input", updateSaveState);
    $("candidates").addEventListener("click", onCandidatesClick);
    updateSaveState();
  },
  show() {
    clearError("design");
    updateSaveState();
  },
});
