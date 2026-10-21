// The later recruiting stages: next steps on each job (deadline, assessments, interviews,
// thank-you notes, offers), the Network page and the Calendar page. Uses helpers from app.js
// (h, api, fill, busy, setMsg, jobs, replaceJob, openJobs, actionButtons, copyButton).

let calMeta = null; // event types and assessment platforms, from /api/calendar

async function loadCalMeta() {
  if (!calMeta) calMeta = await api("/api/calendar?days=7");
  return calMeta;
}

const todayISO = () => new Date().toLocaleDateString("en-CA"); // local date, YYYY-MM-DD
const niceDate = (d) => (d ? new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) : "");

// Open a drafted message in the user's own mailbox; nothing is sent from here.
function composeRow(to, getSubject, getBody) {
  const link = (kind, label) => {
    const a = h("a", { class: "button small secondary", target: "_blank", rel: "noopener", href: "#" }, label);
    a.addEventListener("click", () => {
      const t = encodeURIComponent(typeof to === "function" ? to() : to || ""), su = encodeURIComponent(getSubject()), bo = encodeURIComponent(getBody());
      a.href = kind === "gmail" ? `https://mail.google.com/mail/?view=cm&fs=1&to=${t}&su=${su}&body=${bo}`
        : kind === "outlook" ? `https://outlook.office.com/mail/deeplink/compose?to=${t}&subject=${su}&body=${bo}`
        : `mailto:${t}?subject=${su}&body=${bo}`;
    });
    return a;
  };
  return h("div", { class: "row actions" }, link("gmail", "Open in Gmail"), link("outlook", "Open in Outlook"), link("mailto", "Mail app"),
    copyButton(getBody, "Copy"));
}

function draftBox(d, to) {
  if (!d) return null;
  const subject = h("input", { value: d.subject || "" });
  const body = h("textarea", { rows: 9 }, d.body || "");
  return h("div", { class: "draft" },
    d.subject !== undefined ? [h("label", {}, "Subject"), subject] : null,
    h("label", {}, "Message"), body,
    composeRow(to, () => subject.value, () => body.value),
    d.linkedin_note ? h("details", {}, h("summary", {}, `LinkedIn note (${d.linkedin_note.length}/300)`), h("p", {}, d.linkedin_note),
      copyButton(() => d.linkedin_note, "Copy note")) : null,
    h("p", { class: "muted small" }, "A draft: edit it, then send it yourself."));
}

// ---------- Work authorization ----------

function authBadge(job) {
  const w = job.fit && job.fit.work_authorization;
  if (!w || !w.requirement || w.requirement === "none stated") return null;
  const bad = w.ok_for_candidate === false;
  return h("span", { class: `badge ${bad ? "warn" : w.ok_for_candidate ? "ok" : ""}`, title: [w.evidence, w.note].filter(Boolean).join(" · ") },
    (bad ? "⚠ " : "") + w.requirement);
}

// ---------- Events on a job ----------

function eventForm(job, after) {
  const types = (calMeta && calMeta.event_types) || { oa: "Online assessment", interview: "Interview", other: "Other" };
  const plats = (calMeta && calMeta.oa_platforms) || {};
  const type = h("select", {}, Object.entries(types).filter(([k]) => k !== "deadline").map(([k, v]) => h("option", { value: k }, v)));
  const platform = h("select", { class: "hidden" }, Object.entries(plats).map(([k, v]) => h("option", { value: k }, v)));
  const title = h("input", { placeholder: "e.g. Superday, Round 1, HireVue" });
  const date = h("input", { type: "date", value: todayISO() });
  const time = h("input", { type: "time" });
  const people = h("input", { placeholder: "Interviewers or hosts (optional)" });
  const msg = h("span", { class: "msg" });
  type.addEventListener("change", () => platform.classList.toggle("hidden", type.value !== "oa"));
  const add = h("button", { class: "small", type: "button" }, "Add");
  add.addEventListener("click", () => busy(add, "Adding…", async () => {
    try {
      after(await api(`/api/jobs/${job.id}/events`, { method: "POST", body: { type: type.value, platform: type.value === "oa" ? platform.value : "",
        title: title.value, date: date.value, time: time.value, people: people.value } }));
    } catch (err) { setMsg(msg, err.message, "err"); }
  }));
  return h("div", { class: "event-form" },
    h("div", { class: "row wrap" }, type, platform, title),
    h("div", { class: "row wrap" }, date, time, people, add, msg));
}

function prepPanel(job, ev, after) {
  const p = ev.prep;
  if (!p) return null;
  const video = ev.type === "oa" && ev.platform === "hirevue";
  return h("div", { class: "prep" },
    p.summary ? h("p", {}, p.summary) : null,
    (p.tips || []).length ? h("details", {}, h("summary", {}, "How this assessment works"), h("ul", { class: "tight" }, p.tips.map((t) => h("li", {}, t)))) : null,
    (p.steps || []).length ? h("ol", { class: "tight" }, p.steps.map((s) => h("li", {}, h("b", {}, s.title), s.minutes ? h("span", { class: "muted small" }, ` · ${s.minutes} min`) : null,
      s.why ? h("div", { class: "muted small" }, s.why) : null, s.action ? actionButtons([s.action], "row") : null))) : null,
    (p.practice_questions || []).length ? [h("b", {}, "Likely questions"),
      h("ul", { class: "tight" }, p.practice_questions.map((q) => h("li", {}, q.question, q.why ? h("span", { class: "muted small" }, ` (${q.why})`) : null)))] : null,
    (p.practice_questions || []).some((q) => q.kind === "behavioral") || video
      ? timedPractice(p.practice_questions.filter((q) => q.kind !== "ask").map((q) => q.question), video) : null);
}

// HireVue-style practice: preparation countdown, timed answer (typed or spoken), then a rubric grade.
function timedPractice(questions, video) {
  if (!questions.length) return null;
  const box = h("div", { class: "practice" });
  let i = 0, timer = null;
  const start = h("button", { class: "secondary small", type: "button" }, video ? "▶ Timed practice (30 s to prepare, 2 min to answer)" : "▶ Timed practice");
  const draw = () => {
    clearInterval(timer);
    const q = questions[i % questions.length];
    const clock = h("span", { class: "clock" }, "0:30");
    const phase = h("span", { class: "muted small" }, "Preparing…");
    const answer = h("textarea", { rows: 6, placeholder: "Answer as you would out loud (or press 🎤 in the coach chat and paste it here)", disabled: true });
    const grade = h("button", { class: "small", type: "button" }, "Grade my answer");
    const next = h("button", { class: "ghost small", type: "button" }, "Next question");
    const result = h("div");
    let left = 30, answering = false;
    timer = setInterval(() => {
      left -= 1;
      if (left <= 0 && !answering) { answering = true; left = 120; phase.textContent = "Answer now"; answer.disabled = false; answer.focus(); }
      else if (left <= 0 && answering) { clearInterval(timer); phase.textContent = "Time's up"; }
      clock.textContent = `${Math.floor(Math.max(0, left) / 60)}:${String(Math.max(0, left) % 60).padStart(2, "0")}`;
    }, 1000);
    grade.addEventListener("click", () => busy(grade, "Grading…", async () => {
      clearInterval(timer);
      try {
        const g = await api("/api/practice/grade", { method: "POST", body: { question: q, answer: answer.value } });
        fill(result, h("div", { class: "rubric" }, h("div", { class: "row" }, h("b", {}, "Grade"), h("span", { class: "quality" }, "★".repeat(g.stars) + "☆".repeat(5 - g.stars))),
          h("div", { class: "rubric-rows" }, g.dims.map((d) => h("div", { class: "rubric-row" }, h("div", { class: "rubric-label" }, d.label), dots(d.score), h("div", { class: "rubric-why" }, d.why)))),
          g.top_fix ? h("p", { class: "top-fix" }, h("b", {}, "Biggest improvement: "), g.top_fix) : null,
          h("p", { class: "muted small" }, `${g.words} words. Spoken, about ${Math.round(g.words / 2.5)} seconds.`)));
      } catch (err) { fill(result, h("p", { class: "msg err" }, err.message)); }
    }));
    next.addEventListener("click", () => { i += 1; draw(); });
    fill(box, h("div", { class: "practice-q" }, h("b", {}, q)), h("div", { class: "row" }, phase, clock), answer, h("div", { class: "row actions" }, grade, next), result);
  };
  start.addEventListener("click", draw);
  return [start, box];
}

function eventRow(job, ev, after) {
  const types = (calMeta && calMeta.event_types) || {};
  const plats = (calMeta && calMeta.oa_platforms) || {};
  const label = ev.type === "oa" && ev.platform ? plats[ev.platform] || "Online assessment" : types[ev.type] || ev.type;
  const past = ev.date <= todayISO();
  const done = h("input", { type: "checkbox", checked: ev.done, title: "Done" });
  done.addEventListener("change", async () => after(await api(`/api/jobs/${job.id}/events/${ev.id}`, { method: "PATCH", body: { done: done.checked } })));
  const prep = ["oa", "interview", "coffee_chat", "info_session", "career_fair"].includes(ev.type)
    ? h("button", { class: "ghost small", type: "button" }, ev.prep ? "Redo prep" : "Prepare") : null;
  if (prep) prep.addEventListener("click", () => busy(prep, "Preparing… (up to a minute)", async () => {
    try { after(await api(`/api/jobs/${job.id}/events/${ev.id}/prep`, { method: "POST" })); } catch (err) { alert(err.message); }
  }));
  const del = h("button", { class: "ghost small danger", type: "button", title: "Delete" }, "✕");
  del.addEventListener("click", async () => { if (confirm("Delete this event?")) after(await api(`/api/jobs/${job.id}/events/${ev.id}`, { method: "DELETE" })); });
  let thanks = null;
  if (ev.type === "interview" && past) {
    const notes = h("textarea", { rows: 2, placeholder: "What you talked about (a project, a question you liked, something about the team). Only what really came up." }, ev.notes || "");
    const people = h("input", { value: ev.people || "", placeholder: "Interviewer names" });
    const write = h("button", { class: "small" + (ev.thank_you ? " secondary" : ""), type: "button" }, ev.thank_you ? "Rewrite thank-you" : "Write thank-you note");
    write.addEventListener("click", () => busy(write, "Writing…", async () => {
      try { after(await api(`/api/jobs/${job.id}/events/${ev.id}/thanks`, { method: "POST", body: { notes: notes.value, people: people.value } })); } catch (err) { alert(err.message); }
    }));
    const sent = ev.thank_you ? h("button", { class: "ghost small", type: "button" }, ev.thank_you.sent ? "Sent ✓" : "Mark as sent") : null;
    if (sent) sent.addEventListener("click", async () => after(await api(`/api/jobs/${job.id}/events/${ev.id}`, { method: "PATCH", body: { thanks_sent: !ev.thank_you.sent } })));
    thanks = h("details", { class: "thanks", open: !ev.thank_you || undefined },
      h("summary", {}, ev.thank_you && ev.thank_you.sent ? "Thank-you note · sent ✓" : "Thank-you note (send within a day)"),
      people, notes, h("div", { class: "row actions" }, write, sent), ev.thank_you ? draftBox(ev.thank_you, "") : null);
  }
  return h("div", { class: `event${ev.done ? " done" : ""}` },
    h("div", { class: "row" }, done, h("b", {}, niceDate(ev.date)), ev.time ? h("span", { class: "muted small" }, ev.time) : null,
      h("span", { class: "badge" }, label), ev.title ? h("span", {}, ev.title) : null, ev.people ? h("span", { class: "muted small" }, ev.people) : null,
      h("span", { class: "spacer" }), prep, del),
    prepPanel(job, ev, after), thanks);
}

// ---------- Offers ----------

function offerSection(job, after) {
  const o = job.offer || {};
  const show = job.offer || ["Offer", "Accepted"].includes(job.status);
  const box = h("details", { class: "fit", open: (show && !o.pay) || undefined });
  const field = (k, label, attrs = {}) => {
    const el = h("input", { value: o[k] || "", ...attrs });
    el.dataset.k = k;
    return h("label", { class: "field" }, h("span", { class: "muted small" }, label), el);
  };
  const unit = h("select", {}, ["hour", "week", "month", "year", "total"].map((u) => h("option", { value: u, selected: (o.pay_unit || "hour") === u }, `per ${u}`.replace("per total", "total"))));
  unit.dataset.k = "pay_unit";
  const form = h("div", { class: "offer-grid" },
    field("pay", "Pay", { placeholder: "e.g. 35" }), h("label", { class: "field" }, h("span", { class: "muted small" }, "Unit"), unit),
    field("bonus", "Bonus / stipend"), field("location", "Location"),
    field("start", "Start", { type: "date" }), field("end", "End", { type: "date" }),
    field("deadline", "Decide by", { type: "date" }), field("housing", "Housing / relocation"),
    field("return_offer", "Return-offer chances"), field("notes", "Notes"));
  const msg = h("span", { class: "msg" });
  const save = h("button", { class: "small", type: "button" }, job.offer ? "Save offer" : "Record this offer");
  save.addEventListener("click", () => busy(save, "Saving…", async () => {
    const body = {};
    form.querySelectorAll("[data-k]").forEach((el) => (body[el.dataset.k] = el.value));
    try { after(await api(`/api/jobs/${job.id}/offer`, { method: "PUT", body })); } catch (err) { setMsg(msg, err.message, "err"); }
  }));
  const notes = h("input", { placeholder: "Anything the message should say (e.g. 'waiting to hear from Lazard by Nov 20')" });
  const drafts = job.offer ? h("div", { class: "row actions wrap" }, [["extension", "Ask for more time"], ["negotiate", "Negotiate"], ["accept", "Accept"], ["decline", "Decline"]].map(([k, label]) => {
    const b = h("button", { class: "secondary small", type: "button" }, label);
    b.addEventListener("click", () => busy(b, "Writing…", async () => {
      try { after(await api(`/api/jobs/${job.id}/offer/draft`, { method: "POST", body: { kind: k, notes: notes.value } })); } catch (err) { setMsg(msg, err.message, "err"); }
    }));
    return b;
  })) : null;
  box.append(h("summary", {}, job.offer ? `Offer${o.pay ? ` · ${o.pay} per ${o.pay_unit || "hour"}` : ""}${o.deadline ? ` · decide by ${o.deadline}` : ""}` : "Got an offer? Record it"),
    form, h("div", { class: "row actions" }, save, msg),
    job.offer ? [h("label", {}, "Write a reply to the recruiter"), notes, drafts,
      o.draft ? draftBox(o.draft, (job.contact || {}).email || "") : null,
      h("p", { class: "muted small" }, "Asking for a few more days is normal and rarely hurts. Negotiating an internship offer is less common; it's reasonable with a competing offer or real costs (like relocation)."),
      ] : null);
  return box;
}

// The "Next steps" part of a job card.
function pipelineSection(job) {
  const after = (updated) => { openJobs.add(updated.id); replaceJob(updated); refreshOverview(); };
  const deadline = h("input", { type: "date", value: job.deadline || "" });
  deadline.addEventListener("change", async () => after(await api(`/api/jobs/${job.id}`, { method: "PATCH", body: { deadline: deadline.value } })));
  const events = (job.events || []).slice().sort((a, b) => a.date.localeCompare(b.date));
  const open = events.some((e) => !e.done) || ["Online assessment", "Interviewing", "Offer"].includes(job.status);
  return h("details", { class: "fit", open: open || undefined },
    h("summary", {}, "4. Next steps: deadline, assessments, interviews, offer" + (events.length ? ` · ${events.length} event${events.length === 1 ? "" : "s"}` : "")),
    h("div", { class: "row wrap" }, h("span", { class: "muted small" }, "Application deadline"), deadline, authBadge(job),
      job.fit && job.fit.work_authorization && job.fit.work_authorization.note ? h("span", { class: "muted small" }, job.fit.work_authorization.note) : null),
    events.map((ev) => eventRow(job, ev, after)),
    h("details", {}, h("summary", {}, "Add an assessment, interview or other date"), eventForm(job, after)),
    offerSection(job, after));
}

// ---------- Network page ----------

let network = null;

function linkedinSearch(company, school) {
  return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent([company, school].filter(Boolean).join(" "))}`;
}
function googleSearch(company, school) {
  return `https://www.google.com/search?q=${encodeURIComponent(`site:linkedin.com/in "${company}"${school ? ` "${school}"` : ""}`)}`;
}

function personCard(p) {
  const after = (data) => { network = data; renderNetwork(); refreshOverview(); };
  const status = h("select", {}, network.statuses.map((s) => h("option", { value: s, selected: s === p.status }, s)));
  status.addEventListener("change", async () => after(await api(`/api/network/${p.id}`, { method: "PATCH", body: { status: status.value } })));
  const follow = h("input", { type: "date", value: p.next_followup || "", title: "Next follow-up" });
  follow.addEventListener("change", async () => after(await api(`/api/network/${p.id}`, { method: "PATCH", body: { next_followup: follow.value } })));
  const notes = h("input", { placeholder: "Notes for the message (optional)" });
  const kinds = [["coffee_chat", "Ask for a chat"], ["follow_up", "Follow up"], ["chat_thanks", "Thank after chat"], ["referral", "Ask for a referral"]];
  const msg = h("span", { class: "msg" });
  const writeBtns = kinds.map(([k, label]) => {
    const b = h("button", { class: "secondary small", type: "button" }, label);
    b.addEventListener("click", () => busy(b, "Writing…", async () => {
      try { after(await api(`/api/network/${p.id}/draft`, { method: "POST", body: { kind: k, notes: notes.value } })); } catch (err) { setMsg(msg, err.message, "err"); }
    }));
    return b;
  });
  const prep = h("button", { class: "ghost small", type: "button" }, p.prep ? "Redo chat prep" : "Prepare for a chat");
  prep.addEventListener("click", () => busy(prep, "Preparing…", async () => {
    try { after(await api(`/api/network/${p.id}/prep`, { method: "POST" })); } catch (err) { setMsg(msg, err.message, "err"); }
  }));
  const chatDate = h("input", { type: "date", value: todayISO() });
  const chatNotes = h("textarea", { rows: 2, placeholder: "What you talked about and any advice (used for the thank-you)" });
  const logChat = h("button", { class: "small", type: "button" }, "Save chat");
  logChat.addEventListener("click", () => busy(logChat, "Saving…", async () => after(await api(`/api/network/${p.id}/chat`, { method: "POST", body: { date: chatDate.value, notes: chatNotes.value } }))));
  const del = h("button", { class: "ghost small danger", type: "button", title: "Remove" }, "✕");
  del.addEventListener("click", async () => { if (confirm(`Remove ${p.name}?`)) after(await api(`/api/network/${p.id}`, { method: "DELETE" })); });
  const job = p.job_id ? jobs.find((j) => j.id === p.job_id) : null;
  const due = p.next_followup && p.next_followup <= todayISO();
  return h("details", { class: "card person" },
    h("summary", { class: "job-top" },
      h("div", {}, h("div", { class: "job-title" }, p.name, p.title || p.company ? h("span", { class: "muted" }, ` · ${[p.title, p.company].filter(Boolean).join(", ")}`) : null),
        h("div", { class: "muted small" }, [p.how, p.status, p.last_contact ? `last contact ${p.last_contact}` : "", job ? `for ${job.company}` : ""].filter(Boolean).join(" · "))),
      due ? h("span", { class: "badge warn" }, `due ${p.next_followup}`) : null),
    h("div", { class: "row wrap" }, h("span", { class: "muted small" }, "Status"), status, h("span", { class: "muted small" }, "Next follow-up"), follow,
      p.linkedin ? h("a", { href: p.linkedin, target: "_blank", rel: "noopener", class: "small" }, "LinkedIn ↗") : null,
      p.email ? h("span", { class: "small" }, p.email) : null, h("span", { class: "spacer" }), del),
    p.notes ? h("p", { class: "muted small" }, p.notes) : null,
    h("label", {}, "Write a message"), notes, h("div", { class: "row actions wrap" }, writeBtns, prep, msg),
    p.draft ? draftBox(p.draft, p.email || "") : null,
    p.prep ? h("div", { class: "prep" }, h("b", {}, "How to open"), h("p", {}, p.prep.intro),
      h("b", {}, "Questions to ask"), h("ol", { class: "tight" }, p.prep.questions.map((q) => h("li", {}, q))),
      (p.prep.avoid || []).length ? [h("b", {}, "Avoid"), h("ul", { class: "tight" }, p.prep.avoid.map((q) => h("li", {}, q)))] : null) : null,
    h("details", {}, h("summary", {}, `Log a chat${(p.chats || []).length ? ` (${p.chats.length} so far)` : ""}`),
      (p.chats || []).map((c) => h("p", { class: "small" }, h("b", {}, c.date), " ", c.notes)),
      h("div", { class: "row" }, chatDate, logChat), chatNotes));
}

function renderNetwork() {
  const people = network.people;
  const due = people.filter((p) => p.next_followup && p.next_followup <= todayISO()).length;
  $("#net-summary").textContent = people.length
    ? network.statuses.map((s) => [s, people.filter((p) => p.status === s).length]).filter(([, n]) => n).map(([s, n]) => `${n} ${s.toLowerCase()}`).join(" · ")
      + (due ? ` · ${due} follow-up${due === 1 ? "" : "s"} due` : "")
    : "";
  $("#net-how").replaceChildren(...network.how.map((x) => h("option", { value: x }, x)));
  $("#net-job").replaceChildren(h("option", { value: "" }, "Not linked to a job"), ...jobs.map((j) => h("option", { value: j.id }, `${j.company}${j.title ? " · " + j.title : ""}`)));
  if (!$("#net-school").value) $("#net-school").value = network.school || "";
  fill($("#net-list"), people.length ? people.map(personCard) : h("p", { class: "muted" }, "No one yet. Find alumni at firms you like, add them here, and ask for 15-minute chats."));
}

loaders.network = async () => {
  if (!jobs.length) jobs = await api("/api/jobs");
  network = await api("/api/network");
  renderNetwork();
};

$("#net-find").addEventListener("click", () => busy($("#net-find"), "Looking…", async () => {
  const company = $("#net-company-q").value.trim(), school = $("#net-school").value.trim();
  if (!company) return;
  const box = $("#net-found");
  const links = h("div", { class: "row actions wrap" },
    h("a", { class: "button small secondary", target: "_blank", rel: "noopener", href: linkedinSearch(company, school) }, "Search LinkedIn ↗"),
    h("a", { class: "button small secondary", target: "_blank", rel: "noopener", href: googleSearch(company, school) }, "Search Google for profiles ↗"));
  let fromDb = [];
  try {
    const r = await api(`/api/db/firms?q=${encodeURIComponent(company)}&has_people=1`);
    fromDb = r.rows.slice(0, 3).flatMap((f) => (f.people || []).map((p) => ({ ...p, company: f.name })));
  } catch (e) { /* database not downloaded */ }
  fill(box, links,
    h("p", { class: "muted small" }, "LinkedIn and Google open in your browser; Job Agent never logs into LinkedIn. Add the people you find below."),
    fromDb.length ? [h("b", {}, "People named in SEC filings"), fromDb.slice(0, 12).map((p) => {
      const add = h("button", { class: "ghost small", type: "button" }, "+ Add");
      add.addEventListener("click", async () => {
        network = await api("/api/network", { method: "POST", body: { name: p.name, title: p.title, company: p.company, how: "Cold" } });
        renderNetwork(); add.textContent = "Added ✓"; add.disabled = true;
      });
      return h("div", { class: "qrow" }, h("div", {}, p.name, h("div", { class: "muted small" }, `${p.title || ""} · ${p.company}`)), add);
    })] : null);
}));

$("#net-add").addEventListener("click", () => busy($("#net-add"), "Adding…", async () => {
  const body = { name: $("#net-name").value, title: $("#net-title").value, company: $("#net-company").value, how: $("#net-how").value,
    school: $("#net-school").value, linkedin: $("#net-linkedin").value, email: $("#net-email").value, job_id: $("#net-job").value, notes: $("#net-notes").value };
  try {
    network = await api("/api/network", { method: "POST", body });
    ["#net-name", "#net-title", "#net-company", "#net-linkedin", "#net-email", "#net-notes"].forEach((s) => ($(s).value = ""));
    setMsg($("#net-msg"), "Added.", "ok");
    renderNetwork();
  } catch (err) { setMsg($("#net-msg"), err.message, "err"); }
}));

// ---------- Calendar page ----------

function gcalLink(it) {
  const d = it.date.replace(/-/g, "");
  let dates;
  if (it.time) {
    const t = it.time.replace(":", "") + "00";
    const end = String(Math.min(23, +it.time.slice(0, 2) + 1)).padStart(2, "0") + it.time.slice(3, 5) + "00";
    dates = `${d}T${t}/${d}T${end}`;
  } else {
    const next = new Date(it.date + "T12:00:00"); next.setDate(next.getDate() + 1);
    dates = `${d}/${next.toISOString().slice(0, 10).replace(/-/g, "")}`;
  }
  return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(`${it.kind}: ${it.title}`)}&dates=${dates}&details=${encodeURIComponent(it.company ? `Company: ${it.company} (from Job Agent)` : "From Job Agent")}`;
}

function calItem(it) {
  const open = h("button", { class: "ghost small", type: "button" }, "Open");
  open.addEventListener("click", () => {
    if (it.job_id) { openJobs.add(it.job_id); location.hash = "#jobs"; } else location.hash = "#network";
  });
  return h("div", { class: `cal-item${it.overdue ? " overdue" : ""}` },
    h("div", { class: "cal-date" }, niceDate(it.date), it.time ? h("div", { class: "muted small" }, it.time) : null),
    h("div", { class: "cal-what" }, h("span", { class: "badge" }, it.kind), " ", it.title,
      it.company && !it.title.includes(it.company) ? h("span", { class: "muted small" }, ` · ${it.company}`) : null),
    h("div", { class: "row" }, h("a", { class: "button secondary small", href: gcalLink(it), target: "_blank", rel: "noopener", title: "Add to Google Calendar" }, "+ Google Calendar"), open));
}

function renderTimeline(t) {
  const yours = t.fields.filter((f) => f.yours);
  const row = (f) => h("div", { class: "tl-field" }, h("b", {}, f.label),
    f.windows.length ? h("ul", { class: "tight" }, f.windows.map((w) => h("li", {},
      w.open_now ? h("span", { class: "badge ok" }, "open now") : h("span", { class: "badge" }, w.starts_in_months ? `in ${w.starts_in_months} mo` : "soon"), " ",
      `${w.from} – ${w.to}: ${w.what}`, w.summer_kind ? h("span", { class: "muted small" }, ` (summer ${w.summer}, ${w.summer_kind})`) : h("span", { class: "muted small" }, ` (summer ${w.summer})`))))
      : h("p", { class: "muted small" }, "Nothing coming up in the next three summers."),
    h("p", { class: "muted small" }, f.note));
  fill($("#timeline"),
    h("p", { class: "muted small" }, t.grad_year ? `Based on graduating in ${t.grad_year}: ` + t.summers.map((s) => `summer ${s.year}${s.kind ? ", " + s.kind : ""}`).join("; ") + "."
      : "Add your graduation year to your profile's education to see which summer is which."),
    t.summers.filter((s) => s.advice).map((s) => h("p", { class: "small" }, h("b", {}, `Summer ${s.year}: `), s.advice)),
    yours.length ? yours.map(row) : h("p", { class: "muted small" }, "Your target roles don't match a field below yet; set them in your profile."),
    h("details", {}, h("summary", {}, "Other fields"), t.fields.filter((f) => !f.yours).map(row)),
    h("p", { class: "muted small" }, t.caveat));
}

function calQuickAdd() {
  const job = h("select", {}, jobs.map((j) => h("option", { value: j.id }, `${j.company}${j.title ? " · " + j.title : ""}`)));
  if (!jobs.length) return h("p", { class: "muted small" }, "Add jobs first (Find or My jobs), then add their dates here.");
  const wrap = h("div");
  const draw = () => {
    const j = jobs.find((x) => x.id === job.value) || jobs[0];
    fill(wrap, eventForm(j, () => loaders.calendar()));
  };
  job.addEventListener("change", draw);
  draw();
  return [h("div", { class: "row" }, h("span", { class: "muted small" }, "Job"), job), wrap];
}

loaders.calendar = async () => {
  jobs = await api("/api/jobs");
  const cal = await api("/api/calendar");
  calMeta = cal;
  renderTimeline(cal.timeline);
  const items = cal.items;
  const in7 = new Date(); in7.setDate(in7.getDate() + 7);
  const week = in7.toLocaleDateString("en-CA");
  const groups = [["Overdue", items.filter((i) => i.overdue)], ["Next 7 days", items.filter((i) => !i.overdue && i.date <= week)],
    ["Later", items.filter((i) => i.date > week)]].filter(([, list]) => list.length);
  fill($("#cal-list"), groups.length ? groups.map(([name, list]) => [h("h3", { class: "group-title" }, name), list.map(calItem)])
    : h("p", { class: "muted" }, "Nothing scheduled. Add deadlines, assessments and interviews to your jobs, or follow-up dates to your network."));
  fill($("#cal-add"), calQuickAdd());
};

// Load event types for job cards as soon as the page opens.
loadCalMeta().catch(() => {});

// ---------- Guide: where the knowledge comes from ----------

loaders.guide = async () => {
  const d = await api("/api/official");
  fill($("#sources-list"),
    h("b", {}, "Frameworks"),
    h("ul", { class: "tight" }, d.frameworks.map((f) => h("li", {}, h("a", { href: f.url, target: "_blank", rel: "noopener" }, f.name), ` · ${f.use}`))),
    h("b", {}, `Employers with official interview pages (${d.employers.length})`),
    h("ul", { class: "tight" }, d.employers.map((e) => h("li", {}, h("b", {}, e.name), ": ",
      e.pages.map((p, i) => [i ? ", " : "", h("a", { href: p.url, target: "_blank", rel: "noopener" }, p.about)])))),
    h("p", { class: "muted small" }, "For other companies, company prep looks for interview pages on the company's own website only."),
    h("p", { class: "muted small" }, d.onet_attribution));
};
