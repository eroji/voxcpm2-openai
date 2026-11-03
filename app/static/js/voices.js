/* Voices screen: search + language filters, audition, inline edit, inline delete.
 * All API data goes through textContent; URLs use encodeURIComponent (R-044). */

import {
  api,
  state,
  sessionId,
  loadVoices,
  loadLanguages,
  languageOptions,
  languageEntry,
  play,
  showError,
  clearError,
  formatLang,
  fmtDuration,
  registerScreen,
} from "./core.js";

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/; // same rule the server validates (CONTRACTS §4)

let query = "";
let lang = "all";
let busy = false; // one editor round trip at a time (save or transcribe)

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function actionButton(label, act) {
  const b = el("button", "btn-link", label);
  b.dataset.act = act;
  return b;
}

function findVoice(key) {
  return state.voices.find(v => v.key === key);
}

function visibleVoices() {
  return state.voices.filter(v => {
    const hay = `${v.name} ${v.ref_text || ""}`.toLowerCase();
    const vLang = String(v.language || "").toLowerCase();
    return hay.includes(query) && (lang === "all" || vLang === lang);
  });
}

function languageCell(v) {
  const entry = languageEntry(v.language);
  // SPEC §5a rule 5: the engine lost this language — say so in the row, in plain text
  if (entry && entry.supported === false) return el("td", "tx", "Not supported by the engine");
  return el("td", null, formatLang(v.language));
}

function voiceRow(v) {
  const tr = document.createElement("tr");
  tr.dataset.key = v.key; // API path
  tr.dataset.name = v.name; // display only
  const designed = String(v.source || "").toLowerCase() === "designed";
  const srcTd = el("td");
  srcTd.append(el("span", designed ? "src designed" : "src", designed ? "Designed" : "Uploaded"));
  const actTd = el("td", "act");
  actTd.append(
    actionButton("Audition", "audition"),
    actionButton("Edit", "edit"),
    actionButton("Delete", "delete"),
  );
  tr.append(
    el("td", "name", v.name),
    languageCell(v),
    srcTd,
    el("td", null, fmtDuration(v.ref_duration_s)),
    el("td", "tx", v.ref_text || ""),
    actTd,
  );
  return tr;
}

/* Language filters: All first, then the enabled languages, default first (SPEC §5a). */
function renderFilters() {
  const box = document.getElementById("lang-filters");
  const all = box.querySelector('[data-lang="all"]');
  const buttons = languageOptions().map(option => {
    const button = el("button", "vs-filter", option.name);
    button.dataset.lang = option.code;
    button.setAttribute("aria-pressed", String(option.code === lang));
    return button;
  });
  all.setAttribute("aria-pressed", String(lang === "all"));
  box.replaceChildren(all, ...buttons);
}

function render() {
  renderFilters();
  const tbody = document.getElementById("voice-rows");
  tbody.textContent = "";
  const list = visibleVoices();
  // the header count is the library total (SPEC §8); filters only change the rows
  document.getElementById("voices-count").textContent = String(state.voices.length);
  // empty library: one empty state, never both sets of buttons (SPEC §8)
  const empty = !state.voices.length;
  for (const id of ["voices-actions", "voices-list", "voices-count"]) {
    document.getElementById(id).hidden = empty;
  }
  document.getElementById("voices-empty").hidden = !empty;
  if (empty) return;
  if (!list.length) {
    const td = el("td");
    td.colSpan = 6;
    td.append(el("div", "vs-empty", "No voices match the current search or filter."));
    const tr = document.createElement("tr");
    tr.append(td);
    tbody.append(tr);
  } else {
    for (const v of list) tbody.append(voiceRow(v));
  }
}

/* Full-width inline editor: one row, one <td colspan="6"> — name, language and an
 * auto-growing transcript with an Auto-transcribe button beside it. */
function openEditor(tr) {
  const v = findVoice(tr.dataset.key);
  if (!v) return;
  tr.textContent = "";
  const td = el("td", "vs-edit");
  td.colSpan = 6;

  const nameInput = el("input", "vs-input");
  nameInput.type = "text";
  nameInput.value = v.name;
  nameInput.setAttribute("aria-label", "Name");
  nameInput.dataset.field = "name";

  const langSelect = el("select", "vs-select");
  langSelect.setAttribute("aria-label", "Language");
  langSelect.dataset.field = "language";
  const own = String(v.language || "en").toLowerCase();
  // enabled languages, plus this voice's own code so editing never loses it (SPEC §5a rule 4)
  const codes = languageOptions();
  if (!codes.some(o => o.code === own)) codes.push({ code: own, name: formatLang(own) });
  for (const option of codes) {
    const node = el("option", null, option.name);
    node.value = option.code;
    langSelect.append(node);
  }
  langSelect.value = own;

  const tx = el("textarea", "vs-edit-tx");
  tx.value = v.ref_text || "";
  tx.rows = 1;
  tx.setAttribute("aria-label", "Transcript");
  tx.dataset.field = "ref_text";
  tx.addEventListener("input", () => grow(tx));
  const transcribe = actionButton("Auto-transcribe", "transcribe");

  const grid = el("div", "vs-edit-grid");
  grid.append(field("Name", nameInput), field("Language", langSelect), field("Transcript", tx, transcribe));
  const acts = el("div", "vs-edit-acts");
  acts.append(actionButton("Save", "save"), actionButton("Cancel", "cancel"));
  td.append(grid, acts);
  tr.append(td);
  grow(tx); // the stored transcript may already need more than one line
  nameInput.focus();
}

/* Labelled cell of the editor grid. */
function field(label, control, extra) {
  const box = el("div", "vs-edit-field");
  box.append(el("label", null, label));
  const ctl = el("div", "vs-edit-ctl");
  ctl.append(control);
  if (extra) ctl.append(extra);
  box.append(ctl);
  return box;
}

/* Auto-grow: never a scrollbar inside the transcript, the row expands instead. */
function grow(tx) {
  tx.style.height = "auto";
  tx.style.height = `${tx.scrollHeight}px`;
}

/* Transcribe the stored reference audio into the editor's transcript box. */
async function autoTranscribe(tr, button) {
  if (busy) return;
  busy = true;
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Transcribing…";
  try {
    const res = await fetch(`/api/voices/${encodeURIComponent(tr.dataset.key)}/audio`, {
      headers: { "X-Studio-Session": sessionId },
    });
    if (!res.ok) throw new Error(await errorFrom(res));
    const form = new FormData();
    form.append("file", await res.blob(), `${tr.dataset.key}.wav`);
    const data = await api.postForm("/api/transcribe", form);
    const tx = tr.querySelector('[data-field="ref_text"]');
    tx.value = data.text || "";
    grow(tx);
    clearError("voices");
  } catch (err) {
    showError("voices", err.message); // 503 "transcription is not configured" stays inline
  } finally {
    button.disabled = false;
    button.textContent = label;
    busy = false;
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

/* Inline delete confirm: replaces the actions cell, never uses confirm(). */
function openDelete(tr) {
  const cell = tr.querySelector("td.act");
  if (!cell) return;
  cell.textContent = "";
  cell.append(document.createTextNode(`Delete ${tr.dataset.name}? This can't be undone from the UI. `));
  const confirm = actionButton("Delete", "confirm");
  cell.append(confirm, actionButton("Cancel", "cancel"));
  confirm.focus();
}

async function saveEditor(tr, button) {
  if (busy) return;
  const v = findVoice(tr.dataset.key);
  if (!v) return;
  const body = {};
  const newName = tr.querySelector('[data-field="name"]').value.trim();
  const language = tr.querySelector('[data-field="language"]').value;
  const refText = tr.querySelector('[data-field="ref_text"]').value;
  if (newName && newName !== v.name) {
    if (!NAME_RE.test(newName.toLowerCase())) {
      showError("voices", "Names must match [a-z0-9][a-z0-9_-]{0,63}: 1–64 lowercase letters, digits, _ or -, starting with a letter or digit.");
      return; // editor stays open with the reason inline
    }
    body.name = newName;
  }
  if (language !== String(v.language || "").toLowerCase()) body.language = language;
  if (refText !== (v.ref_text || "")) body.ref_text = refText;
  busy = true;
  if (button) button.disabled = true;
  try {
    if (Object.keys(body).length) {
      await api.patch(`/api/voices/${encodeURIComponent(v.key)}`, body);
    }
    clearError("voices");
    await loadVoices();
    render(); // the row comes back as a normal row: transcript on one line
  } catch (err) {
    showError("voices", err.message); // 409 etc. stays inline, editor stays open
    if (button) button.disabled = false;
  } finally {
    busy = false;
  }
}

async function confirmDelete(tr) {
  try {
    await api.del(`/api/voices/${encodeURIComponent(tr.dataset.key)}`);
    clearError("voices");
    await loadVoices();
    render();
  } catch (err) {
    showError("voices", err.message);
  }
}

registerScreen("voices", {
  init() {
    document.getElementById("voice-search").addEventListener("input", e => {
      query = e.target.value.toLowerCase();
      render();
    });
    document.getElementById("lang-filters").addEventListener("click", e => {
      const button = e.target.closest("button[data-lang]");
      if (!button) return;
      lang = button.dataset.lang;
      for (const b of button.parentElement.querySelectorAll(".vs-filter")) {
        b.setAttribute("aria-pressed", String(b === button));
      }
      render();
    });
    document.getElementById("voice-rows").addEventListener("click", e => {
      const button = e.target.closest("button[data-act]");
      const tr = e.target.closest("tr[data-name]");
      if (!button || !tr) return;
      const act = button.dataset.act;
      if (act === "audition") {
        play(`/api/voices/${encodeURIComponent(tr.dataset.key)}/audio`);
      } else if (act === "edit") {
        openEditor(tr);
      } else if (act === "delete") {
        openDelete(tr);
      } else if (act === "save") {
        saveEditor(tr, button);
      } else if (act === "transcribe") {
        autoTranscribe(tr, button);
      } else if (act === "confirm") {
        confirmDelete(tr);
      } else if (act === "cancel") {
        clearError("voices");
        render();
      }
    });
  },
  async show() {
    await Promise.all([loadVoices(), loadLanguages().catch(() => {})]);
    // a removed language must not leave the filter pointing at nothing (SPEC §8)
    if (lang !== "all" && !state.languages.some(l => l.code === lang)) lang = "all";
    render();
  },
});
