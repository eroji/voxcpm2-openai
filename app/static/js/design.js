/* Design screen: description + tags → candidates → select one → save as voice.
 * All server data goes through textContent (R-044). */

import {
  api,
  state,
  loadVoices,
  loadLanguages,
  languageOptions,
  noPhrasesNote,
  attachPlayer,
  stopAudio,
  renderWave,
  showError,
  clearError,
  registerScreen,
} from "./core.js";

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SVG_NS = "http://www.w3.org/2000/svg";

let candidates = [];
let selectedCandidate = null;

/* Sample-line presets (SPEC §5a/§8) from /api/languages/{code}/presets, following the Design
 * language; no preset selected initially, and a language with none shows the hint. */
let presets = [];
let designPresetId = null;

const $ = id => document.getElementById(id);

/* Design defaults to Japanese when it is enabled (the reference order), else the default. */
function designLang() {
  const codes = languageOptions();
  if (!codes.length) return "";
  return codes.some(o => o.code === "ja") ? "ja" : codes[0].code;
}

function fillLangs() {
  const select = $("design-lang");
  const options = languageOptions();
  if (!options.length) return;
  const current = select.value;
  select.replaceChildren(
    ...options.map(option => {
      const node = document.createElement("option");
      node.value = option.code;
      node.textContent = option.name;
      return node;
    })
  );
  select.value = options.some(o => o.code === current) ? current : designLang();
}

async function renderDesignPresets(swapText) {
  const lang = $("design-lang").value || designLang();
  $("design-sample").lang = lang;
  let items = [];
  try {
    items = (await api.get(`/api/languages/${encodeURIComponent(lang)}/presets`)).presets || [];
  } catch {
    items = [];
  }
  presets = items;
  const box = $("design-presets");
  box.replaceChildren();
  if (!items.length) {
    box.append(noPhrasesNote(lang)); // typed text stays exactly as it is
    return;
  }
  for (const p of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "btn-tag";
    chip.dataset.id = p.id;
    chip.textContent = p.label;
    chip.setAttribute("aria-pressed", String(p.id === designPresetId));
    box.append(chip);
  }
  if (swapText && designPresetId) {
    const p = items.find(x => x.id === designPresetId);
    if (p) $("design-sample").value = p.text;
  }
}

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
  const icon = document.createElementNS(SVG_NS, "svg");
  icon.setAttribute("width", "10");
  icon.setAttribute("height", "10");
  icon.setAttribute("viewBox", "0 0 12 12");
  icon.setAttribute("aria-hidden", "true");
  const iconPath = document.createElementNS(SVG_NS, "path");
  iconPath.setAttribute("fill", "currentColor");
  icon.append(iconPath);
  playBtn.append(icon);

  const wave = document.createElementNS(SVG_NS, "svg");
  wave.setAttribute("class", "vs-wave");
  wave.setAttribute("viewBox", "0 0 300 24");
  wave.setAttribute("preserveAspectRatio", "none");
  wave.setAttribute("aria-hidden", "true");
  // grey base + accent overlay clipped to the playhead, exactly like a Speak take
  const clipId = `cd-${c.id}`;
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
  attachPlayer({
    url: c.audio_url,
    row: art,
    btn: playBtn,
    icon: iconPath,
    wave,
    label: `candidate ${c.index}`,
  });

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
  const taken = state.voices.some(v => v.key === name);
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
    stopAudio(); // the rows are replaced: an old candidate must not keep playing unseen
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
    stopAudio();
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
    $("design-lang").addEventListener("change", () => renderDesignPresets(true));
    $("design-presets").addEventListener("click", e => {
      const chip = e.target.closest(".btn-tag");
      if (!chip) return;
      for (const c of $("design-presets").children) c.setAttribute("aria-pressed", String(c === chip));
      designPresetId = chip.dataset.id;
      const p = presets.find(x => x.id === designPresetId);
      if (p) {
        $("design-sample").value = p.text;
        $("design-sample").lang = $("design-lang").value;
      }
    });
    updateSaveState();
  },
  async show() {
    clearError("design");
    await Promise.all([loadVoices().catch(() => {}), loadLanguages().catch(() => {})]);
    fillLangs(); // a removed language falls back to Japanese or the default (SPEC §8)
    await renderDesignPresets(false);
    updateSaveState();
  },
});
