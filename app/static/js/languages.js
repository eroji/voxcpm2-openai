/* Languages screen (SPEC §5a, §8 "05 Languages"): the enabled set, the default, removal
 * with a voice decision, and each language's test phrases. All server text goes through
 * textContent (R-044); the API is authoritative for every rule the row shows. */

import {
  api,
  state,
  loadVoices,
  loadLanguages,
  languageOptions,
  registerScreen,
  refreshScreens,
  showError,
  clearError,
} from "./core.js";

const $ = id => document.getElementById(id);
const MAX_PHRASES = 12; // server rule (app/languages.py MAX_PRESETS)
const STANDARD_PHRASES = 6; // the reference set the "N of 6" column counts against

let removing = null; // code whose remove panel is open
let editing = null; // code whose phrase editor is open
let draft = null; // presets being edited: [{id,label,text}]

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function action(label, act) {
  const button = el("button", "btn-link", label);
  button.type = "button";
  button.dataset.act = act;
  return button;
}

const voicesUsing = code =>
  state.voices.filter(v => String(v.language || "").toLowerCase() === code).map(v => v.name);

const countLabel = n => (n === 1 ? "1 voice" : `${n} voices`);

function phraseCell(entry) {
  if (entry.supported === false) return el("span", "lock", "Not supported by the engine");
  if (!entry.presets) return el("span", "meta", "None yet");
  return el("span", null, `${entry.presets} of ${Math.max(STANDARD_PHRASES, entry.presets)}`);
}

function rowActions(entry, locked, lockReason) {
  const acts = el("span", "acts");
  acts.append(action("Edit phrases", "edit"));
  if (!entry.default) acts.append(action("Make default", "default"));
  if (locked) acts.append(el("span", "lock", lockReason));
  else if (removing !== entry.code) acts.append(action("Remove", "remove"));
  return acts;
}

/* Remove with voices (SPEC §8): the row expands with the relabel/delete decision. */
function removePanel(entry, names) {
  const panel = el("div", "vs-lang-remove");
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", `Remove ${entry.name}`);
  if (names.length) {
    const line = el("p");
    line.append(
      el("b", null, `${countLabel(names.length)} use ${entry.name}:`),
      ` ${names.join(", ")}. Choose what happens to them before ${entry.name} is removed.`
    );
    const opts = el("div", "opts");
    const label = el("label", "meta", "Relabel them as");
    label.htmlFor = `move-${entry.code}`;
    const select = el("select", "vs-select");
    select.id = `move-${entry.code}`;
    select.style.cssText = "min-height:28px;padding:4px 8px";
    for (const other of languageOptions()) {
      if (other.code === entry.code) continue;
      const option = el("option", null, other.name);
      option.value = other.code;
      select.append(option);
    }
    const relabel = el("button", "btn-inverse btn-small", "Relabel and remove");
    relabel.type = "button";
    relabel.dataset.act = "relabel";
    relabel.style.fontWeight = "700";
    const drop = el("button", "btn-danger btn-small", "Delete the voices too");
    drop.type = "button";
    drop.dataset.act = "delete";
    opts.append(label, select, relabel, drop, action("Cancel", "cancel"));
    panel.append(
      line,
      opts,
      el(
        "p",
        "meta",
        "Relabelling only changes the tag, filters and test phrases. VoxCPM2 reads the language from the text itself, so the voices sound the same."
      )
    );
  } else {
    const opts = el("div", "opts");
    const drop = el("button", "btn-danger btn-small", "Remove");
    drop.type = "button";
    drop.dataset.act = "drop";
    opts.append(
      el("span", null, `Remove ${entry.name}? No voices use it. Its test phrases are deleted too.`),
      drop,
      action("Cancel", "cancel")
    );
    panel.append(opts);
  }
  return panel;
}

/* Edit phrases: inline rows of label + text with add / remove / reorder (max 12). */
function editorPanel(entry) {
  const panel = el("div", "vs-lang-remove");
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", `Test phrases for ${entry.name}`);
  const rows = el("div", "phrases");
  draft.forEach((preset, index) => {
    const row = el("div", "phrase");
    for (const [field, value, label] of [
      ["label", preset.label, "Label"],
      ["text", preset.text, "Test phrase"],
    ]) {
      const input = el("input", "vs-input");
      input.type = "text";
      input.value = value || "";
      input.setAttribute("aria-label", label);
      input.dataset.field = field;
      input.dataset.index = String(index);
      row.append(input);
    }
    const buttons = el("span", "acts");
    for (const [label, act] of [
      ["↑", "up"],
      ["↓", "down"],
      ["Remove", "drop-phrase"],
    ]) {
      const button = action(label, act);
      button.dataset.index = String(index);
      buttons.append(button);
    }
    row.append(buttons);
    rows.append(row);
  });
  const opts = el("div", "opts");
  const add = action("Add phrase", "add-phrase");
  add.disabled = draft.length >= MAX_PHRASES;
  const save = el("button", "btn-inverse btn-small", "Save");
  save.type = "button";
  save.dataset.act = "save-phrases";
  save.style.fontWeight = "700";
  opts.append(add, save, action("Cancel", "cancel"));
  panel.append(rows, opts);
  return panel;
}

function row(entry) {
  const names = voicesUsing(entry.code);
  const locked = entry.default || state.languages.length === 1;
  const lockReason = entry.default
    ? "Default · make another language default to remove"
    : "Last language";
  const div = el("div", "vs-lang-row" + (removing === entry.code ? " removing" : ""));
  div.dataset.code = entry.code;
  const main = el("div", "vs-lang-main");
  const nm = el("span", "nm");
  nm.append(el("b", null, entry.name));
  if (entry.native && entry.native !== entry.name) nm.append(el("span", "native", entry.native));
  if (entry.default) nm.append(el("span", "tag", "Default"));
  main.append(
    nm,
    el("span", "code", entry.code.toUpperCase()),
    el("span", null, countLabel(entry.voices)),
    phraseCell(entry),
    rowActions(entry, locked, lockReason)
  );
  div.append(main);
  if (removing === entry.code) div.append(removePanel(entry, names));
  if (editing === entry.code) div.append(editorPanel(entry));
  return div;
}

function render() {
  const entries = state.languages;
  $("lang-count").textContent = `${entries.length} of ${state.supportedLanguages.length} enabled`;
  $("lang-rows").replaceChildren(...entries.map(row));
  const enabled = new Set(entries.map(l => l.code));
  const available = state.supportedLanguages.filter(l => !enabled.has(l.code));
  $("add-lang-box").hidden = !available.length;
  $("add-lang").replaceChildren(
    ...available.map(l => {
      const option = el("option", null, l.native && l.native !== l.name ? `${l.name} · ${l.native}` : l.name);
      option.value = l.code;
      return option;
    })
  );
}

/* Any change to the enabled set: re-read the server and re-run every screen's show(). */
async function changed() {
  clearError("languages");
  removing = null;
  editing = null;
  draft = null;
  await loadVoices().catch(() => {});
  refreshScreens();
}

async function openEditor(code) {
  try {
    const data = await api.get(`/api/languages/${encodeURIComponent(code)}/presets`);
    editing = code;
    removing = null;
    draft = (data.presets || []).map(p => ({ ...p }));
    clearError("languages");
    render();
  } catch (err) {
    showError("languages", err.message);
  }
}

async function saveEditor() {
  if (draft.some(p => !p.label.trim() || !p.text.trim())) {
    showError("languages", "Every test phrase needs a label and a phrase.");
    return;
  }
  try {
    await api.put(`/api/languages/${encodeURIComponent(editing)}/presets`, { presets: draft });
    await changed();
  } catch (err) {
    showError("languages", err.message);
  }
}

async function removeLanguage(code, body) {
  try {
    await api.del(`/api/languages/${encodeURIComponent(code)}`, body);
    await changed();
  } catch (err) {
    showError("languages", err.message);
  }
}

function move(index, delta) {
  const to = index + delta;
  if (to < 0 || to >= draft.length) return;
  const [item] = draft.splice(index, 1);
  draft.splice(to, 0, item);
  render();
}

function addPhrase() {
  if (draft.length >= MAX_PHRASES) return;
  const ids = new Set(draft.map(p => p.id));
  let id = "";
  do {
    id = `p${Math.random().toString(36).slice(2, 8)}`;
  } while (ids.has(id));
  draft.push({ id, label: "", text: "" });
  render();
}

async function onRowsClick(event) {
  const button = event.target.closest("button[data-act]");
  const row = event.target.closest(".vs-lang-row");
  if (!button || !row) return;
  const code = row.dataset.code;
  const act = button.dataset.act;
  if (act === "remove") {
    removing = code;
    editing = null;
    render();
  } else if (act === "cancel") {
    removing = null;
    editing = null;
    draft = null;
    clearError("languages");
    render();
  } else if (act === "edit") {
    await openEditor(code);
  } else if (act === "default") {
    try {
      await api.patch(`/api/languages/${encodeURIComponent(code)}`, { default: true });
      await changed();
    } catch (err) {
      showError("languages", err.message);
    }
  } else if (act === "relabel") {
    const to = row.querySelector("select").value;
    await removeLanguage(code, { voices: "relabel", to });
  } else if (act === "delete") {
    await removeLanguage(code, { voices: "delete" });
  } else if (act === "drop") {
    await removeLanguage(code);
  } else if (act === "save-phrases") {
    await saveEditor();
  } else if (act === "add-phrase") {
    addPhrase();
  } else if (act === "up" || act === "down") {
    move(Number(button.dataset.index), act === "up" ? -1 : 1);
  } else if (act === "drop-phrase") {
    draft.splice(Number(button.dataset.index), 1);
    render();
  }
}

registerScreen("languages", {
  init() {
    $("lang-rows").addEventListener("click", onRowsClick);
    $("lang-rows").addEventListener("input", event => {
      const input = event.target.closest("input[data-field]");
      if (!input || !draft) return;
      draft[Number(input.dataset.index)][input.dataset.field] = input.value;
    });
    $("add-lang-btn").addEventListener("click", async () => {
      const code = $("add-lang").value;
      if (!code) return;
      try {
        await api.post("/api/languages", { code });
        await changed();
      } catch (err) {
        showError("languages", err.message);
      }
    });
  },
  async show() {
    removing = null;
    editing = null;
    draft = null;
    clearError("languages");
    await Promise.all([loadLanguages(true), loadVoices().catch(() => {})]);
    render();
  },
});
