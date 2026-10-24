"use strict";
// Mentor side panel: on every page except Home (which has the full team chat), a button opens a chat with the coach that
// fits the page: technical on the Skills check, behavioral on Stories, resume on Profile, the lead mentor everywhere else.
// It's the same conversation as on Home. Other code can open it about something specific: openDock({ coach, jobId,
// questionIndex, note, label, prefill }), e.g. the application form's "Ask the lead mentor" button.
// Loaded after app.js; uses its globals (h, fill, api, busy, richText, actionButtons, rememberedChips, COACH_INFO).

const DOCK_COACH = { skills: "technical", stories: "behavioral", profile: "resume" };
const dock = { coach: "lead", picked: false, chat: null, context: null, sending: false };

function dockPage() { return (location.hash.slice(1) || "mentors").split("?")[0]; }

function dockDefaultCoach() { return DOCK_COACH[dockPage()] || "lead"; }

function updateDockButton() {
  const onHome = ["mentors", "home"].includes(dockPage());
  $("#dock-toggle").classList.toggle("hidden", onHome || !$("#dock").classList.contains("hidden"));
  if (onHome) closeDock();
  if (!dock.picked) dock.coach = dockDefaultCoach();
  $("#dock-toggle-label").textContent = COACH_INFO[dock.coach].name;
}

async function loadDockChat() {
  try { dock.chat = await api(`/api/mentor/chat/${dock.coach}`); }
  catch (err) { dock.chat = { messages: [] }; }
  renderDock();
}

function renderDock(pending) {
  const info = COACH_INFO[dock.coach];
  fill($("#dock-coaches"), Object.entries(COACH_INFO).map(([id, c]) => {
    const b = h("button", { class: "coach-tab" + (id === dock.coach ? " on" : ""), type: "button" }, c.name.replace(" coach", "").replace(" mentor", ""));
    b.addEventListener("click", () => { dock.coach = id; dock.picked = true; updateDockButton(); loadDockChat(); });
    return b;
  }));
  const ctx = dock.context;
  fill($("#dock-context"), ctx && ctx.label ? [h("span", {}, "About: ", h("b", {}, ctx.label)),
    h("button", { class: "ghost small", type: "button", title: "Ask without this", onclick: () => { dock.context = null; renderDock(); } }, "✕")] : null);
  $("#dock-context").classList.toggle("hidden", !(ctx && ctx.label));
  const items = [h("div", { class: "bubble assistant" }, info.intro)];
  for (const m of (dock.chat && dock.chat.messages) || []) {
    if (m.role === "user" && m.context) items.push(h("div", { class: "lookups", style: "align-self:flex-end" }, m.context));
    items.push(chatBubble(m));
    if (m.role === "assistant") items.push(actionButtons(m.actions), rememberedChips(m.remembered));
  }
  if (pending) items.push(h("div", { class: "bubble user" }, pending), h("div", { class: "bubble assistant typing" }, `${info.name} is thinking…`));
  const log = $("#dock-log");
  log.replaceChildren(...items.filter(Boolean));
  log.scrollTop = log.scrollHeight;
}

async function sendDock(text) {
  text = (text || "").trim();
  if (!text || dock.sending) return;
  dock.sending = true;
  $("#dock-send").disabled = true;
  $("#dock-input").value = "";
  renderDock(text);
  const ctx = dock.context || {};
  try {
    dock.chat = await api(`/api/mentor/chat/${dock.coach}`, { method: "POST", body: {
      text, page: dockPage(), job_id: ctx.jobId || "", question_index: ctx.questionIndex ?? null, note: ctx.note || "" } });
    renderDock();
  } catch (err) {
    $("#dock-input").value = text;
    renderDock();
    $("#dock-log").append(h("p", { class: "msg err small" }, err.message));
  } finally {
    dock.sending = false;
    $("#dock-send").disabled = false;
  }
}

function openDock(opts = {}) {
  if (opts.coach) { dock.coach = opts.coach; dock.picked = true; }
  if (opts.jobId || opts.note || opts.label) dock.context = { jobId: opts.jobId, questionIndex: opts.questionIndex, note: opts.note, label: opts.label };
  $("#dock").classList.remove("hidden");
  document.body.classList.add("dock-open");
  updateDockButton();
  loadDockChat();
  if (opts.prefill) $("#dock-input").value = opts.prefill;
  setTimeout(() => $("#dock-input").focus(), 50);
}

function closeDock() {
  $("#dock").classList.add("hidden");
  document.body.classList.remove("dock-open");
  $("#dock-toggle").classList.toggle("hidden", ["mentors", "home"].includes(dockPage()));
}

$("#dock-toggle").addEventListener("click", () => openDock());
$("#dock-close").addEventListener("click", closeDock);
$("#dock-form").addEventListener("submit", (e) => { e.preventDefault(); sendDock($("#dock-input").value); });
$("#dock-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendDock($("#dock-input").value); }
});
window.addEventListener("hashchange", () => { dock.picked = false; dock.context = null; updateDockButton(); if (!$("#dock").classList.contains("hidden")) loadDockChat(); });
updateDockButton();
