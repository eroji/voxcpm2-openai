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
  compareVoices,
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
// Column sort. "language" (the default) groups rows under a header per language.
let sortKey = "language";
let sortDir = 1; // 1 ascending, -1 descending

const SORT_VALUE = {
  name: v => String(v.name).toLowerCase(),
  language: v => String(v.language || "").toLowerCase(),
  source: v => String(v.source || "").toLowerCase(),
  ref: v => Number(v.ref_duration_s) || 0,
  tx: v => String(v.ref_text || "").toLowerCase(),
};

/* Primary column in the chosen direction; ties fall back to language, then name, A–Z. */
function sortVoices(list) {
  const value = SORT_VALUE[sortKey];
  return list.slice().sort((a, b) => {
    const x = value(a);
    const y = value(b);
    const primary = typeof x === "number" ? x - y : x.localeCompare(y);
    return sortDir * primary || compareVoices(a, b);
  });
}

/* aria-sort on the active header; the arrow is drawn from it in CSS. */
function syncSortHeaders() {
  for (const button of document.querySelectorAll("#voices-list .vs-sort")) {
    const th = button.closest("th");
    if (button.dataset.sort !== sortKey) th.removeAttribute("aria-sort");
    else th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
  }
}

function groupRow(code, count) {
  const tr = el("tr", "vs-group");
  const td = el("td");
  td.colSpan = 6;
  td.append(el("b", null, formatLang(code) || "No language"), " ", el("span", "meta", String(count)));
  tr.append(td);
  return tr;
}

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
  const actTd = el("td", "act");
  actTd.append(
    actionButton("Audition", "audition"),
    actionButton("Edit", "edit"),
    actionButton("Delete", "delete"),
  );
  tr.append(
    el("td", "name", v.name),
    languageCell(v),
    sourceCell(v),
    el("td", null, fmtDuration(v.ref_duration_s)),
    transcriptCell(v.ref_text),
    actTd,
  );
  return tr;
}

/* The transcript cell clamps to one line. The cap is on the content, never on the cell: a
 * max-width on the <td> would let one long transcript move that row's columns. */
function transcriptCell(text) {
  const td = el("td", "tx");
  td.append(el("span", null, text || ""));
  return td;
}

function sourceCell(v) {
  const designed = String(v.source || "").toLowerCase() === "designed";
  const td = el("td");
  td.append(el("span", designed ? "src designed" : "src", designed ? "Designed" : "Uploaded"));
  return td;
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
    syncSortHeaders();
    let group = null;
    const sorted = sortVoices(list);
    for (const v of sorted) {
      const code = SORT_VALUE.language(v);
      if (sortKey === "language" && code !== group) {
        group = code;
        tbody.append(groupRow(code, sorted.filter(x => SORT_VALUE.language(x) === code).length));
      }
      tbody.append(voiceRow(v));
    }
  }
}

/* Inline editor: a normal table row — one cell per column, so the header and every other
 * row stay aligned. The transcript cell holds an auto-growing textarea with Auto-transcribe
 * under it; the row grows with the text instead of scrolling. */
function openEditor(tr) {
  const v = findVoice(tr.dataset.key);
  if (!v) return;
  tr.textContent = "";
  tr.classList.add("vs-edit");

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
  const txCell = el("div", "vs-edit-txcell");
  txCell.append(tx, actionButton("Auto-transcribe", "transcribe"));

  const actTd = el("td", "act");
  actTd.append(actionButton("Save", "save"), actionButton("Cancel", "cancel"));
  tr.append(
    cell("name", nameInput),
    cell(null, langSelect),
    sourceCell(v),
    el("td", null, fmtDuration(v.ref_duration_s)),
    cell("tx", txCell),
    actTd,
  );
  grow(tx); // the stored transcript may already need more than one line
  slideOpen(txCell);
  nameInput.focus();
}

/* "Slide down": a cell's content (editor transcript, delete confirm) opens from the one-line
 * row height to its full height. Only that cell grows; every other cell keeps its first line. */
function slideOpen(node) {
  if (!node.animate || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  node.animate([{ height: "28px" }, { height: `${node.scrollHeight}px` }], {
    duration: 180,
    easing: "ease-out",
  });
}

/* Editor cell holding one control; the classes keep the column styling of the normal row. */
function cell(cls, control) {
  const td = el("td", cls);
  td.append(control);
  return td;
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

/* Inline delete confirm, never confirm(): the question takes the actions' line and the
 * rest slides down inside the actions cell, like the editor — no other cell moves. */
function openDelete(tr) {
  const cell = tr.querySelector("td.act");
  if (!cell) return;
  tr.classList.add("vs-confirm");
  const confirm = actionButton("Delete", "confirm");
  const buttons = el("div", "btns");
  buttons.append(confirm, actionButton("Cancel", "cancel"));
  const box = el("div", "vs-confirm-box");
  box.append(
    el("div", "q", `Delete ${tr.dataset.name}? `),
    el("div", "why", "This can't be undone from the UI."),
    buttons,
  );
  cell.replaceChildren(box);
  slideOpen(box);
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
    // a narrower window rewraps an open transcript: keep it fully visible, never clipped
    addEventListener("resize", () => {
      for (const tx of document.querySelectorAll("#voice-rows .vs-edit-tx")) grow(tx);
    });
    document.querySelector("#voices-list thead").addEventListener("click", e => {
      const button = e.target.closest(".vs-sort");
      if (!button) return;
      // same column flips the direction; a new column starts ascending
      sortDir = button.dataset.sort === sortKey ? -sortDir : 1;
      sortKey = button.dataset.sort;
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
