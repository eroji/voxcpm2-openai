/* Pronunciation screen: respelling rules (edit, filter, save/revert), a test box, and JSON
 * import (merge or replace, previewed by a dry run) / export. Adapted from qwen3-tts-openai.
 * All rule text goes through value/textContent (R-044). */

import { api, showError, clearError, registerScreen } from "./core.js";

const $ = id => document.getElementById(id);

let saved = []; // rules as stored on the server
let draft = []; // rules being edited
let fileRules = null; // parsed JSON of the chosen import file

const clone = rules => rules.map(r => ({ ...r }));
const canon = rules =>
  JSON.stringify(rules.map(r => [r.match, r.replace, !!r.whole_word, !!r.after_number, !!r.case_sensitive]));
const dirty = () => canon(draft) !== canon(saved);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function textInput(value, placeholder, label, onInput) {
  const input = el("input", "vs-input");
  input.type = "text";
  input.value = value;
  input.placeholder = placeholder;
  input.setAttribute("aria-label", label);
  input.addEventListener("input", () => onInput(input.value));
  return input;
}

function flag(checked, label, onChange, disabled = false) {
  const td = el("td", "c");
  const box = el("input");
  box.type = "checkbox";
  box.checked = checked;
  box.disabled = disabled;
  box.setAttribute("aria-label", label);
  box.addEventListener("change", () => onChange(box.checked));
  td.append(box);
  return td;
}

function ruleRow(rule, index) {
  const n = ` rule ${index + 1}`;
  const tr = el("tr");
  tr.dataset.text = `${rule.match} ${rule.replace}`.toLowerCase();
  const matchTd = el("td");
  matchTd.append(textInput(rule.match, "e.g. nginx", `Match,${n}`, v => edit(() => (rule.match = v))));
  const sayTd = el("td");
  sayTd.append(textInput(rule.replace, "e.g. engine X", `Say,${n}`, v => edit(() => (rule.replace = v))));
  const del = el("button", "btn-link", "Remove");
  del.setAttribute("aria-label", `Remove${n}`);
  del.addEventListener("click", () => {
    draft.splice(index, 1);
    renderRules();
  });
  const delTd = el("td", "act");
  delTd.append(del);
  tr.append(
    matchTd,
    sayTd,
    // units always end at a word boundary, so Whole word does not apply to them
    flag(
      rule.whole_word && !rule.after_number,
      `Whole word,${n}`,
      v => edit(() => (rule.whole_word = v)),
      rule.after_number,
    ),
    flag(rule.after_number, `After a number,${n}`, v => {
      rule.after_number = v;
      renderRules();
    }),
    flag(rule.case_sensitive, `Case-sensitive,${n}`, v => edit(() => (rule.case_sensitive = v))),
    delTd,
  );
  return tr;
}

/* A field edit: update the draft, keep the row (and focus) in place, refresh the state. */
function edit(change) {
  change();
  syncState();
}

function renderRules() {
  $("pron-rows").replaceChildren(...draft.map(ruleRow));
  $("pron-empty").hidden = draft.length > 0;
  applyFilter();
  syncState();
}

function applyFilter() {
  const q = $("pron-filter").value.trim().toLowerCase();
  const rows = [...$("pron-rows").children];
  let shown = 0;
  for (const tr of rows) {
    tr.hidden = !!q && !tr.dataset.text.includes(q);
    if (!tr.hidden) shown += 1;
  }
  $("pron-filtered").textContent = q ? `Showing ${shown} of ${rows.length}` : "";
}

function syncState() {
  const d = dirty();
  $("pron-count").textContent = String(draft.length);
  $("pron-dirty").hidden = !d;
  $("pron-save").disabled = !d;
  $("pron-revert").disabled = !d;
  $("pron-test-note").hidden = !d;
}

function status(message) {
  const node = $("pron-status");
  node.textContent = message;
  node.hidden = !message;
}

async function load() {
  saved = (await api.get("/api/pronunciation")).rules || [];
  draft = clone(saved);
  renderRules();
}

async function save() {
  const rules = draft.filter(r => r.match.trim() || r.replace.trim()); // drop blank rows
  if (rules.some(r => !r.match)) {
    showError("pronunciation", "Every rule needs something to match.");
    return;
  }
  $("pron-save").disabled = true;
  try {
    saved = (await api.put("/api/pronunciation", { rules })).rules || [];
    draft = clone(saved);
    clearError("pronunciation");
    status("Rules saved. They apply to new requests in every language.");
    renderRules();
  } catch (err) {
    showError("pronunciation", err.message);
    syncState();
  }
}

function addRule() {
  draft.push({ match: "", replace: "", whole_word: true, case_sensitive: false, after_number: false });
  $("pron-filter").value = "";
  renderRules();
  $("pron-rows").lastElementChild?.querySelector("input")?.focus();
}

async function rewrite() {
  try {
    const r = await api.post("/api/pronunciation/test", { text: $("pron-test-text").value });
    $("pron-test-out").textContent = `→ ${r.text}`;
    clearError("pronunciation");
  } catch (err) {
    showError("pronunciation", err.message);
  }
}

const importMode = () => document.querySelector('input[name="pron-mode"]:checked')?.value || "merge";

function describe(summary, dry) {
  if (importMode() === "replace") {
    return `${dry ? "Replaces" : "Replaced"} ${plural(summary.removed, "rule")} with the file's ${summary.in_file}.`;
  }
  return `${plural(summary.in_file, "rule")} in the file: ${summary.added} new, ${summary.updated} changed → ${plural(summary.total, "rule")}.`;
}

/* Dry run: show what the import would change before anything is saved. */
async function previewImport() {
  const go = $("pron-import");
  go.disabled = true;
  try {
    const r = await api.post("/api/pronunciation/import", { mode: importMode(), rules: fileRules, dry_run: true });
    let text = describe(r.summary, true);
    if (dirty()) text += " Importing saves the rules and discards your unsaved edits.";
    $("pron-preview").textContent = text;
    clearError("pronunciation");
    go.disabled = false;
  } catch (err) {
    $("pron-preview").textContent = "";
    showError("pronunciation", err.message);
  }
}

async function chooseFile() {
  const input = $("pron-file");
  const file = input.files[0];
  input.value = ""; // choosing the same file again still fires change
  if (!file) return;
  $("pron-file-name").textContent = file.name;
  $("pron-preview").textContent = "";
  $("pron-import").disabled = true;
  fileRules = null;
  try {
    fileRules = JSON.parse(await file.text());
  } catch (err) {
    showError("pronunciation", `${file.name} is not valid JSON: ${err.message}`);
    return;
  }
  previewImport();
}

async function runImport() {
  const go = $("pron-import");
  go.disabled = true;
  try {
    const r = await api.post("/api/pronunciation/import", { mode: importMode(), rules: fileRules });
    saved = r.rules || [];
    draft = clone(saved);
    fileRules = null;
    $("pron-file-name").textContent = "";
    $("pron-preview").textContent = "";
    clearError("pronunciation");
    status(`Rules imported. ${describe(r.summary, false)}`);
    renderRules();
  } catch (err) {
    showError("pronunciation", err.message);
    go.disabled = false;
  }
}

async function exportRules() {
  try {
    const data = await api.get("/api/pronunciation/export");
    const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: "application/json" });
    const link = el("a");
    link.href = URL.createObjectURL(blob);
    link.download = "tts_pronunciation.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (err) {
    showError("pronunciation", err.message);
  }
}

registerScreen("pronunciation", {
  init() {
    $("pron-save").addEventListener("click", save);
    $("pron-revert").addEventListener("click", () => {
      draft = clone(saved);
      status("");
      renderRules();
    });
    $("pron-add").addEventListener("click", addRule);
    $("pron-filter").addEventListener("input", applyFilter);
    $("pron-test-btn").addEventListener("click", rewrite);
    $("pron-file").addEventListener("change", chooseFile);
    for (const radio of document.querySelectorAll('input[name="pron-mode"]')) {
      radio.addEventListener("change", () => fileRules && previewImport());
    }
    $("pron-import").addEventListener("click", runImport);
    $("pron-export").addEventListener("click", exportRules);
  },
  async show() {
    if (dirty()) return; // coming back to the tab keeps unsaved edits
    clearError("pronunciation");
    await load();
  },
});
