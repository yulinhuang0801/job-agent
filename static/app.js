"use strict";

const $ = (sel) => document.querySelector(sel);

// Build DOM nodes without innerHTML, so model output and job text are never parsed as HTML.
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === "") continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

// "📝 Remembered: …" chips under a reply; ✕ forgets that fact everywhere.
function rememberedChips(facts) {
  if (!(facts || []).length) return null;
  return h("div", { class: "remembered" }, facts.map((f) => {
    const x = h("button", { class: "ghost small", type: "button", title: "Forget this" }, "✕");
    const chip = h("span", { class: "memo" }, "📝 Remembered: ", f.text, " ", x);
    x.addEventListener("click", async () => {
      await api(`/api/memory/facts/${f.id}`, { method: "DELETE" });
      chip.classList.add("forgotten");
      x.remove();
    });
    return chip;
  }));
}

// Replace an element's content. Accepts nested lists and skips null/false, which the browser's own
// replaceChildren would otherwise print as "[object HTMLDivElement]" or "null".
function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false && c !== ""));
  return el;
}

const list = (items) => h("ul", { class: "tight" }, (items || []).map((i) => h("li", {}, i)));
const chips = (items) => h("div", { class: "chips" }, (items || []).filter(Boolean).map((i) => h("span", { class: "chip" }, i)));

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("Can't reach the app server. Is it still running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `Request failed (${res.status})`);
  }
  return data;
}

function setMsg(el, text, kind) {
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

async function busy(button, label, fn) {
  const old = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

// ---------- Tabs ----------

const loaders = {};
const TABS = ["mentors", "interview", "stories", "skills", "profile", "find", "jobs", "tracker", "guide", "settings"];

function showTab(name) {
  if (!TABS.includes(name)) name = "mentors";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  Promise.resolve(loaders[name] && loaders[name]()).catch((e) => console.error(e));
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

// ---------- Settings ----------

const KNOWN_MODELS = ["deepseek-flash", "deepseek-v4-pro"];
let settings = null;

function keyField(id, has, hint, emptyPlaceholder) {
  $(id).value = "";
  $(id).placeholder = has ? `Saved (${hint}). Paste a new key to replace it` : emptyPlaceholder;
}

loaders.settings = async () => {
  settings = await api("/api/settings");
  const known = KNOWN_MODELS.includes(settings.model);
  $("#model").value = known ? settings.model : "custom";
  $("#model-custom").value = known ? "" : settings.model;
  $("#model-custom").classList.toggle("hidden", known);
  $("#base-url").value = settings.base_url;
  keyField("#api-key", settings.has_key, settings.key_hint, "sk-...");
  $("#key-status").textContent = settings.has_key ? "✓ Key saved." : "No key saved yet.";

  keyField("#backup-key", settings.has_backup, settings.backup_hint, "AIza...");
  $("#backup-status").textContent = settings.has_backup ? "✓ Backup is on." : "Off: if DeepSeek is busy you'll see an error and can retry.";
  $("#backup-model").value = settings.backup_model;
  $("#backup-base-url").value = settings.backup_base_url;

  keyField("#search-key", settings.has_search, settings.search_hint, "tvly-...");
  $("#search-status").textContent = settings.has_search ? "✓ Web lookups are on." : "Off: the coach will ask you instead of looking things up.";

  keyField("#serper-key", settings.has_serper, settings.serper_hint, "Serper API key");
  $("#serper-status").textContent = settings.has_serper ? "✓ Find uses Google results." : "Off: Find uses Tavily.";
  keyField("#hunter-key", settings.has_hunter, settings.hunter_hint, "Hunter API key");
  $("#hunter-status").textContent = settings.has_hunter ? "✓ Hunter.io lookups are on." : "Off: use Apollo.io by hand when no email is found.";

  keyField("#stt-key", settings.has_stt, settings.stt_hint, "sk-...");
  $("#stt-status").textContent = settings.has_stt ? "✓ Using this service for 🎤." : "Off: 🎤 uses your browser's voice typing.";
  $("#sec-contact").value = settings.sec_contact || "";
  $("#stt-provider").value = settings.stt_provider;
  $("#stt-base-url").value = settings.stt_base_url;
  $("#stt-model").value = settings.stt_model;
  $("#stt-model-wrap").classList.toggle("hidden", settings.stt_provider === "assemblyai");

  const saved = { api_key: settings.has_key, backup_key: settings.has_backup, search_key: settings.has_search, stt_key: settings.has_stt,
    hunter_key: settings.has_hunter, serper_key: settings.has_serper };
  document.querySelectorAll(".remove-secret").forEach((b) => b.classList.toggle("hidden", !saved[b.dataset.field]));
};

$("#stt-provider").addEventListener("change", () => {
  const assembly = $("#stt-provider").value === "assemblyai";
  $("#stt-base-url").value = assembly ? "https://api.assemblyai.com" : "https://api.siliconflow.cn/v1";
  $("#stt-model-wrap").classList.toggle("hidden", assembly);
});

$("#model").addEventListener("change", () => {
  $("#model-custom").classList.toggle("hidden", $("#model").value !== "custom");
});

$("#toggle-key").addEventListener("click", () => {
  const input = $("#api-key");
  input.type = input.type === "password" ? "text" : "password";
  $("#toggle-key").textContent = input.type === "password" ? "Show" : "Hide";
});

function settingsBody() {
  const val = (id) => $(id).value.trim() || null;
  const model = $("#model").value === "custom" ? $("#model-custom").value.trim() : $("#model").value;
  return {
    api_key: val("#api-key"), model: model || null, base_url: val("#base-url"),
    backup_key: val("#backup-key"), backup_model: val("#backup-model"), backup_base_url: val("#backup-base-url"),
    search_key: val("#search-key"), hunter_key: val("#hunter-key"), serper_key: val("#serper-key"), sec_contact: val("#sec-contact"),
    stt_key: val("#stt-key"), stt_base_url: val("#stt-base-url"), stt_model: val("#stt-model"), stt_provider: $("#stt-provider").value,
  };
}

async function saveSettings() {
  await api("/api/settings", { method: "PUT", body: settingsBody() });
  await loaders.settings();
}

$("#save-settings").addEventListener("click", (e) =>
  busy(e.target, "Saving…", async () => {
    try {
      await saveSettings();
      setMsg($("#settings-msg"), "Saved.", "ok");
    } catch (err) {
      setMsg($("#settings-msg"), err.message, "err");
    }
  })
);

document.querySelectorAll(".test-model").forEach((b) =>
  b.addEventListener("click", () =>
    busy(b, "Testing…", async () => {
      const msg = document.querySelector(`.test-msg[data-which="${b.dataset.which}"]`);
      setMsg(msg, "");
      try {
        await saveSettings();
        const r = await api(`/api/settings/test?which=${b.dataset.which}`, { method: "POST" });
        setMsg(msg, `Connected ✓ (${b.dataset.which === "stt" ? r.provider : r.model})`, "ok");
      } catch (err) {
        setMsg(msg, err.message, "err");
      }
    })
  )
);

document.querySelectorAll(".remove-secret").forEach((b) =>
  b.addEventListener("click", async () => {
    if (!confirm("Remove this saved key from this computer?")) return;
    await api(`/api/settings/${b.dataset.field}`, { method: "DELETE" });
    await loaders.settings();
  })
);

// ---------- Interview ----------

const SECTION_LABELS = {
  goals: "Your goals", education: "Education", experience: "Work experience", projects: "Projects",
  technical: "Technical skills", behavioral: "Behavioral stories", contact: "Contact details", done: "Done",
};
const GREETING = "Hi! I'm your career coach. What kind of job are you looking for? Tell me in a sentence or two: the role, the field, and where. (You can write in English or 中文.)";
let interview = { messages: [], section: "goals", progress: 0, complete: false };

function messageBubble(m) {
  if (m.kind === "upload") return h("div", { class: "bubble user upload" }, `📎 Uploaded ${interview.resume_name || "my CV"}`);
  const bubble = h("div", { class: `bubble ${m.role}` }, m.content);
  return [(m.lookups || []).length ? h("div", { class: "lookups" }, "🔎 Looked up: " + m.lookups.join(" · ")) : null,
          bubble, rememberedChips(m.remembered)].filter(Boolean);
}

function renderChat(pendingText) {
  const chat = $("#chat");
  chat.replaceChildren(h("div", { class: "bubble assistant" }, GREETING));
  for (const m of interview.messages) chat.append(...[messageBubble(m)].flat());
  if (pendingText) {
    chat.append(h("div", { class: "bubble user" }, pendingText));
    chat.append(h("div", { class: "bubble assistant typing" }, interview.search_enabled ? "Thinking (may look things up)…" : "Thinking…"));
  }
  chat.scrollTop = chat.scrollHeight;
  $("#progress-bar").style.width = interview.progress + "%";
  $("#progress-label").textContent = interview.messages.length
    ? `${interview.progress}% done · Now: ${SECTION_LABELS[interview.section] || interview.section}`
    : "";
  $("#build-profile").classList.toggle("secondary", !interview.complete);
  $("#resume-status").textContent = interview.resume_name ? `Using ${interview.resume_name}` : "";
  $("#search-badge").textContent = interview.search_enabled ? "🔎 Web lookups on" : "";
}

loaders.interview = async () => {
  interview = await api("/api/interview");
  renderChat();
  const p = await api("/api/profile");
  $("#build-profile").textContent = Object.keys(p).length ? "Update my profile (keeps your edits)" : "Build my profile";
};

// Read the coach's replies aloud with the computer's built-in voices (works offline, any country).
const speakToggle = $("#speak-toggle");
try { speakToggle.checked = localStorage.getItem("speak") === "1"; } catch (e) { /* storage blocked */ }
speakToggle.addEventListener("change", () => {
  try { localStorage.setItem("speak", speakToggle.checked ? "1" : "0"); } catch (e) { /* storage blocked */ }
  if (!speakToggle.checked && window.speechSynthesis) speechSynthesis.cancel();
});

function speak(text) {
  if (!speakToggle.checked || !window.speechSynthesis) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = /[一-鿿]/.test(text) ? "zh-CN" : "en-US";
  speechSynthesis.speak(u);
}

function lastReply() {
  const m = interview.messages[interview.messages.length - 1];
  return m && m.role === "assistant" ? m.content : "";
}

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  stopVoice();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || $("#send").disabled) return;
  input.value = "";
  $("#send").disabled = true;
  setMsg($("#interview-msg"), "");
  renderChat(text);
  try {
    interview = await api("/api/interview/message", { method: "POST", body: { text } });
    renderChat();
    speak(lastReply());
    if (interview.complete) setMsg($("#interview-msg"), "Interview complete. Click Build my profile.", "ok");
  } catch (err) {
    renderChat();
    input.value = text;
    setMsg($("#interview-msg"), err.message, "err");
  } finally {
    $("#send").disabled = false;
    input.focus();
  }
});

$("#resume-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const msg = $("#interview-msg");
  setMsg(msg, "");
  $("#resume-status").textContent = `Reading ${file.name}…`;
  renderChat(`📎 Uploading ${file.name}`);
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/interview/resume", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Upload failed (${res.status})`);
    interview = data;
    renderChat();
    speak(lastReply());
    setMsg(msg, "CV read. Answer the coach's questions, or click Build my profile any time.", "ok");
  } catch (err) {
    renderChat();
    setMsg(msg, err.message, "err");
  }
});

// ---------- Voice input ----------
// With a speech-to-text key in Settings: record audio and transcribe on the server.
// Otherwise: the browser's own voice typing (Chrome, Edge, Safari).
// attachVoice() wires a 🎤 button to a text box; only one can listen at a time.

const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
let voice = null; // the active {mic, input, lang, msg, recognizer, recorder}

function setMic(mic, on) {
  mic.classList.toggle("recording", on);
  mic.textContent = on ? "■" : "🎤";
  mic.title = on ? "Stop" : "Talk instead of typing";
}

async function startServerRecording(v) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  const rec = new MediaRecorder(stream);
  v.recorder = rec;
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const type = rec.mimeType || "audio/webm";
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("file", new Blob(chunks, { type }), `answer.${ext}`);
    v.msg.textContent = "Transcribing…";
    try {
      const res = await fetch("/api/transcribe", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Transcription failed (${res.status})`);
      v.input.value = (v.input.value.trim() + " " + data.text).trim();
      v.msg.textContent = "Check the text, then press Send.";
      v.input.focus();
    } catch (err) {
      v.msg.textContent = err.message;
    }
  };
  rec.start();
}

function startBrowserRecognition(v) {
  const before = v.input.value.trim() ? v.input.value.trim() + " " : "";
  const r = new SpeechRec();
  v.recognizer = r;
  r.lang = v.lang.value;
  r.continuous = true;
  r.interimResults = true;
  r.onresult = (e) => {
    let text = "";
    for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
    v.input.value = before + text;
  };
  r.onerror = (e) => {
    v.msg.textContent = e.error === "network"
      ? "Browser voice typing can't reach its server from here. Add a speech-to-text key in Settings."
      : e.error === "not-allowed" ? "Microphone access was blocked. Allow it in your browser's site settings."
      : `Voice typing stopped (${e.error}).`;
  };
  r.onend = () => {
    if (voice === v) stopVoice();
  };
  r.start();
}

async function startVoice(v) {
  v.msg.textContent = "";
  try {
    if (settings && settings.has_stt && window.MediaRecorder) {
      await startServerRecording(v);
    } else if (SpeechRec) {
      startBrowserRecognition(v);
    } else {
      v.msg.textContent = "This browser has no voice typing. Use Chrome, Edge or Safari, or add a speech-to-text key in Settings.";
      return;
    }
    voice = v;
    setMic(v.mic, true);
    v.msg.textContent = "Listening… press ■ when you're done.";
  } catch (err) {
    v.msg.textContent = err.name === "NotAllowedError" ? "Microphone access was blocked. Allow it in your browser's site settings." : err.message;
  }
}

function stopVoice() {
  const v = voice;
  if (!v) return;
  voice = null;
  setMic(v.mic, false);
  if (v.recognizer) {
    v.recognizer.onend = null;
    v.recognizer.stop();
    v.recognizer = null;
    v.msg.textContent = "Check the text, then press Send.";
  }
  if (v.recorder && v.recorder.state !== "inactive") v.recorder.stop();
  v.recorder = null;
}

function attachVoice(micSel, inputSel, langSel, msgSel) {
  const v = { mic: $(micSel), input: $(inputSel), lang: $(langSel), msg: $(msgSel) };
  try { v.lang.value = localStorage.getItem("voiceLang") || "en-US"; } catch (e) { /* storage blocked */ }
  v.lang.addEventListener("change", () => {
    try { localStorage.setItem("voiceLang", v.lang.value); } catch (e) { /* storage blocked */ }
  });
  v.mic.addEventListener("click", () => {
    const wasMine = voice === v;
    stopVoice();
    if (!wasMine) startVoice(v);
  });
}

attachVoice("#mic", "#chat-input", "#voice-lang", "#voice-msg");

// Enter sends, Shift+Enter adds a line. Skip while a Chinese/Japanese IME is composing.
function enterToSend(inputSel, formSel) {
  $(inputSel).addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $(formSel).requestSubmit();
    }
  });
}

enterToSend("#chat-input", "#chat-form");

$("#reset-interview").addEventListener("click", async () => {
  if (!confirm("Clear the interview and start over? (Your saved profile is kept.)")) return;
  interview = await api("/api/interview/reset", { method: "POST" });
  setMsg($("#interview-msg"), "");
  renderChat();
});

$("#build-profile").addEventListener("click", (e) =>
  busy(e.target, "Building profile…", async () => {
    try {
      const r = await api("/api/profile/build", { method: "POST" });
      const changes = r._changes || [];
      alert(changes.length ? `Profile updated (nothing was removed; your edits were kept):\n\n• ${changes.slice(0, 12).join("\n• ")}${changes.length > 12 ? `\n…and ${changes.length - 12} more` : ""}`
                           : "Your profile was already up to date.");
      location.hash = "#profile";
    } catch (err) {
      setMsg($("#interview-msg"), err.message, "err");
    }
  })
);

// ---------- Stories ----------

const STAR_LABELS = { situation: "Situation", task: "Task", action: "What you did", result: "Result", reflection: "Learned" };
const STATUS_TEXT = { done: "✓ ready", active: "in progress", pending: "to do", skipped: "skipped" };
let stories = null;

function storyCard(comp) {
  const s = stories.stories[comp];
  const kind = stories.kinds[comp];
  const stars = s.quality ? "★".repeat(s.quality) + "☆".repeat(5 - s.quality) : "";
  const body = kind === "story"
    ? h("dl", { class: "star" }, Object.entries(STAR_LABELS).map(([k, lbl]) => [
        h("dt", {}, lbl), s[k] ? h("dd", {}, s[k]) : h("dd", { class: "missing" }, "missing")]))
    : h("p", { class: "answer" }, s.answer);
  return h("div", { class: "card" },
    h("div", { class: "job-top" },
      h("div", {}, h("div", { class: "qmeta" }, `${stories.labels[comp]} · ${STATUS_TEXT[s.status] || s.status}`),
        h("b", {}, s.title || s.question)),
      stars ? h("span", { class: "quality", title: "How convincing this would be in an interview" }, stars) : null),
    body,
    (s.missing || []).length && s.status !== "done" ? h("p", { class: "small missing-note" }, "Still needed: " + s.missing.join(", ")) : null,
    s.notes ? h("p", { class: "muted small" }, s.notes) : null);
}

function renderStories(pendingText) {
  const chat = $("#story-chat");
  chat.replaceChildren(...stories.messages.map((m) => h("div", { class: `bubble ${m.role}` }, m.content)));
  if (pendingText) {
    chat.append(h("div", { class: "bubble user" }, pendingText),
      h("div", { class: "bubble assistant typing" }, pendingText.startsWith("📄") ? "Reading your document and filing each answer… (up to a minute)" : "Thinking…"));
  }
  chat.scrollTop = chat.scrollHeight;
  $("#story-steps").replaceChildren(...stories.plan.map((c) => {
    const st = stories.stories[c].status;
    const cls = c === stories.current ? "active" : st === "done" ? "done" : st === "skipped" ? "skipped" : "";
    return h("span", { class: `step ${cls}` }, `${stories.labels[c]}${st === "done" ? " ✓" : ""}`);
  }));
  // Answers with something in them, grouped like the question bank (Pitch, Motivation, Stories…).
  const filled = stories.plan.filter((c) => {
    const s = stories.stories[c];
    return s.status !== "pending" || s.answer || s.action || s.result;
  });
  const ORDER = ["Pitch", "Motivation", "Strengths & weaknesses", "Stories", "Others"];
  const groups = [...new Set(filled.map((c) => stories.groups[c]))].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
  $("#story-cards").replaceChildren(...(filled.length
    ? groups.map((g) => [h("h3", { class: "group-title" }, g), filled.filter((c) => stories.groups[c] === g).map(storyCard)])
    : [h("p", { class: "muted" }, "Answers show up here as you go.")]).flat(2));
  $("#story-extra").replaceChildren(...stories.extra_left.map((c) => h("option", { value: c }, stories.labels[c])));
  $("#story-add").disabled = !stories.extra_left.length;
  $("#story-extra").classList.toggle("hidden", !stories.extra_left.length);
  $("#story-skip").disabled = stories.done;
  $("#story-input").disabled = stories.done;
  $("#story-send").disabled = stories.done;
  setMsg($("#story-msg"), stories.done ? "All questions done. Next: the Skills check tab, or add another question." : "", stories.done ? "ok" : "");
}

loaders.stories = async () => {
  stories = await api("/api/stories");
  if (!stories.messages.length) stories = await api("/api/stories/start", { method: "POST" });
  renderStories();
  loadMyQuestions();
};

async function sendStory(text) {
  stopVoice();
  $("#story-send").disabled = true;
  setMsg($("#story-msg"), "");
  renderStories(text.length >= 2500 ? `📄 Pasted document (${text.length.toLocaleString()} characters)` : text);
  try {
    stories = await api("/api/stories/message", { method: "POST", body: { text } });
    renderStories();
    const last = stories.messages[stories.messages.length - 1];
    if (last && text.length < 2500) speak(last.content);
    return true;
  } catch (err) {
    renderStories();
    setMsg($("#story-msg"), err.message, "err");
    return false;
  } finally {
    $("#story-send").disabled = stories.done;
  }
}

$("#story-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#story-input");
  const text = input.value.trim();
  if (!text || $("#story-send").disabled) return;
  input.value = "";
  if (!(await sendStory(text))) input.value = text;
  input.focus();
});
enterToSend("#story-input", "#story-form");
attachVoice("#story-mic", "#story-input", "#story-lang", "#story-voice-msg");

$("#story-skip").addEventListener("click", () => sendStory("I don't have one for this. Can we skip it?"));

$("#story-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  setMsg($("#story-msg"), "");
  renderStories(`📄 Uploading ${file.name}`);
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/stories/import", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Import failed (${res.status})`);
    stories = data;
    renderStories();
  } catch (err) {
    renderStories();
    setMsg($("#story-msg"), err.message, "err");
  }
});

$("#story-add").addEventListener("click", async () => {
  try {
    stories = await api("/api/stories/add", { method: "POST", body: { competency: $("#story-extra").value } });
    renderStories();
  } catch (err) {
    setMsg($("#story-msg"), err.message, "err");
  }
});

$("#reset-stories").addEventListener("click", async () => {
  if (!confirm("Delete all answers and start over?")) return;
  stories = await api("/api/stories/reset", { method: "POST" });
  renderStories();
});

// ---------- Skills check ----------

let skills = null;
const LEVELS = { 1: "easy", 2: "medium", 3: "harder" };
const CODE_LANGS = [["python", "Python"], ["sql", "SQL"], ["javascript", "JavaScript"], ["java", "Java"], ["cpp", "C++"], ["plaintext", "Plain text"]];
const editors = {}; // question id -> editor for coding questions
let editorsRound = 0; // bumped on every redraw, so editors from an older draw never save

function defaultLang(q) {
  if (q.lang) return q.lang;
  const a = (q.area || "").toLowerCase();
  return a.includes("sql") ? "sql" : a.includes("javascript") || a.includes("frontend") ? "javascript"
    : a === "java" ? "java" : a.includes("c++") || a.includes("c /") ? "cpp" : a.includes("excel") || a.includes("accounting") ? "plaintext" : "python";
}

function collectAnswers() {
  const answers = {};
  document.querySelectorAll("#skills-questions textarea[data-id]").forEach((t) => (answers[t.dataset.id] = t.value));
  for (const [id, ed] of Object.entries(editors)) answers[id] = ed.getValue();
  return answers;
}

let saveTimer = null;
function saveAnswersSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api("/api/skills/answers", { method: "PUT", body: { answers: collectAnswers() } }).catch(() => {}), 800);
}

function codeBox(q, answer) {
  const box = h("div");
  const lang = h("select", { class: "inline-select", title: "Language" },
    CODE_LANGS.map(([v, t]) => h("option", { value: v, selected: v === defaultLang(q) }, t)));
  const status = h("span", { class: "muted small" }, "Loading the code editor…");
  const round = editorsRound;
  const current = () => round === editorsRound;
  CodeEditor.create(box, { value: answer, language: lang.value, isCurrent: current,
                           onChange: () => current() && saveAnswersSoon() }).then((ed) => {
    if (!current()) return ed.dispose();
    editors[q.id] = ed;
    status.textContent = ed.kind === "monaco"
      ? "VS Code editor: brackets and quotes close themselves, Tab indents."
      : "Basic editor (the VS Code editor couldn't load): brackets close themselves, Tab indents.";
  });
  lang.addEventListener("change", () => editors[q.id] && editors[q.id].setLanguage(lang.value));
  return [h("div", { class: "row editor-bar" }, lang, status), box];
}

function renderSkills() {
  const graded = skills.status === "graded";
  const answering = skills.status === "answering";
  $("#skills-start").classList.toggle("hidden", skills.status !== "none");
  $("#skills-actions").classList.toggle("hidden", !answering);
  $("#skills-retake").classList.toggle("hidden", skills.status === "none");
  $("#skills-reason").textContent = skills.status === "none" ? "" : skills.reason;
  const grades = graded ? Object.fromEntries(skills.results.grades.map((g) => [g.id, g])) : {};
  editorsRound += 1;
  for (const id of Object.keys(editors)) {
    editors[id].dispose();
    delete editors[id];
  }

  $("#skills-questions").replaceChildren(...skills.questions.map((q, i) => {
    const g = grades[q.id];
    const answer = (skills.answers || {})[q.id] || "";
    const coding = q.type === "practical";
    let input;
    if (!answering) input = h("pre", { class: "muted" + (coding ? " code-answer" : "") }, answer || "(no answer)");
    else if (coding) input = codeBox(q, answer);
    else {
      input = h("textarea", { "data-id": q.id, rows: 5, placeholder: "Your answer…" }, answer);
      input.addEventListener("input", saveAnswersSoon);
    }
    return h("div", { class: "card question" },
      h("div", { class: "job-top" },
        h("div", { class: "qmeta" }, `Question ${i + 1} · ${q.area} · ${LEVELS[q.level] || ""}${coding ? " · coding" : ""}`),
        g ? h("span", { class: `score ${g.score >= 3 ? "hi" : g.score >= 2 ? "mid" : "lo"}` }, `${g.score}/4`) : null),
      h("pre", {}, q.question),
      input,
      g ? [h("p", {}, g.feedback), g.model_answer ? h("details", {}, h("summary", {}, "A strong answer"),
        h("pre", { class: coding ? "code-answer" : "" }, g.model_answer)) : null] : null);
  }));

  const r = skills.results;
  $("#skills-results").replaceChildren(...(graded ? [h("div", { class: "card" },
    h("div", { class: "big-score" }, `${r.total} / ${r.max}`),
    h("p", {}, r.summary),
    chips((r.skills || []).map((s) => `${s.area}: ${s.level}`)),
    h("p", { class: "muted small" }, "These levels go into your profile when you click Build my profile."))] : []));
}

const pickedAreas = new Set();

function renderAreas(areas) {
  $("#skills-areas").replaceChildren(...areas.map((a) => {
    const chip = h("span", { class: `chip${pickedAreas.has(a.label) ? " on" : ""}${a.mine ? " mine" : ""}`, title: `${a.count} questions` }, a.label);
    chip.addEventListener("click", () => {
      pickedAreas.has(a.label) ? pickedAreas.delete(a.label) : pickedAreas.add(a.label);
      chip.classList.toggle("on");
    });
    return chip;
  }));
  $("#mt-areas").replaceChildren(...areas.map((a) => h("option", { value: a.label })));
}

function questionRow(text, sub, id, after) {
  const del = h("button", { class: "ghost small danger", type: "button" }, "✕");
  del.addEventListener("click", async () => {
    if (!confirm("Delete this question?")) return;
    await api(`/api/questions/${id}`, { method: "DELETE" });
    after();
  });
  return h("div", { class: "qrow" }, h("div", {}, text, sub ? h("div", { class: "muted small" }, sub) : null), del);
}

async function loadMyQuestions() {
  const q = await api("/api/questions");
  $("#mt-list").replaceChildren(...q.technical.map((t) => questionRow(t.question, `${t.area} · ${LEVELS[t.level] || ""} · ${t.type}`, t.id, refreshSkillsExtras)));
  $("#mb-list").replaceChildren(...q.behavioral.map((b) => questionRow(b.question, [b.group, b.kind === "answer" ? "short answer" : "story"].filter(Boolean).join(" · "), b.id, refreshStoriesExtras)));
}

async function refreshSkillsExtras() {
  renderAreas((await api("/api/skills/areas")).areas);
  await loadMyQuestions();
}

async function refreshStoriesExtras() {
  await loadMyQuestions();
  if (stories) {
    stories = await api("/api/stories");
    renderStories();
  }
}

function prepCard(p) {
  const practice = h("button", { class: "small", type: "button" }, "Practice these in Stories");
  practice.addEventListener("click", () => busy(practice, "Adding…", async () => {
    for (const id of p.behavioral_ids) {
      try { await api("/api/stories/add", { method: "POST", body: { competency: id } }); } catch (e) { /* already added */ }
    }
    location.hash = "#stories";
  }));
  const check = h("button", { class: "small secondary", type: "button" }, "Skills check on these");
  check.addEventListener("click", () => busy(check, "Picking questions…", async () => {
    if (skills && skills.status === "answering" && !confirm("Replace your current skills check?")) return;
    skills = await api("/api/skills/start", { method: "POST", body: { count: Math.min(5, p.technical_ids.length || 5), areas: [p.area] } });
    renderSkills();
    $("#skills-questions").scrollIntoView({ behavior: "smooth" });
  }));
  const del = h("button", { class: "ghost small danger", type: "button" }, "Delete");
  del.addEventListener("click", async () => {
    if (!confirm(`Delete the ${p.company} prep and its questions?`)) return;
    renderPreps((await api(`/api/prep/${p.id}`, { method: "DELETE" })).preps);
    refreshSkillsExtras();
  });
  return h("details", { class: "card", open: true },
    h("summary", {}, `${p.company}${p.role ? " · " + p.role : ""}`),
    p.summary ? h("p", {}, p.summary) : null,
    !p.searched ? h("p", { class: "small missing-note" }, "Built without web search (no Tavily key), from general knowledge. Double-check details.") : null,
    (p.process || []).length ? [h("b", {}, "Likely interview stages"), h("ol", { class: "tight" }, p.process.map((x) => h("li", {}, x)))] : null,
    (p.values || []).length ? [h("b", {}, "What they look for"), list(p.values.map((v) => `${v.value}: ${v.how_to_show}`))] : null,
    (p.tips || []).length ? [h("b", {}, "Tips"), list(p.tips)] : null,
    h("p", { class: "muted small" }, `Added ${p.behavioral_ids.length} behavioral and ${p.technical_ids.length} technical questions to your question bank.`),
    h("div", { class: "row actions" }, practice, check, del));
}

function renderPreps(preps) {
  $("#prep-list").replaceChildren(...preps.map(prepCard));
}

loaders.skills = async () => {
  skills = await api("/api/skills");
  renderSkills();
  refreshSkillsExtras();
  const r = await api("/api/prep");
  renderPreps(r.preps);
};

$("#skills-start-btn").addEventListener("click", (e) =>
  busy(e.target, "Picking questions…", async () => {
    try {
      skills = await api("/api/skills/start", { method: "POST", body: { count: +$("#skills-count").value, areas: [...pickedAreas] } });
      setMsg($("#skills-start-msg"), "");
      renderSkills();
    } catch (err) {
      setMsg($("#skills-start-msg"), err.message, "err");
    }
  })
);

$("#prep-go").addEventListener("click", (e) => busy(e.target, "Researching the company… (up to a minute)", async () => {
  try {
    const r = await api("/api/prep", { method: "POST", body: { company: $("#prep-company").value, role: $("#prep-role").value } });
    renderPreps(r.preps);
    refreshSkillsExtras();
    setMsg($("#prep-msg"), "Done. Your new questions are in Stories and in this check's areas.", "ok");
  } catch (err) {
    setMsg($("#prep-msg"), err.message, "err");
  }
}));

$("#mt-add").addEventListener("click", (e) => busy(e.target, "Adding…", async () => {
  try {
    await api("/api/questions", { method: "POST", body: { kind: "technical", area: $("#mt-area").value.trim(), type: $("#mt-type").value,
      level: +$("#mt-level").value, question: $("#mt-question").value.trim(), rubric: $("#mt-rubric").value.trim() } });
    $("#mt-question").value = ""; $("#mt-rubric").value = "";
    setMsg($("#mt-msg"), "Added.", "ok");
    refreshSkillsExtras();
  } catch (err) {
    setMsg($("#mt-msg"), err.message, "err");
  }
}));

$("#mb-add").addEventListener("click", (e) => busy(e.target, "Adding…", async () => {
  try {
    await api("/api/questions", { method: "POST", body: { kind: "behavioral", question: $("#mb-question").value.trim(),
      look_for: $("#mb-look").value.trim(), type: $("#mb-kind").value } });
    $("#mb-question").value = ""; $("#mb-look").value = "";
    setMsg($("#mb-msg"), "Added. Pick it under \"Add this question\" above.", "ok");
    refreshStoriesExtras();
  } catch (err) {
    setMsg($("#mb-msg"), err.message, "err");
  }
}));

$("#skills-submit").addEventListener("click", (e) => {
  const answers = collectAnswers();
  const blank = Object.values(answers).filter((a) => !a.trim()).length;
  if (blank && !confirm(`${blank} question(s) are blank and will score 0. Submit anyway?`)) return;
  busy(e.target, "Grading…", async () => {
    try {
      skills = await api("/api/skills/grade", { method: "POST", body: { answers } });
      setMsg($("#skills-msg"), "");
      renderSkills();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      setMsg($("#skills-msg"), err.message, "err");
    }
  });
});

$("#skills-retake").addEventListener("click", async () => {
  if (!confirm("Start a new skills check? Your current answers and results will be cleared.")) return;
  skills = await api("/api/skills/reset", { method: "POST" });
  renderSkills();
});

// ---------- Profile ----------

function dates(x) {
  return [x.start, x.end].filter(Boolean).join(" – ");
}

function renderEvaluation(ev) {
  fill($("#evaluation"), 
    h("h2", {}, "How you stack up"),
    ev.readiness ? h("p", {}, ev.readiness) : null,
    h("div", { class: "cols" },
      h("div", {}, h("b", {}, "Strengths"), list(ev.strengths)),
      h("div", {}, h("b", {}, "Gaps to work on"), list(ev.gaps))),
    ev.recommended_roles && ev.recommended_roles.length ? [h("b", {}, "Roles to target"), chips(ev.recommended_roles)] : null,
    ev.next_steps && ev.next_steps.length ? [h("b", {}, "Next steps"), list(ev.next_steps)] : null
  );
}

function renderProfileSummary(p) {
  const t = p.targets || {};
  const sk = p.skills || {};
  const contact = [p.email, p.phone, p.location].filter(Boolean).join(" · ");
  fill($("#profile-summary"), 
    h("h2", {}, p.name || "Name not given yet"),
    contact ? h("p", { class: "muted small" }, contact) : null,
    p.summary ? h("p", {}, p.summary) : null,
    h("b", {}, "Looking for"),
    chips([...(t.roles || []), ...(t.locations || []), t.work_authorization]),
    (p.experience || []).length ? h("b", {}, "Experience") : null,
    (p.experience || []).map((x) => h("div", { class: "entry" },
      h("b", {}, [x.title, x.company].filter(Boolean).join(" — ")), " ", h("span", { class: "muted small" }, dates(x)),
      list(x.bullets))),
    (p.projects || []).length ? h("b", {}, "Projects") : null,
    (p.projects || []).map((x) => h("div", { class: "entry" }, h("b", {}, x.name), list(x.bullets))),
    (p.education || []).length ? h("b", {}, "Education") : null,
    (p.education || []).map((x) => h("div", { class: "entry" },
      h("b", {}, x.school), " ", h("span", { class: "muted small" }, [x.degree, x.field, dates(x)].filter(Boolean).join(" · ")))),
    (sk.technical || []).length ? [h("b", {}, "Skills"), chips((sk.technical || []).map((s) => (typeof s === "string" ? s : `${s.name}${s.level ? " · " + s.level : ""}${s.verified ? " ✓" : ""}`)))] : null,
    (p.behavioral_stories || []).length ? [h("b", {}, "Stories for interviews"), chips(p.behavioral_stories.map((s) => s.theme))] : null
  );
}

loaders.profile = async () => {
  const p = await api("/api/profile");
  const empty = !p || !Object.keys(p).length;
  $("#profile-empty").classList.toggle("hidden", !empty);
  $("#profile-body").classList.toggle("hidden", empty);
  $("#master-resume").classList.toggle("disabled", empty);
  if (empty) return;
  $("#profile-json").value = JSON.stringify(p, null, 2);
  renderEvaluation(p.evaluation || {});
  renderProfileSummary(p);
};

$("#save-profile").addEventListener("click", async () => {
  const msg = $("#profile-msg");
  let data;
  try {
    data = JSON.parse($("#profile-json").value);
  } catch (err) {
    setMsg(msg, "That isn't valid JSON: " + err.message, "err");
    return;
  }
  try {
    await api("/api/profile", { method: "PUT", body: data });
    await loaders.profile();
    setMsg(msg, "Saved.", "ok");
  } catch (err) {
    setMsg(msg, err.message, "err");
  }
});

// ---------- Find: database ----------

const DB_FILTERS = {
  postings: [["job_type", "Type"], ["function", "Job function"], ["industry", "Industry"], ["term", "Term"], ["state", "State"],
             ["country", "Country"], ["sponsorship", "Visa"], ["source", "Source"]],
  firms: [["industry", "Industry"], ["category", "Category"], ["size", "Employees"], ["state", "State"], ["source", "Source"]],
};
const DB_TOGGLES = {
  postings: [["remote", "Remote only"]],
  firms: [["small", "Small firms (≤30 people)"], ["has_website", "Has a website"], ["has_people", "Has named people"]],
};
let dbTable = "postings";
let dbFilters = { postings: { job_type: "Internship" }, firms: { small: "1" } };
let dbPage = 1;
let dbResult = null;
let dbPoll = null;
const SOURCE_KEYS = { "SEC Form ADV": "adv", "SEC Form D": "formd", "Job boards": "jobs" };
const SOURCE_TEXT = {
  "SEC Form ADV": "Investment advisers (PE, VC, hedge, real estate, wealth managers), monthly",
  "SEC Form D": "New funds and startups that just raised money, with people on the filing, quarterly",
  "Job boards": "Company job boards (Greenhouse, Lever, Ashby) and SimplifyJobs internship lists",
};

function setFindMode(mode) {
  document.querySelectorAll("#find-modes .coach-tab").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("#find-db").classList.toggle("hidden", mode !== "db");
  $("#find-web").classList.toggle("hidden", mode !== "web");
  $("#find-import").classList.toggle("hidden", mode !== "import");
  $("#find-results").classList.toggle("hidden", mode === "db");
  try { localStorage.setItem("findMode", mode); } catch (e) { /* storage blocked */ }
}
document.querySelectorAll("#find-modes .coach-tab").forEach((b) => b.addEventListener("click", () => setFindMode(b.dataset.mode)));

async function loadDbStatus() {
  const st = await api("/api/db/status");
  $("#db-counts").textContent = `(${st.counts.firms.toLocaleString()} firms & funds · ${st.counts.people.toLocaleString()} people · ${st.counts.postings.toLocaleString()} postings)`;
  $("#db-sec-note").classList.toggle("hidden", st.sec_contact_set);
  fill($("#db-source-list"), Object.entries(SOURCE_KEYS).map(([name, key]) => {
    const s = st.sources[name] || {};
    const running = st.running.includes(name);
    const btn = h("button", { class: "small secondary", type: "button", disabled: running }, running ? "Updating…" : s.updated ? "Update" : "Download");
    btn.addEventListener("click", async () => {
      try {
        await api("/api/db/refresh", { method: "POST", body: { source: key } });
        loadDbStatus();
      } catch (err) { setMsg($("#db-msg"), err.message, "err"); }
    });
    return h("div", { class: "qrow" },
      h("div", {}, h("b", {}, name), h("div", { class: "muted small" }, SOURCE_TEXT[name]),
        h("div", { class: "small" + (s.status === "error" ? " sev-high" : "") },
          running ? `⏳ ${s.message || "Working…"}` : s.status === "error" ? `Failed: ${s.message}` : s.updated ? `✓ ${s.message} · updated ${s.updated.slice(0, 10)}` : "Not downloaded yet")),
      btn);
  }));
  clearTimeout(dbPoll);
  if (st.running.length) dbPoll = setTimeout(() => { loadDbStatus(); if (!$("#find-db").classList.contains("hidden")) dbSearch(); }, 4000);
}

function dbParams() {
  const params = new URLSearchParams({ page: dbPage });
  const q = $("#db-q").value.trim();
  if (q) params.set("q", q);
  for (const [k, v] of Object.entries(dbFilters[dbTable])) if (v) params.set(k, v);
  return params;
}

async function dbSearch(resetPage) {
  if (resetPage) dbPage = 1;
  try {
    dbResult = await api(`/api/db/${dbTable}?${dbParams()}`);
    renderDbFilters();
    renderDbResults();
  } catch (err) {
    fill($("#db-results"), h("p", { class: "msg err" }, err.message));
  }
}

function renderDbFilters() {
  const f = dbFilters[dbTable];
  const selects = DB_FILTERS[dbTable].map(([key, label]) => {
    const options = (dbResult.facets[key] || []).map((o) => h("option", { value: o.value, selected: f[key] === o.value }, `${o.value} (${o.count.toLocaleString()})`));
    if (f[key] && !options.some((o) => o.value === f[key])) options.unshift(h("option", { value: f[key], selected: true }, f[key]));
    const sel = h("select", {}, h("option", { value: "" }, `Any ${label.toLowerCase()}`), options);
    sel.addEventListener("change", () => { f[key] = sel.value; dbSearch(true); });
    return h("label", { class: "filter" }, h("span", { class: "muted small" }, label), sel);
  });
  const toggles = DB_TOGGLES[dbTable].map(([key, label]) => {
    const cb = h("input", { type: "checkbox", checked: f[key] === "1" });
    cb.addEventListener("change", () => { f[key] = cb.checked ? "1" : ""; dbSearch(true); });
    return h("label", { class: "toggle small" }, cb, " ", label);
  });
  const clear = h("button", { class: "ghost small", type: "button" }, "Clear filters");
  clear.addEventListener("click", () => { dbFilters[dbTable] = {}; $("#db-q").value = ""; dbSearch(true); });
  fill($("#db-filter-row"), selects, h("div", { class: "row filter-toggles" }, toggles, clear));
}

function money(n) {
  if (!n) return "";
  return n >= 1e9 ? `$${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}K`;
}

function dbRow(r, i) {
  const cb = h("input", { type: "checkbox", "data-i": i });
  if (dbTable === "postings") {
    return h("label", { class: "card candidate" }, cb, h("div", { class: "grow" },
      h("div", { class: "job-top" }, h("b", {}, `${r.company} · ${r.title}`), h("span", { class: "chip" }, r.job_type)),
      h("div", { class: "muted small" }, [r.location, r.remote ? "Remote" : "", r.term, r.function, r.industry].filter(Boolean).join(" · ")),
      h("div", { class: "small" }, h("a", { href: r.url, target: "_blank", rel: "noopener" }, "Posting ↗"),
        r.posted ? h("span", { class: "muted" }, ` · posted ${r.posted}`) : null,
        r.sponsorship ? h("span", { class: "sev-medium" }, ` · ${r.sponsorship}`) : null)));
  }
  const people = (r.people || []).filter((p) => !/^n\/a|llc|l\.p\.|lp$|gp,/i.test(p.name)).slice(0, 3);
  return h("label", { class: "card candidate" }, cb, h("div", { class: "grow" },
    h("div", { class: "job-top" }, h("b", {}, r.name), h("span", { class: "chip" }, r.category)),
    h("div", { class: "muted small" }, [[r.city, r.state].filter(Boolean).join(", "), r.employees != null ? `${r.employees} employees` : "",
      r.fund_types && r.kind === "adviser" ? r.fund_types : "", r.raised ? `raised ${money(r.raised)}` : "", r.assets ? `fund assets ${money(r.assets)}` : "",
      r.filed ? `${r.source === "SEC Form D" ? "filed" : "ADV updated"} ${r.filed}` : ""].filter(Boolean).join(" · ")),
    people.length ? h("div", { class: "small" }, "People: ", people.map((p) => `${p.name} (${p.title.split(",")[0]})`).join("; ")) : null,
    h("div", { class: "small" }, r.website ? h("a", { href: r.website, target: "_blank", rel: "noopener" }, `${r.domain || "website"} ↗`) : h("span", { class: "muted" }, "No website on file (Research will look it up)"),
      r.extra && r.extra.filing_url ? [" · ", h("a", { href: r.extra.filing_url, target: "_blank", rel: "noopener" }, "SEC filings ↗")] : null)));
}

function renderDbResults() {
  const r = dbResult;
  if (!r.total) {
    fill($("#db-results"), h("p", { class: "muted" }, "No matches. Loosen the filters, or download the data sources above."));
    return;
  }
  const add = h("button", { type: "button" }, "Add selected to Jobs");
  const msg = h("span", { class: "msg" });
  add.addEventListener("click", () => busy(add, "Adding…", async () => {
    const picked = [...$("#db-results").querySelectorAll("input[type=checkbox]:checked")].map((cb) => r.rows[+cb.dataset.i]);
    if (!picked.length) return setMsg(msg, "Tick at least one.", "err");
    const candidates = picked.map((x) => dbTable === "postings"
      ? { name: x.company, title: x.title, kind: "posting", posting_url: x.url, website: "", location: x.location, category: x.industry, reasons: `${x.job_type} · ${x.function}` }
      : { name: x.name, website: x.website, kind: "firm", location: [x.city, x.state].filter(Boolean).join(", "), category: x.category,
          reasons: [x.category, x.employees != null ? `${x.employees} employees` : ""].filter(Boolean).join(" · "),
          contact_name: ((x.people || []).find((p) => !/llc|l\.p\.|lp$|gp,|^n\/a/i.test(p.name)) || {}).name || "",
          contact_title: ((x.people || []).find((p) => !/llc|l\.p\.|lp$|gp,|^n\/a/i.test(p.name)) || {}).title || "" });
    try {
      const res = await api("/api/find/add", { method: "POST", body: { candidates } });
      jobs = res.jobs;
      setMsg(msg, `Added ${res.added}. Open the Jobs tab to research them.`, "ok");
    } catch (err) { setMsg(msg, err.message, "err"); }
  }));
  const pages = Math.ceil(r.total / r.per_page);
  const prev = h("button", { class: "ghost small", type: "button", disabled: r.page <= 1 }, "← Previous");
  const next = h("button", { class: "ghost small", type: "button", disabled: r.page >= pages }, "Next →");
  prev.addEventListener("click", () => { dbPage -= 1; dbSearch(); });
  next.addEventListener("click", () => { dbPage += 1; dbSearch(); });
  fill($("#db-results"),
    h("div", { class: "row actions" }, h("b", {}, `${r.total.toLocaleString()} ${dbTable === "postings" ? "postings" : "firms & funds"}`),
      h("span", { class: "muted small" }, `page ${r.page} of ${pages}`), h("span", { class: "spacer" }), add, msg),
    r.rows.map(dbRow),
    h("div", { class: "row actions" }, prev, next));
}

document.querySelectorAll("#find-db .mode-tabs .coach-tab").forEach((b) => b.addEventListener("click", () => {
  dbTable = b.dataset.table;
  document.querySelectorAll("#find-db .mode-tabs .coach-tab").forEach((x) => x.classList.toggle("on", x === b));
  dbSearch(true);
}));
$("#db-go").addEventListener("click", () => dbSearch(true));
$("#db-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); dbSearch(true); } });
$("#db-board-add").addEventListener("click", (e) => busy(e.target, "Reading the board…", async () => {
  try {
    const r = await api("/api/db/boards", { method: "POST", body: { url: $("#db-board-url").value } });
    setMsg($("#db-msg"), `Added ${r.company}: ${r.postings} postings.`, "ok");
    $("#db-board-url").value = "";
    loadDbStatus(); dbSearch(true);
  } catch (err) { setMsg($("#db-msg"), err.message, "err"); }
}));

// ---------- Find ----------

let findCategories = [];
let candidates = [];
const DIRECTORIES = [
  ["Axial", "https://www.axial.net"],
  ["SearchFunder", "https://www.searchfunder.com"],
  ["Private Equity List", "https://privateequitylist.com"],
  ["CT Acquisitions", "https://ctacquisitions.com"],
  ["Apollo.io", "https://app.apollo.io"],
];

function linkedinSearch() {
  const cat = findCategories.find((c) => c.id === $("#find-category").value);
  const words = [cat ? cat.label.replace(/\(.*\)/, "").replace("&", "") : "", $("#find-industry").value, $("#find-location").value].join(" ").trim();
  // companySize B = 1-10 employees, C = 11-50 (the guide's "under ~30 employees" rule)
  return `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(words)}&companySize=%5B%22B%22%2C%22C%22%5D`;
}

function renderDirectoryLinks() {
  fill($("#directory-links"), 
    ...DIRECTORIES.map(([name, url]) => h("a", { class: "button secondary small", href: url, target: "_blank", rel: "noopener" }, name)),
    h("a", { class: "button secondary small", href: linkedinSearch(), target: "_blank", rel: "noopener", id: "linkedin-link" }, "LinkedIn company search (1–50 people)"));
}

function renderCandidates() {
  const box = $("#find-results");
  if (!candidates.length) {
    box.replaceChildren();
    return;
  }
  const rows = candidates.map((c, i) => h("label", { class: "card candidate" + (c.already_added ? " muted" : "") },
    h("input", { type: "checkbox", "data-i": i, checked: !c.already_added && c.fit_score >= 50, disabled: c.already_added }),
    h("div", { class: "grow" },
      h("div", { class: "job-top" },
        h("b", {}, c.name + (c.title ? ` · ${c.title}` : "")),
        c.fit_score ? h("span", { class: `score ${scoreClass(c.fit_score)}` }, c.fit_score) : null),
      h("div", { class: "muted small" },
        [c.website ? h("a", { href: c.website, target: "_blank", rel: "noopener" }, c.domain || c.website)
          : c.posting_url ? h("a", { href: c.posting_url, target: "_blank", rel: "noopener" }, "posting ↗") : "website unknown",
         c.location, c.size_hint, c.already_added ? "already in Jobs" : ""].filter(Boolean).flatMap((x, k) => (k ? [" · ", x] : [x]))),
      c.reasons ? h("div", { class: "small" }, c.reasons) : null,
      c.red_flags ? h("div", { class: "small sev-high" }, "⚠ " + c.red_flags) : null)));
  const add = h("button", { type: "button" }, "Add selected to Jobs");
  const msg = h("span", { class: "msg" });
  add.addEventListener("click", () => busy(add, "Adding…", async () => {
    const picked = [...box.querySelectorAll("input[type=checkbox]:checked")].map((cb) => candidates[+cb.dataset.i]);
    if (!picked.length) return setMsg(msg, "Tick at least one.", "err");
    try {
      const r = await api("/api/find/add", { method: "POST", body: { candidates: picked } });
      jobs = r.jobs;
      candidates = candidates.map((c) => (picked.includes(c) ? { ...c, already_added: true } : c));
      renderCandidates();
      setMsg($("#find-msg"), `Added ${r.added}. Open the Jobs tab to research them.`, "ok");
    } catch (err) {
      setMsg(msg, err.message, "err");
    }
  }));
  fill(box, h("h2", { class: "section-title" }, `${candidates.length} results`),
    h("p", { class: "muted small" }, "Ticked: fit score 50 or more. Check the red flags before adding."),
    ...rows, h("div", { class: "row actions" }, add, msg));
}

function updateFindForm() {
  const cat = findCategories.find((c) => c.id === $("#find-category").value) || {};
  $("#find-role-wrap").classList.toggle("hidden", !(cat.kind === "posting" || cat.source !== "web"));
  $("#find-industry").parentElement.classList.toggle("hidden", cat.source === "simplify" || cat.source === "hn");
  $("#find-engine").textContent = cat.source === "simplify" ? "Reads the SimplifyJobs internship list on GitHub (free, updated hourly). Closed roles are skipped."
    : cat.source === "hn" ? "Searches this month's Hacker News \"Who is hiring?\" thread (free)."
    : findEngine ? `Searches with ${findEngine}; each search uses about 4–8 searches of its monthly allowance.` : "";
}

let findEngine = "";

loaders.find = async () => {
  const r = await api("/api/find/categories");
  findEngine = r.engine;
  if (!findCategories.length) {
    findCategories = r.categories;
    $("#find-category").replaceChildren(...findCategories.map((c) => h("option", { value: c.id }, c.label)));
  }
  $("#find-no-search").classList.toggle("hidden", r.search_enabled);
  $("#src-sites").value = r.sources.sites.join("\n");
  $("#src-queries").value = r.sources.queries.join("\n");
  updateFindForm();
  renderDirectoryLinks();
  renderCandidates();
  let mode = "db";
  try { mode = localStorage.getItem("findMode") || "db"; } catch (e) { /* storage blocked */ }
  setFindMode(mode);
  loadDbStatus();
  if (!dbResult) dbSearch(true);
};

$("#src-save").addEventListener("click", (e) => busy(e.target, "Saving…", async () => {
  try {
    const r = await api("/api/find/sources", { method: "PUT", body: {
      sites: $("#src-sites").value.split("\n"), queries: $("#src-queries").value.split("\n") } });
    $("#src-sites").value = r.sites.join("\n");
    setMsg($("#src-msg"), `Saved ${r.sites.length} sites and ${r.queries.length} phrases.`, "ok");
  } catch (err) {
    setMsg($("#src-msg"), err.message, "err");
  }
}));

$("#find-category").addEventListener("change", () => {
  updateFindForm();
  renderDirectoryLinks();
});
["#find-industry", "#find-location"].forEach((s) => $(s).addEventListener("input", renderDirectoryLinks));

$("#find-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const src = (findCategories.find((c) => c.id === $("#find-category").value) || {}).source;
  busy($("#find-go"), src === "simplify" ? "Reading the list…" : "Searching… (about a minute)", async () => {
    setMsg($("#find-msg"), "");
    try {
      const r = await api("/api/find/search", { method: "POST", body: {
        category: $("#find-category").value, location: $("#find-location").value,
        industry: $("#find-industry").value, role: $("#find-role").value } });
      candidates = r.candidates;
      renderCandidates();
      setMsg($("#find-msg"), candidates.length ? "" : "Nothing useful came back. Try another location or industry word.", candidates.length ? "" : "err");
    } catch (err) {
      setMsg($("#find-msg"), err.message, "err");
    }
  });
});

$("#import-go").addEventListener("click", (e) => busy(e.target, "Reading…", async () => {
  try {
    const r = await api("/api/find/import", { method: "POST", body: { text: $("#import-text").value } });
    candidates = r.candidates.map((c) => ({ ...c, fit_score: c.already_added ? 0 : 60 }));
    renderCandidates();
    setMsg($("#import-msg"), `Found ${candidates.length}.`, "ok");
  } catch (err) {
    setMsg($("#import-msg"), err.message, "err");
  }
}));

// ---------- Jobs ----------

let jobs = [];
const openJobs = new Set();
const scoreClass = (s) => (s >= 75 ? "hi" : s >= 50 ? "mid" : "lo");

function replaceJob(updated) {
  jobs = jobs.map((j) => (j.id === updated.id ? updated : j));
  renderJobs();
}

async function patchJob(id, changes) {
  const updated = await api(`/api/jobs/${id}`, { method: "PATCH", body: changes });
  jobs = jobs.map((j) => (j.id === id ? updated : j));
  return updated;
}

function copyButton(getText, label = "Copy") {
  const b = h("button", { type: "button", class: "ghost small" }, label);
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      b.textContent = "Copied ✓";
    } catch (e) {
      b.textContent = "Select and copy manually";
    }
    setTimeout(() => (b.textContent = label), 1500);
  });
  return b;
}

function fitDetails(fit) {
  return h("details", { class: "fit" },
    h("summary", {}, `Fit: ${fit.score} · ${fit.verdict}`),
    fit.summary ? h("p", {}, fit.summary) : null,
    (fit.matched || []).length ? [h("b", {}, "Where you match"), list(fit.matched.map((m) => `${m.requirement}: ${m.evidence}`))] : null,
    (fit.gaps || []).length ? [h("b", {}, "Gaps"),
      h("ul", { class: "tight" }, fit.gaps.map((g) => h("li", {},
        h("span", { class: `sev-${g.severity}` }, `[${g.severity}] `), g.requirement, g.advice ? ` → ${g.advice}` : "")))] : null,
    (fit.missing_keywords || []).length ? [h("b", {}, "Keywords you don't show yet"), chips(fit.missing_keywords)] : null);
}

// Shown when there's no confirmed email: walks the user through finding it themselves.
function needsEmailHelp(job) {
  const c = job.contact || {};
  return !!job.research && (!c.email || /guess|general|not found|built from/.test(c.email_source || ""));
}

function findEmailHelper(job) {
  const c = job.contact || {};
  const r = job.research || {};
  const domain = (job.website || "").replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  const q = encodeURIComponent;
  const name = c.name || "";
  const teamPages = (r.pages || []).filter((u) => /team|people|leader|about|contact|partner|professional/i.test(u));
  const links = [
    teamPages.length ? ["Team / contact page", teamPages[0]] : job.website ? ["Firm website", job.website] : null,
    ["Apollo.io", "https://app.apollo.io/#/people"],
    name ? ["LinkedIn profile", `https://www.linkedin.com/search/results/people/?keywords=${q(`${name} ${job.company || ""}`)}`] : null,
    domain ? ["Hunter domain search", `https://hunter.io/search/${domain}`] : null,
    domain ? ["Google for emails", `https://www.google.com/search?q=${q(`"@${domain}"`)}`] : null,
  ].filter(Boolean);
  const knownEmail = h("input", { placeholder: `anyone@${domain || "firm.com"}` });
  const knownName = h("input", { placeholder: "That person's full name" });
  const msg = h("span", { class: "msg" });
  const build = h("button", { class: "small", type: "button" }, "Build contact's email");
  build.addEventListener("click", () => busy(build, "Working…", async () => {
    try {
      replaceJob(await api(`/api/jobs/${job.id}/email-pattern`, { method: "POST",
        body: { known_email: knownEmail.value, known_name: knownName.value } }));
    } catch (err) {
      setMsg(msg, err.message, "err");
    }
  }));
  return h("div", { class: "notice" },
    h("b", {}, c.email ? "This email isn't confirmed yet. Check it yourself:" : "No email found yet. Find it yourself:"),
    h("ol", { class: "tight" },
      h("li", {}, "Look on the firm's team or contact page; about 30% of firms list emails there."),
      h("li", {}, "Search ", h("b", {}, name || "the contact"), " on Apollo.io: Search → People → name and company → Access email (prefer Verified)."),
      h("li", {}, "Check their LinkedIn profile → Contact info."),
      h("li", {}, "Still nothing? Find any one person's email at the firm (Hunter domain search or Google), enter it below with that person's name, and Job Agent builds your contact's email from the same pattern.")),
    h("div", { class: "row" }, links.map(([t, u]) => h("a", { class: "button small secondary", href: u, target: "_blank", rel: "noopener" }, `${t} ↗`))),
    h("div", { class: "grid2" }, knownEmail, knownName),
    h("div", { class: "row actions" }, build, msg),
    h("p", { class: "small muted" }, "Found the email directly? Paste it in the Email box above. No email at all? Use the LinkedIn note in step 2. ",
      h("a", { href: "#guide" }, "Full guide")));
}

function researchSection(job, run) {
  const r = job.research;
  const c = job.contact || {};
  const inp = (field, value, placeholder) => {
    const el = h("input", { value: value || "", placeholder });
    el.addEventListener("change", () => patchJob(job.id, { [field]: el.value }).then(renderJobs));
    return el;
  };
  const noEmail = needsEmailHelp(job);
  return h("details", { class: "fit", open: !r || noEmail || undefined },
    h("summary", {}, "1. Research & contact" + (c.name ? ` · ${c.name}` : "")),
    h("div", { class: "row actions" },
      h("button", { class: "small" + (r ? " secondary" : ""), onclick: run("Researching… (up to a minute)", "research") }, r ? "Research again" : "Research firm"),
      job.website ? h("a", { href: job.website, target: "_blank", rel: "noopener", class: "small" }, "Website ↗") : null),
    !job.website ? [h("label", {}, "Website"), inp("website", job.website, "https://… (leave empty and Research will try to find it)")] : null,
    r ? [
      h("p", {}, r.summary || ""),
      r.focus ? h("p", { class: "small" }, h("b", {}, "Focus: "), r.focus) : null,
      (r.hooks || []).length ? [h("b", {}, "Hooks for your email"), list(r.hooks)] : null,
      h("p", { class: "small muted" }, [r.size_estimate, r.closed_deal && r.closed_deal !== "not a search fund" ? `closed a deal: ${r.closed_deal}` : "", r.fit_notes].filter(Boolean).join(" · ")),
      (r.people || []).length ? h("details", {}, h("summary", {}, `People found (${r.people.length})`),
        list(r.people.map((p) => [p.name, p.title, p.email].filter(Boolean).join(" · ")))) : null,
    ] : null,
    h("div", { class: "grid2" },
      h("div", {}, h("label", {}, "Contact"), inp("contact_name", c.name, "Name")),
      h("div", {}, h("label", {}, "Title"), inp("contact_title", c.title, "Founder, MD, Partner…"))),
    h("label", {}, "Email"), inp("contact_email", c.email, "name@firm.com"),
    c.email_source ? h("p", { class: "small " + (/guess|not found|general/.test(c.email_source) ? "missing-note" : "muted") }, c.email_source) : null,
    needsEmailHelp(job) ? findEmailHelper(job) : null,
    h("label", {}, "How to apply"),
    (() => {
      const sel = h("select", {}, [["email", "Cold email"], ["form", "Application form"], ["linkedin", "LinkedIn message"]].map(
        ([v, t]) => h("option", { value: v, selected: (job.channel || "email") === v }, t)));
      sel.addEventListener("change", () => patchJob(job.id, { channel: sel.value }).then(renderJobs));
      return sel;
    })(),
    r && r.channel_reason ? h("p", { class: "muted small" }, r.channel_reason) : null);
}

function emailSection(job, run) {
  const e = job.email;
  const c = job.contact || {};
  const resumeLink = job.documents ? `/api/jobs/${job.id}/resume.pdf` : "/api/profile/resume.pdf";
  if (!e) {
    return h("details", { class: "fit", open: !!job.research || undefined },
      h("summary", {}, "2. Cold email"),
      h("p", { class: "muted small" }, "Uses your profile, stories, outreach details and the firm research. Research the firm first for a specific hook."),
      h("button", { class: "small", onclick: run("Writing…", "email") }, "Write email"));
  }
  const subject = h("input", { value: e.subject });
  const body = h("textarea", { rows: 14 }, e.body);
  const note = h("textarea", { rows: 3 }, e.linkedin_note);
  const follow = h("textarea", { rows: 4 }, e.followup);
  const save = (field, el) => el.addEventListener("change", () =>
    api(`/api/jobs/${job.id}/email`, { method: "PATCH", body: { [field]: el.value } }).then((u) => (jobs = jobs.map((j) => (j.id === u.id ? u : j)))));
  save("subject", subject); save("body", body); save("linkedin_note", note); save("followup", follow);
  const count = h("span", { class: "muted small" });
  const updateCount = () => (count.textContent = `${note.value.length}/300 characters`);
  note.addEventListener("input", updateCount); updateCount();
  const compose = (kind) => {
    const to = encodeURIComponent(c.email || ""), su = encodeURIComponent(subject.value), bo = encodeURIComponent(body.value);
    return kind === "gmail" ? `https://mail.google.com/mail/?view=cm&fs=1&to=${to}&su=${su}&body=${bo}`
      : kind === "outlook" ? `https://outlook.office.com/mail/deeplink/compose?to=${to}&subject=${su}&body=${bo}`
      : `mailto:${to}?subject=${su}&body=${bo}`;
  };
  const opener = (kind, label) => {
    const a = h("a", { class: "button small secondary", target: "_blank", rel: "noopener", href: "#" }, label);
    a.addEventListener("click", () => (a.href = compose(kind)));
    return a;
  };
  const sent = h("button", { class: "small", type: "button" }, job.contacted_date ? `Sent ${job.contacted_date} ✓` : "I sent it");
  sent.addEventListener("click", async () => replaceJob(await api(`/api/jobs/${job.id}/sent`, { method: "POST" })));
  return h("details", { class: "fit", open: true },
    h("summary", {}, "2. Cold email" + (job.contacted_date ? ` · sent ${job.contacted_date}` : "")),
    h("label", {}, "To"), h("p", {}, c.email || h("span", { class: "missing-note" }, "No email yet: add it in step 1 (Apollo.io).")),
    h("label", {}, "Subject"), subject,
    h("label", {}, "Email"), body,
    h("p", { class: "small" }, "📎 Attach your resume: ", h("a", { href: resumeLink }, job.documents ? "tailored resume PDF" : "your CV PDF"),
      job.documents ? null : h("span", { class: "muted" }, " (or write a tailored one below)")),
    h("div", { class: "row actions" }, opener("gmail", "Open in Gmail"), opener("outlook", "Open in Outlook"), opener("mailto", "Mail app"),
      copyButton(() => body.value, "Copy email"), sent,
      h("button", { class: "ghost small", onclick: run("Rewriting…", "email") }, "Rewrite")),
    h("p", { class: "muted small" }, "Opens a draft in your own mailbox. Nothing is sent until you press Send there."),
    h("details", {}, h("summary", {}, "LinkedIn connection note"), note, h("div", { class: "row" }, count, copyButton(() => note.value))),
    h("details", { open: !!job.followup_due || undefined }, h("summary", {}, "Follow-up" + (job.followup_due ? ` (send by ${job.followup_due})` : " (after 7 days)")),
      follow, h("div", { class: "row" }, copyButton(() => follow.value))));
}

function formSection(job) {
  const f = job.form;
  const box = h("details", { class: "fit", open: true }, h("summary", {}, "2. Application form"));
  const msg = h("span", { class: "msg" });
  if (!f) {
    const url = h("input", { value: job.apply_url || job.url || "", placeholder: "Application page link" });
    const text = h("textarea", { rows: 6, placeholder: "…or copy everything on the application form and paste it here (works for any site)" });
    const go = h("button", { class: "small", type: "button" }, "Read the form");
    go.addEventListener("click", () => busy(go, "Reading and drafting answers… (up to a minute)", async () => {
      try {
        replaceJob(await api(`/api/jobs/${job.id}/form`, { method: "POST", body: { url: text.value.trim() ? "" : url.value, text: text.value } }));
      } catch (err) {
        setMsg(msg, err.message, "err");
      }
    }));
    box.append(h("p", { class: "muted small" }, "Job Agent drafts every answer from your profile and stories, then walks through them with you one by one."),
      h("label", {}, "Application link"), url, text, h("div", { class: "row actions" }, go, msg));
    return box;
  }
  const qs = f.questions;
  let i = Math.min(f.step || 0, qs.length - 1);
  const panel = h("div");
  const draw = () => {
    const q = qs[i];
    const answer = h("textarea", { rows: q.category === "essay" ? 8 : 2 }, q.answer);
    answer.addEventListener("change", () => { q.answer = answer.value; api(`/api/jobs/${job.id}/form`, { method: "PATCH", body: { index: i, answer: answer.value } }); });
    const instr = h("input", { placeholder: "How should it change? (optional)" });
    const rewrite = h("button", { class: "ghost small", type: "button" }, "Rewrite");
    rewrite.addEventListener("click", () => busy(rewrite, "Rewriting…", async () => {
      const u = await api(`/api/jobs/${job.id}/form/rewrite`, { method: "POST", body: { index: i, instruction: instr.value } });
      jobs = jobs.map((j) => (j.id === u.id ? u : j)); job = u; qs[i] = u.form.questions[i]; draw();
    }));
    const go = (d) => { i = Math.max(0, Math.min(qs.length - 1, i + d)); draw(); };
    const done = h("button", { class: "small", type: "button" }, q.done ? "Done ✓" : "Mark done & next");
    done.addEventListener("click", async () => {
      q.done = true; q.answer = answer.value;
      await api(`/api/jobs/${job.id}/form`, { method: "PATCH", body: { index: i, answer: answer.value, done: true } });
      go(1);
    });
    const finished = qs.filter((x) => x.done).length;
    fill(panel, 
      h("div", { class: "qmeta" }, `Question ${i + 1} of ${qs.length} · ${q.category}${q.required ? " · required" : ""} · ${finished} done`),
      h("p", {}, h("b", {}, q.question)),
      (q.options || []).length ? h("p", { class: "small muted" }, "Options: " + q.options.join(" | ")) : null,
      answer,
      q.note ? h("p", { class: "small missing-note" }, q.note) : null,
      h("div", { class: "row actions" },
        h("button", { class: "ghost small", type: "button", onclick: () => go(-1), disabled: i === 0 }, "← Back"),
        done, copyButton(() => answer.value),
        h("button", { class: "ghost small", type: "button", onclick: () => go(1), disabled: i === qs.length - 1 }, "Skip →")),
      h("div", { class: "row" }, instr, rewrite));
  };
  draw();
  box.append(h("p", { class: "muted small" }, "Open the application in another tab and copy each answer in. Answers are saved as you edit."),
    h("div", { class: "row" },
      (job.apply_url || job.url) ? h("a", { class: "button small secondary", href: job.apply_url || job.url, target: "_blank", rel: "noopener" }, "Open application ↗") : null,
      h("a", { class: "button small secondary", href: `/api/jobs/${job.id}/form.txt` }, "Download all answers (.txt)"),
      h("a", { class: "button small secondary", href: job.documents ? `/api/jobs/${job.id}/resume.pdf` : "/api/profile/resume.pdf" }, "Resume PDF")),
    panel);
  return box;
}

function jobCard(job) {
  const fit = job.fit;
  const msg = h("span", { class: "msg" });
  const run = (label, action) => async (e) => {
    setMsg(msg, "");
    await busy(e.target, label, async () => {
      try {
        replaceJob(await api(`/api/jobs/${job.id}/${action}`, { method: "POST" }));
      } catch (err) {
        setMsg(msg, err.message, "err");
      }
    });
  };
  const card = h("details", { class: "card job", open: openJobs.has(job.id) || undefined });
  card.addEventListener("toggle", () => (card.open ? openJobs.add(job.id) : openJobs.delete(job.id)));
  const where = [job.kind === "firm" ? "Firm" : "Posting", job.location, job.contact && job.contact.name].filter(Boolean).join(" · ");
  card.append(
    h("summary", { class: "job-top" },
      h("div", {}, h("div", { class: "job-title" }, job.company || "Unknown company", job.title && job.kind !== "firm" ? ` · ${job.title}` : ""),
        h("div", { class: "muted small" }, where, " · ", job.status)),
      fit ? h("span", { class: `score ${scoreClass(fit.score)}` }, `${fit.score}`) : null),
    h("div", { class: "row actions" }, msg),
    researchSection(job, run),
    job.channel === "form" ? formSection(job) : emailSection(job, run),
    h("details", { class: "fit" },
      h("summary", {}, "3. Fit, tailored resume & cover letter"),
      h("div", { class: "row actions" },
        h("button", { class: "small" + (fit ? " secondary" : ""), onclick: run("Scoring…", "analyze") }, fit ? "Re-score fit" : "Score my fit"),
        h("button", { class: "small" + (job.documents ? " secondary" : ""), onclick: run("Writing…", "documents") },
          job.documents ? "Rewrite resume + cover letter" : "Write resume + cover letter"),
        job.documents ? [
          h("a", { class: "button small secondary", href: `/api/jobs/${job.id}/resume.pdf` }, "Resume PDF"),
          h("a", { class: "button small secondary", href: `/api/jobs/${job.id}/cover-letter.pdf` }, "Cover letter PDF")] : null),
      fit ? fitDetails(fit) : null,
      job.documents && (job.documents.notes || []).length ? h("details", {}, h("summary", {}, "What the tailoring changed"), list(job.documents.notes)) : null),
    job.notes ? h("p", { class: "muted small" }, job.notes) : null);
  return card;
}

function renderJobs() {
  const el = $("#job-list");
  el.replaceChildren(...(jobs.length ? jobs.map(jobCard) : [h("p", { class: "muted" }, "No jobs yet. Use the Find tab, or add a posting above.")]));
}

loaders.jobs = async () => {
  jobs = await api("/api/jobs");
  renderJobs();
  const d = await api("/api/outreach");
  $("#od-intro").value = d.intro; $("#od-hours").value = d.hours_per_week; $("#od-start").value = d.start;
  $("#od-signature").value = d.signature; $("#od-examples").value = d.examples;
  if (!d.intro) $("#outreach-card").open = true;
};

$("#od-save").addEventListener("click", (e) => busy(e.target, "Saving…", async () => {
  try {
    await api("/api/outreach", { method: "PUT", body: { intro: $("#od-intro").value, hours_per_week: $("#od-hours").value,
      start: $("#od-start").value, signature: $("#od-signature").value, examples: $("#od-examples").value } });
    setMsg($("#od-msg"), "Saved.", "ok");
  } catch (err) {
    setMsg($("#od-msg"), err.message, "err");
  }
}));

$("#job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#job-msg");
  const body = {
    url: $("#job-url").value.trim(),
    description: $("#job-desc").value.trim(),
    title: $("#job-title").value.trim(),
    company: $("#job-company").value.trim(),
  };
  await busy($("#add-job"), "Adding…", async () => {
    try {
      setMsg(msg, body.url && !body.description ? "Reading the posting…" : "");
      const job = await api("/api/jobs", { method: "POST", body });
      e.target.reset();
      jobs.unshift(job);
      openJobs.add(job.id);
      renderJobs();
      setMsg(msg, "Added. Scoring your fit…");
      try {
        replaceJob(await api(`/api/jobs/${job.id}/analyze`, { method: "POST" }));
        setMsg(msg, "Added and scored.", "ok");
      } catch (err) {
        setMsg(msg, "Added, but couldn't score yet: " + err.message, "err");
      }
    } catch (err) {
      setMsg(msg, err.message, "err");
    }
  });
});

// ---------- Tracker ----------

let statuses = null;

function trackerRow(job) {
  const fit = job.fit;
  const c = job.contact || {};
  const dateInput = h("input", { type: "date", value: job.applied_date || "" });
  const status = h("select", {}, statuses.map((s) => h("option", { value: s, selected: s === job.status }, s)));
  status.addEventListener("change", async () => {
    const updated = await patchJob(job.id, { status: status.value });
    dateInput.value = updated.applied_date || "";
  });
  dateInput.addEventListener("change", () => patchJob(job.id, { applied_date: dateInput.value }));
  const notes = h("input", { value: job.notes || "", placeholder: "Add a note" });
  notes.addEventListener("change", () => patchJob(job.id, { notes: notes.value }));
  const del = h("button", { class: "ghost small danger", title: "Delete" }, "✕");
  del.addEventListener("click", async () => {
    if (!confirm(`Delete ${job.company || "this job"}?`)) return;
    await api(`/api/jobs/${job.id}`, { method: "DELETE" });
    jobs = jobs.filter((j) => j.id !== job.id);
    renderTracker();
  });
  const today = new Date().toISOString().slice(0, 10);
  const due = job.followup_due && !["Followed up", "Interviewing", "Offer", "Rejected", "Withdrawn"].includes(job.status) ? job.followup_due : "";
  return h("tr", {},
    h("td", {}, job.website ? h("a", { href: job.website, target: "_blank", rel: "noopener" }, job.company || "?") : job.company || "?"),
    h("td", {}, job.url && job.kind !== "firm" ? h("a", { href: job.url, target: "_blank", rel: "noopener" }, job.title || "posting") : job.title || ""),
    h("td", { class: "small" }, c.name || "", c.email ? h("div", { class: "muted" }, c.email) : null),
    h("td", {}, fit ? h("span", { class: `score ${scoreClass(fit.score)}` }, fit.score) : "–"),
    h("td", {}, status),
    h("td", {}, dateInput),
    h("td", { class: "small" + (due && due <= today ? " sev-high" : "") }, due ? (due <= today ? `due ${due}` : due) : ""),
    h("td", { class: "notes" }, notes),
    h("td", {}, del));
}

function renderTracker() {
  $("#tracker tbody").replaceChildren(...jobs.map(trackerRow));
  $("#tracker-empty").classList.toggle("hidden", jobs.length > 0);
  $("#tracker").parentElement.classList.toggle("hidden", jobs.length === 0);
}

loaders.tracker = async () => {
  if (!statuses) statuses = (await api("/api/meta")).statuses;
  jobs = await api("/api/jobs");
  renderTracker();
};

// ---------- Mentors ----------
// Today's plan from the lead mentor (left) and chats with the four coaches (right).
// Coaches suggest actions as buttons; nothing changes until the user clicks one.

const COACH_INFO = {
  lead: { name: "Lead mentor", intro: "I see everything: your profile, stories, skills, every firm you're working on and what's due. Ask me what to focus on, or tell me where you want to apply.",
    starters: ["What should I focus on this week?", "How am I doing overall?", "I want to apply to Anthropic. What should I do?"] },
  behavioral: { name: "Behavioral coach", intro: "I run mock behavioral interviews with your own stories and help you tell them well. Answer out loud with 🎤, like a real interview.",
    starters: ["Run a mock behavioral interview", "Help me with \"Tell me about yourself\"", "Let's fix my weakest story"] },
  technical: { name: "Technical coach", intro: "I teach your weak areas and give you practice problems with hints, finance or tech.",
    starters: ["Teach me my weakest area", "Give me a practice problem", "Quiz me on valuation"] },
  resume: { name: "Resume coach", intro: "I review your resume line by line and suggest stronger bullets. You choose which rewrites to apply; I never add facts you didn't give.",
    starters: ["Review my resume", "Make my experience bullets stronger", "What's missing for my target roles?"] },
};
let coach = "lead";
let coachChat = { messages: [] };
let today = null;

async function runAction(a, button) {
  const msg = $("#coach-msg");
  try {
    if (a.type === "open") location.hash = "#" + a.tab;
    else if (a.type === "job") { openJobs.add(a.job_id); location.hash = "#jobs"; }
    else if (a.type === "coach") { await switchCoach(a.coach); if (a.message) await sendCoach(a.message); }
    else if (a.type === "company_prep") {
      await busy(button, "Researching… (up to a minute)", () => api("/api/prep", { method: "POST", body: { company: a.company, role: a.role } }));
      location.hash = "#skills";
    } else if (a.type === "skills_check") {
      const current = await api("/api/skills");
      if (current.status === "answering" && !confirm("Replace your current skills check with this one?")) return;
      await busy(button, "Picking questions…", () => api("/api/skills/start", { method: "POST", body: { areas: a.areas, count: a.count } }));
      location.hash = "#skills";
    } else if (["edit_profile", "save_story", "add_profile_item", "update_targets", "update_skill", "update_job", "add_question"].includes(a.type)) {
      await busy(button, "Saving…", () => api("/api/mentor/apply", { method: "POST", body: { action: a } }));
      button.textContent = "Done ✓";
      button.disabled = true;
      loadMemory();
    }
  } catch (err) {
    setMsg(msg, err.message, "err");
  }
}

function actionButtons(actions, cls = "bubble-actions") {
  if (!(actions || []).length) return null;
  return h("div", { class: cls }, actions.map((a) => {
    const tip = a.type === "edit_profile" ? `Change to: ${a.new}` : a.type === "add_profile_item" ? JSON.stringify(a.item)
      : a.type === "update_targets" ? JSON.stringify(a.targets) : a.type === "update_job" ? `Status: ${a.status || "unchanged"}` : "";
    const b = h("button", { class: "small secondary" + (/^(edit|save|add|update)/.test(a.type) ? " update" : ""), type: "button", title: tip }, a.label);
    b.addEventListener("click", () => runAction(a, b));
    return b;
  }));
}

function renderToday() {
  const t = today;
  const p = t.plan || {};
  $("#today-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  const done = new Set(t.done);
  fill($("#today-card"), 
    p.stage ? h("span", { class: "stage" }, p.stage) : null,
    h("p", { class: "headline" }, p.headline || ""),
    p.status ? h("p", {}, p.status) : null,
    ...(p.tasks || []).map((task, i) => {
      const cb = h("input", { type: "checkbox", checked: done.has(i) });
      const row = h("div", { class: "task" + (done.has(i) ? " done" : "") }, cb,
        h("div", { class: "grow" },
          h("div", { class: "task-title" }, task.title, task.minutes ? h("span", { class: "muted small" }, ` · ${task.minutes} min`) : null),
          task.why ? h("div", { class: "muted small" }, task.why) : null,
          task.action ? actionButtons([task.action], "row actions") : null));
      cb.addEventListener("change", async () => {
        row.classList.toggle("done", cb.checked);
        await api("/api/mentor/today/task", { method: "POST", body: { index: i, done: cb.checked } });
      });
      return row;
    }),
    p.encouragement ? h("p", { class: "muted" }, p.encouragement) : null);

  const c = t.counts;
  const firstOpen = t.milestones.findIndex((m) => !m.done);
  const goal = h("input", { type: "number", min: 1, max: 50, value: t.goal_per_day, class: "inline-select", style: "width:70px" });
  goal.addEventListener("change", () => api("/api/mentor/goal", { method: "PUT", body: { goal: +goal.value } }));
  fill($("#progress-card"), 
    h("h2", {}, "Progress"),
    h("div", { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, `${c.contacted_today}/${t.goal_per_day}`), h("span", {}, "firms contacted today")),
      h("div", { class: "stat" }, h("b", {}, c.contacted_this_week), h("span", {}, "this week")),
      h("div", { class: "stat" }, h("b", { class: c.followups_due ? "sev-high" : "" }, c.followups_due), h("span", {}, "follow-ups due")),
      h("div", { class: "stat" }, h("b", {}, c.jobs_total), h("span", {}, "in your pipeline"))),
    h("ul", { class: "milestones" }, t.milestones.map((m, i) =>
      h("li", { class: m.done ? "done" : i === firstOpen ? "next" : "" }, `${m.done ? "✓" : i === firstOpen ? "→" : "○"} ${m.label}`))),
    h("div", { class: "row actions small" }, "Daily goal:", goal, "firms"));
}

function renderCoachTabs() {
  $("#coach-tabs").replaceChildren(...Object.entries(COACH_INFO).map(([id, c]) => {
    const b = h("button", { class: "coach-tab small" + (id === coach ? " on" : ""), type: "button" }, c.name);
    b.addEventListener("click", () => switchCoach(id));
    return b;
  }));
}

function renderCoachChat(pending) {
  const info = COACH_INFO[coach];
  const chat = $("#coach-chat");
  const items = [h("div", { class: "bubble assistant" }, info.intro)];
  if (!coachChat.messages.length && !pending) {
    items.push(h("div", { class: "bubble-actions starter" }, info.starters.map((s) => {
      const b = h("button", { class: "small secondary", type: "button" }, s);
      b.addEventListener("click", () => sendCoach(s));
      return b;
    })));
  }
  for (const m of coachChat.messages) {
    items.push(h("div", { class: `bubble ${m.role}` }, m.content));
    if (m.role === "assistant") items.push(actionButtons(m.actions), rememberedChips(m.remembered));
  }
  if (pending) items.push(h("div", { class: "bubble user" }, pending), h("div", { class: "bubble assistant typing" }, `${info.name} is thinking…`));
  chat.replaceChildren(...items.filter(Boolean));
  chat.scrollTop = chat.scrollHeight;
}

async function switchCoach(id) {
  coach = id;
  renderCoachTabs();
  setMsg($("#coach-msg"), "");
  coachChat = await api(`/api/mentor/chat/${id}`);
  renderCoachChat();
}

async function sendCoach(text) {
  stopVoice();
  $("#coach-send").disabled = true;
  setMsg($("#coach-msg"), "");
  renderCoachChat(text);
  try {
    coachChat = await api(`/api/mentor/chat/${coach}`, { method: "POST", body: { text } });
    renderCoachChat();
    loadMemory();
    const last = coachChat.messages[coachChat.messages.length - 1];
    if (last) speak(last.content);
    return true;
  } catch (err) {
    renderCoachChat();
    setMsg($("#coach-msg"), err.message, "err");
    return false;
  } finally {
    $("#coach-send").disabled = false;
  }
}

async function loadMemory() {
  const m = await api("/api/memory");
  $("#memory-count").textContent = `(${m.facts.length} facts)`;
  fill($("#fact-list"), m.facts.length ? m.facts.slice().reverse().map((f) => {
    const x = h("button", { class: "ghost small danger", type: "button", title: "Forget" }, "✕");
    x.addEventListener("click", async () => { await api(`/api/memory/facts/${f.id}`, { method: "DELETE" }); loadMemory(); });
    return h("div", { class: "qrow" }, h("div", {}, f.text, h("div", { class: "muted small" }, `${f.source} · ${f.at.slice(0, 10)}`)), x);
  }) : h("p", { class: "muted small" }, "Nothing yet. Facts appear here as you talk to your team."));
  fill($("#event-list"), m.events.length ? m.events.slice().reverse().slice(0, 15).map((e) =>
    h("div", { class: "small event" }, h("span", { class: "muted" }, e.at.slice(5, 16).replace("T", " ") + "  "), e.text))
    : h("p", { class: "muted small" }, "No activity yet."));
}

$("#fact-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#fact-input").value.trim();
  if (!text) return;
  await api("/api/memory/facts", { method: "POST", body: { text } });
  $("#fact-input").value = "";
  loadMemory();
});

loaders.mentors = async () => {
  renderCoachTabs();
  switchCoach(coach);
  loadMemory();
  try {
    today = await api("/api/mentor/today");
    renderToday();
  } catch (err) {
    $("#today-card").replaceChildren(h("p", { class: "msg err" }, err.message));
  }
};

$("#today-refresh").addEventListener("click", (e) => busy(e.target, "Planning…", async () => {
  try {
    today = await api("/api/mentor/today?refresh=true");
    renderToday();
  } catch (err) {
    setMsg($("#coach-msg"), err.message, "err");
  }
}));

$("#coach-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#coach-input");
  const text = input.value.trim();
  if (!text || $("#coach-send").disabled) return;
  input.value = "";
  if (!(await sendCoach(text))) input.value = text;
  input.focus();
});
enterToSend("#coach-input", "#coach-form");
attachVoice("#coach-mic", "#coach-input", "#coach-lang", "#coach-voice-msg");

$("#coach-clear").addEventListener("click", async () => {
  if (!confirm(`Clear your chat with the ${COACH_INFO[coach].name.toLowerCase()}?`)) return;
  coachChat = await api(`/api/mentor/chat/${coach}`, { method: "DELETE" });
  renderCoachChat();
});

// ---------- Start ----------

(async () => {
  try {
    await loaders.settings();
  } catch (e) {
    console.error(e);
  }
  const wanted = location.hash.slice(1);
  if (wanted) showTab(wanted);
  else location.hash = settings && settings.has_key ? "#mentors" : "#settings";
})();
