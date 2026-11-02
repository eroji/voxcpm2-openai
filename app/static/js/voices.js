/* Voices screen: search + language filters, audition, inline edit, inline delete.
 * All API data goes through textContent; URLs use encodeURIComponent (R-044). */

import {
  api,
  state,
  loadVoices,
  play,
  showError,
  clearError,
  formatLang,
  fmtDuration,
  registerScreen,
} from "./core.js";

const LANGS = ["en", "ja", "ko", "zh"];
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/; // same rule the server validates (CONTRACTS §4)

let query = "";
let lang = "all";

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
    el("td", null, formatLang(v.language)),
    srcTd,
    el("td", null, fmtDuration(v.ref_duration_s)),
    el("td", "tx", v.ref_text || ""),
    actTd,
  );
  return tr;
}

function emptyRow() {
  const td = el("td");
  td.colSpan = 6;
  const box = el(
    "div",
    "vs-empty",
    "No voices yet. Clone one from a recording or design one from a description.",
  );
  for (const [label, target] of [
    ["Clone from a recording", "clone"],
    ["Design from a description", "design"],
  ]) {
    const b = el("button", "btn-secondary", label);
    b.addEventListener("click", () => {
      location.hash = target;
    });
    box.append(b);
  }
  td.append(box);
  const tr = document.createElement("tr");
  tr.append(td);
  return tr;
}

function render() {
  const tbody = document.getElementById("voice-rows");
  tbody.textContent = "";
  const list = visibleVoices();
  document.getElementById("voices-count").textContent = String(list.length);
  if (!state.voices.length) {
    tbody.append(emptyRow());
    return;
  }
  for (const v of list) tbody.append(voiceRow(v));
}

/* Inline editor: one cell with name / language / transcript inputs + Save/Cancel. */
function openEditor(tr) {
  const v = findVoice(tr.dataset.key);
  if (!v) return;
  tr.textContent = "";
  const td = el("td");
  td.colSpan = 6;
  td.style.display = "flex";
  td.style.gap = "8px";
  td.style.flexWrap = "wrap";

  const nameInput = el("input", "vs-input");
  nameInput.type = "text";
  nameInput.value = v.name;
  nameInput.setAttribute("aria-label", "Name");
  nameInput.dataset.field = "name";

  const langSelect = el("select", "vs-select");
  langSelect.setAttribute("aria-label", "Language");
  langSelect.dataset.field = "language";
  for (const code of LANGS) {
    const option = el("option", null, formatLang(code));
    option.value = code;
    langSelect.append(option);
  }
  langSelect.value = String(v.language || "en").toLowerCase();

  const txInput = el("input", "vs-input");
  txInput.type = "text";
  txInput.value = v.ref_text || "";
  txInput.setAttribute("aria-label", "Transcript");
  txInput.dataset.field = "ref_text";

  td.append(nameInput, langSelect, txInput, actionButton("Save", "save"), actionButton("Cancel", "cancel"));
  tr.append(td);
  nameInput.focus();
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

async function saveEditor(tr) {
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
  try {
    if (Object.keys(body).length) {
      await api.patch(`/api/voices/${encodeURIComponent(v.key)}`, body);
    }
    clearError("voices");
    await loadVoices();
    render();
  } catch (err) {
    showError("voices", err.message); // 409 etc. stays inline, editor stays open
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
        saveEditor(tr);
      } else if (act === "confirm") {
        confirmDelete(tr);
      } else if (act === "cancel") {
        clearError("voices");
        render();
      }
    });
  },
  async show() {
    await loadVoices();
    render();
  },
});
