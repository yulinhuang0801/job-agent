"use strict";
// Home → Today's matches: postings scored against the profile each morning; the best are already prepared in My jobs.
// Loaded after app.js and pipeline.js; uses their globals (h, fill, api, busy, setMsg, openJobs, openPage).

let matches = null;
let matchesTimer = null;

async function loadMatches() {
  try {
    matches = await api("/api/matches");
  } catch (err) {
    fill($("#matches-card"), h("h2", {}, "Today's matches"), h("p", { class: "msg err" }, err.message));
    return;
  }
  renderMatches();
  clearTimeout(matchesTimer);
  if (matches.running) matchesTimer = setTimeout(loadMatches, 4000);
}

function scoreBadge(score) {
  const kind = score >= 85 ? "ok" : score >= 70 ? "warn" : "";
  return h("span", { class: "score-badge " + kind, title: "Match score (0-100)" }, String(score));
}

const PREP_LABELS = { fit: "fit check", documents: "resume + cover letter", contacts: "contact" };

function matchRow(m) {
  const open = h("button", { class: "small", type: "button" }, m.job_id ? "Open in My jobs" : "Add to My jobs");
  open.addEventListener("click", () => busy(open, "Adding…", async () => {
    let id = m.job_id;
    if (!id) {
      const r = await api(`/api/matches/${encodeURIComponent(m.id)}/add`, { method: "POST" });
      id = r.job_id;
    }
    openJobs.add(id);
    location.hash = "#jobs";
  }));
  const dismiss = h("button", { class: "ghost small", type: "button", title: "Not interested: hide it" }, "Dismiss");
  dismiss.addEventListener("click", async () => {
    matches = await api(`/api/matches/${encodeURIComponent(m.id)}/dismiss`, { method: "POST" });
    renderMatches();
  });
  const where = [m.location || (m.remote ? "Remote" : ""), m.posted ? `posted ${m.posted}` : "", m.source].filter(Boolean).join(" · ");
  return h("div", { class: "match" + (m.score >= (matches.settings.min_score || 70) ? " strong" : "") },
    scoreBadge(m.score),
    h("div", { class: "grow" },
      h("div", { class: "task-title" }, m.url ? h("a", { href: m.url, target: "_blank", rel: "noopener" }, m.title) : m.title,
        h("span", { class: "muted" }, ` · ${m.company}`)),
      where ? h("div", { class: "muted small" }, where) : null,
      m.why ? h("div", { class: "small" }, m.why) : null,
      m.concerns ? h("div", { class: "small warn-text" }, "⚠ ", m.concerns) : null,
      (m.prepared || []).length ? h("div", { class: "small ok-text" }, "✓ Prepared: ", m.prepared.map((p) => PREP_LABELS[p] || p).join(", ")) : null,
      m.prepare_error ? h("div", { class: "small muted" }, "Couldn't finish preparing: ", m.prepare_error) : null,
      h("div", { class: "row actions" }, open, m.status === "new" ? dismiss : null)));
}

function renderMatches() {
  const m = matches;
  const today = new Date().toLocaleDateString("en-CA");
  const fresh = m.items.filter((x) => x.date === m.date);
  const older = m.items.filter((x) => x.date !== m.date && x.status === "new");
  const st = m.stats || {};
  const run = h("button", { class: "ghost small", type: "button", title: "Score new postings now" }, m.running ? "Running…" : m.date === today ? "Run again" : "Find matches now");
  run.disabled = m.running;
  run.addEventListener("click", () => busy(run, "Starting…", async () => {
    try {
      matches = await api("/api/matches/run", { method: "POST", body: { full: m.date !== today } });
      renderMatches();
      clearTimeout(matchesTimer);
      matchesTimer = setTimeout(loadMatches, 3000);
    } catch (err) { setMsg(msg, err.message, "err"); }
  }));
  const settingsLink = h("a", { href: "#settings", class: "small" }, "Settings");
  settingsLink.addEventListener("click", (e) => { e.preventDefault(); openPage("settings", "daily_matches"); });
  const msg = h("span", { class: "msg" });
  const head = h("div", { class: "view-head" },
    h("div", {}, h("h2", {}, "Today's matches"),
      h("p", { class: "muted small" }, m.date
        ? `${m.date === today ? "This morning" : m.date}: ${st.scored || 0} new postings scored, ${st.strong || 0} strong fits, ${st.prepared || 0} prepared · `
        : "Not run yet. Each morning new postings are scored against your profile and the best are prepared. · ", settingsLink)),
    h("div", { class: "row" }, run, msg));
  const body = [];
  if (m.running) body.push(h("p", { class: "muted small" }, "⏳ ", m.progress || "Working…"));
  if (!m.settings.on) body.push(h("p", { class: "muted small" }, "Daily matches are off. Turn them on in Settings → Daily matches."));
  if (fresh.length) body.push(...fresh.map(matchRow));
  else if (m.date && !m.running) body.push(h("p", { class: "muted" }, "No new matches today. ",
    st.relaxed_remote ? "There were few remote roles, so on-site ones were included. " : "",
    "Widen your target roles in Profile, or add job boards in Find → Data sources."));
  if (older.length) {
    body.push(h("details", { class: "small" }, h("summary", {}, `${older.length} earlier matches you haven't looked at`), older.map(matchRow)));
  }
  if (st.level) body.push(h("p", { class: "muted small" }, `Looking for ${st.level}`, (st.functions || []).length ? ` in ${st.functions.join(", ")}` : "",
    st.remote_only ? ", remote" : "", st.international ? "; citizens-only roles are skipped" : "", `. ${(m.boards || {}).boards || 0} company job boards tracked.`));
  fill($("#matches-card"), head, body);
}
