/* Clone screen: reference-audio drop/upload, auto-transcribe, save, ad-hoc test line.
 * The server is authoritative for validation (duration, name, consent); the client
 * only pre-checks so the user gets inline feedback before a round trip. */

import {
  api,
  state,
  loadVoices,
  play,
  showError,
  clearError,
  registerScreen,
  fmtDuration,
} from "./core.js";

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const MIN_S = 3;
const MAX_S = 30; // mirrors the server rule (CONTRACTS §6)

const SAMPLE = {
  en: "This is a test of the cloned voice.",
  ja: "これはクローンした声のテストです。",
  ko: "이것은 복제한 음성의 테스트입니다.",
  zh: "这是克隆声音的测试。",
};

const $ = id => document.getElementById(id);
let file = null; // File picked for reference audio, or null
let saving = false; // in-flight Save: blocks double-submit

/* Read duration from an audio file's metadata. Rejects non-finite durations. */
function probeDuration(f) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(f);
    const audio = new Audio();
    const fail = message => {
      URL.revokeObjectURL(url);
      reject(new Error(message));
    };
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      if (Number.isFinite(audio.duration)) resolve(audio.duration);
      else fail("Could not read the clip duration.");
    };
    audio.onerror = () => fail("Could not read the audio file.");
    audio.src = url;
  });
}

function metaText() {
  return $("drop-zone").querySelector(".meta");
}

function nameProblem() {
  const name = $("clone-name").value;
  if (!name) return "Choose a name for the voice.";
  if (!NAME_RE.test(name))
    return "Name must be 1–64 characters: lowercase letters, digits, _ or -, starting with a letter or digit.";
  if (state.voices.some(v => v.key === name))
    return `A voice named “${name}” already exists.`;
  return null;
}

function updateSave() {
  $("save-voice").disabled = saving || !(file && !nameProblem() && $("consent").checked);
}

function setStatus(message) {
  const el = $("clone-status");
  el.textContent = message;
  el.hidden = !message;
}

function onFile(f) {
  clearError("clone");
  setStatus("");
  file = null;
  if (!f) return;
  probeDuration(f)
    .then(seconds => {
      if (seconds < MIN_S || seconds > MAX_S) {
        showError(
          "clone",
          `Clip is ${Number(seconds).toFixed(1)} s — reference audio must be ${MIN_S}–${MAX_S} seconds.`
        );
        updateSave(); // file was rejected: keep the button state honest
        return;
      }
      file = f;
      metaText().textContent = `${f.name} · ${fmtDuration(seconds)}`;
      updateSave();
    })
    .catch(err => {
      showError("clone", err.message);
      updateSave();
    });
}

async function onTranscribe() {
  if (!file) {
    showError("clone", "Choose a reference audio file first.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const data = await api.postForm("/api/transcribe", form);
    $("ref-text").value = data.text || "";
    clearError("clone");
    setStatus("");
  } catch (err) {
    showError("clone", err.message); // e.g. 503 "transcription is not configured"
  }
}

async function onSave() {
  if (saving) return;
  const problem = nameProblem();
  if (problem || !file || !$("consent").checked) {
    showError("clone", problem || "Choose a reference audio file first.");
    return;
  }
  clearError("clone");
  setStatus("");
  const form = new FormData();
  form.append("file", file);
  form.append("name", $("clone-name").value);
  form.append("language", $("clone-lang").value);
  form.append("ref_text", $("ref-text").value);
  form.append("consent", "true");
  saving = true;
  updateSave();
  try {
    await api.postForm("/api/voices/clone", form);
    file = null;
    $("ref-file").value = "";
    $("ref-text").value = "";
    $("clone-name").value = "";
    $("consent").checked = false;
    metaText().textContent = "3–30 seconds of clean, single-speaker speech";
    await loadVoices();
    setStatus("Voice saved."); // neutral status row; no forced navigation
  } catch (err) {
    showError("clone", err.message);
  } finally {
    saving = false;
    updateSave();
  }
}

/* Ad-hoc clone via /v1/audio/speech with ref_audio data URI (Q-009). */
async function onTest() {
  if (!file) {
    showError("clone", "Choose a reference audio file first.");
    return;
  }
  clearError("clone");
  setStatus("");
  try {
    const refAudio = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read the file."));
      reader.readAsDataURL(file);
    });
    const res = await fetch("/v1/audio/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "voxcpm2",
        voice: $("clone-name").value || "ad-hoc",
        input: SAMPLE[$("clone-lang").value] || SAMPLE.en,
        response_format: "wav",
        ref_audio: refAudio,
        ref_text: $("ref-text").value,
        stream: false,
      }),
    });
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error && body.error.message) message = body.error.message;
      } catch {
        /* non-JSON error body */
      }
      throw new Error(message);
    }
    play(URL.createObjectURL(await res.blob()));
  } catch (err) {
    showError("clone", err.message);
  }
}

registerScreen("clone", {
  init() {
    // #drop-zone is a <label for="ref-file">, so click already opens the picker.
    const zone = $("drop-zone");
    zone.addEventListener("dragover", e => {
      e.preventDefault();
      zone.classList.add("is-over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("is-over");
      const dropped = e.dataTransfer && e.dataTransfer.files[0];
      if (!dropped) return;
      const transfer = new DataTransfer();
      transfer.items.add(dropped);
      $("ref-file").files = transfer.files; // keep the input in sync with the form
      onFile(dropped);
    });
    $("ref-file").addEventListener("change", e => onFile(e.target.files[0]));
    $("auto-transcribe").addEventListener("click", onTranscribe);
    $("clone-name").addEventListener("input", e => {
      const lowered = e.target.value.toLowerCase();
      if (lowered !== e.target.value) e.target.value = lowered;
      clearError("clone");
      const problem = nameProblem();
      if (problem && lowered) showError("clone", problem);
      updateSave();
    });
    $("consent").addEventListener("change", updateSave);
    $("save-voice").addEventListener("click", onSave);
    $("test-line").addEventListener("click", onTest);
    updateSave();
  },
  async show() {
    await loadVoices(); // fresh names for duplicate validation
    // SPEC §8/§10: auto-transcribe needs STT; the test line calls /v1, which needs an API key (Q-023)
    $("auto-transcribe").disabled = !state.engine?.stt;
    const gated = !!state.engine?.api_keys_required;
    $("test-line").disabled = gated;
    $("test-line").title = gated
      ? "Test a line calls /v1/audio/speech, which requires an API key on this server."
      : "";
    updateSave();
  },
});
